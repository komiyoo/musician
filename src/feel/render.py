"""渲染 + 混音（带缓存）：ArrangementSpec → out/web/<id>.wav (+ .mp3)。

Cache layout (build/feel/):
  cache/stems/<part>-<h>.wav     raw stem, h = hash(part MIDI bytes, engine mode, sample rate)
  cache/fx/<part>-<h2>.wav       loudness-aligned + FX'd stem, h2 = hash(h, target, bpm, voice mode)
  jobs/<id>/midi/*.mid           MIDI the renderers read (+ full.mid, spec.json)

A tweak that leaves a part's MIDI unchanged reuses its stem (and usually its FX'd stem),
so e.g. moving 「是否抢口播」 only re-renders the violin (if at all) and re-mixes.
Changing speed changes every part's tempo map, so everything re-renders.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import soundfile as sf

from src import config as C
from src.feel import compose as CP
from src.feel.spec import ArrangementSpec

CACHE_VERSION = "feel-1"
FEEL_DIR = C.ROOT / "build" / "feel"
OUT_WEB = C.OUT_DIR / "web"
LOCK = threading.Lock()          # src.config is module-global state: one render at a time
_POOL: ProcessPoolExecutor | None = None


def _pool() -> ProcessPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=8, mp_context=get_context("spawn"))
    return _POOL


def _h(*parts) -> str:
    m = hashlib.sha1(CACHE_VERSION.encode())
    for p in parts:
        m.update(p if isinstance(p, bytes) else str(p).encode())
        m.update(b"\0")
    return m.hexdigest()[:16]


def _render_worker(part: str, midi_dir: str, tmp_dir: str, form: dict, fallback: bool) -> tuple[str, str]:
    """Runs in a separate process: point config at the job, render one part with the existing renderers."""
    from src import config as CC
    from src.render import sfizz_render, surge_render
    CC.MIDI_DIR = Path(midi_dir)
    CC.STEMS_DIR = Path(tmp_dir)
    CC.BPM, CC.N_BARS, CC.TAIL_SECONDS = form["bpm"], form["bars"], form["tail"]
    CC.RIT_BPM = {int(k): v for k, v in form["rit"].items()}
    mod = surge_render if CC.PARTS[part]["engine"] == "surge" else sfizz_render
    path = mod.render(part, force_fallback=fallback)
    return path, (Path(tmp_dir) / f"{part}.engine").read_text()


def generate(spec: ArrangementSpec, fallback: bool = False, log=print) -> dict:
    from pedalboard import HighShelfFilter, PeakFilter, Pedalboard
    from src.mix import fx, loudness

    t0 = time.time()
    with LOCK:
        score = CP.compose(spec)                       # also applies spec → src.config
        per_part, full_mid = CP.midi_files(spec, score)
        spec_json = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)
        job = _h(spec_json, fallback)
        job_dir = FEEL_DIR / "jobs" / job
        midi_dir = job_dir / "midi"
        midi_dir.mkdir(parents=True, exist_ok=True)
        for part, b in per_part.items():
            (midi_dir / f"{part}.mid").write_bytes(b)
        (midi_dir / "full.mid").write_bytes(full_mid)
        stem_dir, fx_dir = FEEL_DIR / "cache" / "stems", FEEL_DIR / "cache" / "fx"
        stem_dir.mkdir(parents=True, exist_ok=True)
        fx_dir.mkdir(parents=True, exist_ok=True)
        mode = "fallback" if fallback else "auto"
        sr = C.SAMPLE_RATE
        form = {"bpm": C.BPM, "bars": C.N_BARS, "tail": C.TAIL_SECONDS, "rit": C.RIT_BPM}

        # ---------------------------------------------------- 1. render only parts whose MIDI changed
        keys = {p: _h(b, mode, sr) for p, b in per_part.items()}
        todo = [p for p in per_part if not (stem_dir / f"{p}-{keys[p]}.wav").exists()]
        cached = [p for p in per_part if p not in todo]
        if todo:
            log(f"[feel] rendering {', '.join(todo)} (cached: {', '.join(cached) or '-'})")
            futs = {}
            for p in todo:
                tmp = FEEL_DIR / "tmp" / f"{p}-{keys[p]}"
                tmp.mkdir(parents=True, exist_ok=True)
                futs[p] = (tmp, _pool().submit(_render_worker, p, str(midi_dir), str(tmp), form, fallback))
            for p, (tmp, fut) in futs.items():
                path, engine = fut.result()
                shutil.move(path, stem_dir / f"{p}-{keys[p]}.wav")
                (stem_dir / f"{p}-{keys[p]}.engine").write_text(engine)
                shutil.rmtree(tmp, ignore_errors=True)
        else:
            log(f"[feel] all {len(per_part)} parts cached")
        t_render = time.time() - t0

        # ---------------------------------------------------- 2. loudness align + per-track FX (cached)
        fx.BEAT = 60.0 / spec.bpm
        fx.DOTTED_EIGHTH = fx.BEAT * 0.75                 # arp delay follows the tempo
        n = int(round(C.total_seconds() * sr))
        bus = np.zeros((2, n), dtype=np.float32)
        report = {}
        lead_trim = {"不抢": -4.0, "平衡": 0.0, "偏配乐": +1.5}[spec.voice_mode]
        for p in per_part:
            target = loudness.target_for(p) + (lead_trim if p in ("violin", "piano") else 0.0)
            fk = _h(keys[p], target, spec.bpm, n)
            fpath = fx_dir / f"{p}-{fk}.wav"
            if fpath.exists():
                proc, _ = sf.read(fpath, always_2d=True, dtype="float32")
                proc = proc.T
            else:
                stem, _ = sf.read(stem_dir / f"{p}-{keys[p]}.wav", always_2d=True, dtype="float32")
                aligned, _, _ = loudness.align(stem.T, sr, target)
                proc = fx.chain(p)(aligned, sr)[:, :n]
                sf.write(fpath, proc.T, sr, subtype="FLOAT")
            bus[:, : proc.shape[1]] += proc[:, :n]
            report[p] = {"engine": (stem_dir / f"{p}-{keys[p]}.engine").read_text(), "target_lufs": target,
                         "cached": p in cached}

        # ---------------------------------------------------- 3. master: voice pocket, brightness tilt, LUFS, limiter
        tilt = (spec.knobs["brightness"] - 50) / 50 * 3.0          # ±3 dB air
        chain = [*fx.master_chain(), HighShelfFilter(6000, gain_db=tilt)]
        if spec.duck_for_voice:
            chain.append(PeakFilter(2500, -3.0 if spec.voice_mode == "不抢" else -1.5, 0.7))
        master = Pedalboard(chain)(bus, sr)
        fi, fo = int(0.03 * sr), int(min(2.5, spec.tail_s) * sr)
        master[:, :fi] *= np.linspace(0, 1, fi)[None, :]
        master[:, -fo:] *= np.linspace(1, 0, fo)[None, :] ** 1.5
        master, _, _ = loudness.align(master, sr, spec.bed_lufs)
        master = fx.limiter(master, sr)
        lufs = loudness.measure_lufs(master, sr)
        duration = master.shape[1] / sr

    kind = "preview" if spec.preview else "full"
    OUT_WEB.mkdir(parents=True, exist_ok=True)
    wav = OUT_WEB / f"{kind}-{job}.wav"
    sf.write(wav, master.T, sr, subtype="PCM_24")
    mp3 = None
    if shutil.which("ffmpeg"):
        mp3 = wav.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "192k", str(mp3)], check=False)
        if not mp3.exists():
            mp3 = None
    mid = OUT_WEB / f"{kind}-{job}.mid"
    mid.write_bytes(full_mid)
    spec_d = spec.to_dict()
    spec_d["duration_s"] = round(duration, 1)
    (job_dir / "spec.json").write_text(json.dumps(spec_d, ensure_ascii=False, indent=1))
    result = {
        "id": job, "kind": kind, "wav": str(wav), "mp3": str(mp3) if mp3 else None, "mid": str(mid),
        "duration_s": round(duration, 1), "lufs": round(lufs, 1), "parts": report,
        "rendered": todo, "reused": cached, "seconds": round(time.time() - t0, 1),
        "render_seconds": round(t_render, 1), "spec": spec_d,
    }
    log(f"[feel] {kind} {job}: {duration:.1f}s, {lufs:.1f} LUFS, rendered {len(todo)}/{len(per_part)} parts "
        f"in {result['seconds']}s -> {wav}")
    return result


def main(argv=None):
    """CLI: python -m src.feel.render "悬疑一点的科技解说" [--full] [--fallback] [--mood 30 ...]"""
    import argparse
    from src.feel.spec import build_spec, parse_feel
    ap = argparse.ArgumentParser(description="一句话感觉 → 配乐")
    ap.add_argument("feel", nargs="?", default="")
    ap.add_argument("--full", action="store_true", help="完整轨（默认试听 ~20 秒）")
    ap.add_argument("--fallback", action="store_true", help="强制 numpy 兜底合成器（快）")
    for k in ("mood", "speed", "density", "brightness", "voice"):
        ap.add_argument(f"--{k}", type=int)
    a = ap.parse_args(argv)
    knobs, reasons = parse_feel(a.feel)
    knobs.update({k: getattr(a, k) for k in knobs if getattr(a, k) is not None})
    for r in reasons:
        print("[feel]", r)
    res = generate(build_spec(a.feel, knobs, preview=not a.full), fallback=a.fallback)
    print(json.dumps({k: res[k] for k in ("wav", "mp3", "duration_s", "lufs", "rendered", "reused")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
