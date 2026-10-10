"""Step 3 — 混音：loudness-align each stem -> per-track FX -> bus -> out/final.wav

  build/stems/<part>.wav --(loudness.py: per-track target)--> --(fx.py chain)-->
  build/stems_fx/<part>.wav --sum--> master chain --> master LUFS --> limiter
  --> out/final.wav (+ out/final.mp3 if ffmpeg is installed)

Optional:  --voice narration.wav  ducks the bed under the voice (sidechain-ish)
Run:       python -m src.mix.mix [--mode lufs|dbfs] [--voice path.wav]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from src import config as C
from src.mix import fx, loudness


def load_stem(part: str) -> np.ndarray | None:
    p = C.STEMS_DIR / f"{part}.wav"
    if not p.exists():
        return None
    a, sr = sf.read(p, always_2d=True, dtype="float32")
    assert sr == C.SAMPLE_RATE, f"{p} is {sr} Hz"
    return a.T


def duck_under_voice(bed: np.ndarray, voice_path: str, sr: int, depth_db: float = -7.0) -> np.ndarray:
    from scipy.signal import lfilter, resample_poly
    v, vsr = sf.read(voice_path, always_2d=True, dtype="float32")
    v = v.mean(axis=1)
    if vsr != sr:
        v = resample_poly(v, sr, vsr).astype(np.float32)
    v = np.pad(v, (0, max(0, bed.shape[1] - len(v))))[: bed.shape[1]]
    # envelope follower: fast attack (~30 ms) / slow release (~400 ms)
    rect = np.abs(v)
    a_att, a_rel = np.exp(-1 / (0.03 * sr)), np.exp(-1 / (0.4 * sr))
    env = lfilter([1 - a_rel], [1, -a_rel], lfilter([1 - a_att], [1, -a_att], rect))
    env = env / (env.max() or 1.0)
    gain = 10 ** ((depth_db * np.clip(env * 4, 0, 1)) / 20)
    return bed * gain[None, :]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["lufs", "dbfs"], default="lufs")
    ap.add_argument("--voice", help="optional narration WAV to duck under")
    ap.add_argument("--out", default=str(C.OUT_DIR / "final.wav"), help="output WAV path")
    a = ap.parse_args(argv)
    sr = C.SAMPLE_RATE
    n = int(round(C.total_seconds() * sr))
    bus = np.zeros((2, n), dtype=np.float32)
    fx_dir = C.BUILD_DIR / "stems_fx"
    fx_dir.mkdir(parents=True, exist_ok=True)
    report = {}

    for part in C.PARTS:
        stem = load_stem(part)
        if stem is None:
            print(f"[mix] {part}: no stem, skipped")
            continue
        target = loudness.target_for(part)
        aligned, measured, gain = loudness.align(stem, sr, target, a.mode)
        pan = C.PARTS[part].get('pan', 0)
        aligned *= np.sqrt([[1 - pan], [1 + pan]])
        processed = fx.chain(part)(aligned, sr)
        processed = processed[:, :n]
        sf.write(fx_dir / f"{part}.wav", processed.T, sr, subtype="FLOAT")
        bus[:, : processed.shape[1]] += processed
        engine = (C.STEMS_DIR / f"{part}.engine").read_text() if (C.STEMS_DIR / f"{part}.engine").exists() else "?"
        report[part] = {"engine": engine, "measured": round(measured, 2), "target": target, "gain_db": round(gain, 2)}
        print(f"[mix] {part:7s} [{engine:16s}] {measured:6.1f} -> {target:6.1f} {a.mode.upper()} "
              f"({gain:+5.1f} dB) -> fx")

    master = fx.master_chain()(bus, sr)
    # gentle fades: 30 ms in, 2.5 s out over the tail
    fi, fo = min(n, int(0.03 * sr)), min(n, int(min(2.5, C.TAIL_SECONDS) * sr))
    if fi:
        master[:, :fi] *= np.linspace(0, 1, fi)[None, :]
    if fo:
        master[:, -fo:] *= np.linspace(1, 0, fo)[None, :] ** 1.5
    master, m_meas, m_gain = loudness.align(master, sr, C.MASTER_TARGET_LUFS)
    if a.voice:  # duck AFTER loudness alignment so the dips are kept (bed = -18 LUFS without voice)
        master = duck_under_voice(master, a.voice, sr)
    master = fx.limiter(master, sr)
    final_lufs = loudness.measure_lufs(master, sr)
    peak_db = 20 * np.log10(np.max(np.abs(master)) + 1e-12)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, master.T, sr, subtype="PCM_24")
    report["_master"] = {"lufs": round(final_lufs, 2), "peak_dbfs": round(float(peak_db), 2),
                         "duration_s": round(master.shape[1] / sr, 2)}
    out.with_suffix(".report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"[mix] master: {final_lufs:.1f} LUFS, peak {peak_db:.1f} dBFS, "
          f"{master.shape[1] / sr:.1f}s -> {out}")
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-b:a", "256k",
                        str(out.with_suffix(".mp3"))], check=False)
        print(f"[mix] mp3 -> {out.with_suffix('.mp3')}")


if __name__ == "__main__":
    main()
