"""演示：《致艾丽丝》(Für Elise) 开头动机 → 马尔科夫链 → 温度采样变奏 → 两段短音频（原动机 / 变奏）。

种子 MIDI：examples/fur_elise_motif.mid（不存在时由 FUR_ELISE 自动写出；python -m src.markov.demo 也会重写它）。
音色用一个极简 numpy 钢琴（和 src/render/fallback_synth.py 的钢琴同一思路），几百毫秒就能渲完。
"""
from __future__ import annotations

import hashlib
import io
import random
import shutil
import subprocess
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from src import config as C
from src.markov.chain import MarkovChain, note_name

MOTIF_MID = C.ROOT / "examples" / "fur_elise_motif.mid"
TPB = 480
SIXTEENTH = TPB // 4
BPM = 70                                   # 四分音符 70 → 十六分音符 ≈ 0.21 秒（poco moto）
# (音高 或 None=休止, 十六分音符个数) — 右手旋律 + 分解和弦，A 小调，第 1–8 小节（3/8 拍）
E5, Ds5, D5, C5, B4, A4, Gs4, E4, C4 = 76, 75, 74, 72, 71, 69, 68, 64, 60
_PHRASE = [(E5, 1), (Ds5, 1), (E5, 1), (Ds5, 1), (E5, 1), (B4, 1), (D5, 1), (C5, 1),
           (A4, 2), (None, 1), (C4, 1), (E4, 1), (A4, 1),
           (B4, 2), (None, 1), (E4, 1), (Gs4, 1), (B4, 1)]
FUR_ELISE = (_PHRASE + [(C5, 2), (None, 1), (E4, 1)]
             + _PHRASE[:-3] + [(E4, 1), (C5, 1), (B4, 1), (A4, 4)])


def motif_midi_bytes() -> bytes:
    mf = mido.MidiFile(type=1, ticks_per_beat=TPB)
    tr = mido.MidiTrack()
    tr += [mido.MetaMessage("track_name", name="piano", time=0),
           mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(BPM), time=0),
           mido.MetaMessage("time_signature", numerator=3, denominator=8, time=0),
           mido.Message("program_change", channel=0, program=0, time=0)]
    gap = 0
    for p, d in FUR_ELISE:
        if p is None:
            gap += d * SIXTEENTH
            continue
        tr.append(mido.Message("note_on", note=p, velocity=72, time=gap))
        tr.append(mido.Message("note_off", note=p, velocity=0, time=int(d * SIXTEENTH * 0.95)))
        gap = d * SIXTEENTH - int(d * SIXTEENTH * 0.95)
    tr.append(mido.MetaMessage("end_of_track", time=TPB))
    mf.tracks.append(tr)
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def write_motif(path: Path = MOTIF_MID) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(motif_midi_bytes())
    return path


def read_notes(src) -> list[tuple[float, float, int, int]]:
    """MIDI → [(t_on 秒, t_off 秒, pitch, vel)]（tempo-aware）。"""
    mf = mido.MidiFile(file=io.BytesIO(src)) if isinstance(src, bytes) else mido.MidiFile(src)
    t, open_, out = 0.0, {}, []
    for m in mf:
        t += m.time
        if m.type == "note_on" and m.velocity > 0:
            open_[m.note] = (t, m.velocity)
        elif m.type in ("note_on", "note_off") and m.note in open_:
            t0, v = open_.pop(m.note)
            out.append((t0, t, m.note, v))
    return sorted(out)


def piano(notes, sr: int = 44100, tail: float = 1.2) -> np.ndarray:
    end = max(n[1] for n in notes) + tail
    out = np.zeros(int(end * sr) + sr)
    for t_on, t_off, p, v in notes:
        f0 = 440.0 * 2 ** ((p - 69) / 12)
        hold = max(1, int((t_off - t_on) * sr))
        n = hold + int(0.9 * sr)
        t = np.arange(n) / sr
        sig = sum((1 / k ** 1.3) * np.sin(2 * np.pi * f0 * k * t) * np.exp(-t * (1.5 + 0.9 * k)) for k in range(1, 7))
        env = np.minimum(1, t / 0.008)
        env[hold:] *= np.exp(-(t[hold:] - t[hold]) * 6)
        s0 = int(t_on * sr)
        out[s0:s0 + n] += sig * env * (v / 127) ** 1.5
    out = out[: int(end * sr)]
    out /= (np.max(np.abs(out)) or 1) / 0.6
    return out.astype(np.float32)


def _write_audio(path: Path, audio: np.ndarray, sr: int) -> str:
    sf.write(path, audio, sr, subtype="PCM_16")
    if shutil.which("ffmpeg"):
        mp3 = path.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-b:a", "160k", str(mp3)], check=False)
        if mp3.exists():
            return mp3.name
    return path.name


def demo(temperature: float = 0.9, seed: int | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or (C.OUT_DIR / "web")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not MOTIF_MID.exists():
        write_motif()
    src = MOTIF_MID.read_bytes()
    notes = read_notes(src)
    seq = [p for _, _, p, _ in notes]
    chain = MarkovChain.fit(seq)
    seed = random.randrange(1_000_000) if seed is None else int(seed)
    rng = random.Random(seed)
    cur, var = seq[0], [seq[0]]
    for _ in seq[1:]:
        nxt = chain.sample_next(cur, rng, temperature)
        cur = nxt if nxt is not None else seq[0]
        var.append(cur)
    var[-1] = 69 if 69 in chain.state_counts else var[-1]      # 落回主音 A，听起来像「说完了」
    var_notes = [(a, b, p, v) for (a, b, _, v), p in zip(notes, var)]
    sr = 44100
    tag = hashlib.sha1(f"{temperature:.2f}-{seed}".encode()).hexdigest()[:10]
    orig_name = _write_audio(out_dir / "markov-elise-orig.wav", piano(notes, sr), sr)
    var_name = _write_audio(out_dir / f"markov-elise-var-{tag}.wav", piano(var_notes, sr), sr)
    return {
        "source": "致艾丽丝 (Für Elise) 开头动机", "midi": MOTIF_MID.name, "temperature": temperature, "seed": seed,
        "graph": chain.graph(), "original": [note_name(p) for p in seq], "variation": [note_name(p) for p in var],
        "changed": sum(a != b for a, b in zip(seq, var)),
        "audio_original": f"/media/{orig_name}", "audio_variation": f"/media/{var_name}",
    }


if __name__ == "__main__":
    import json
    print("wrote", write_motif())
    d = demo()
    print(json.dumps({k: d[k] for k in ("seed", "changed", "original", "variation")}, ensure_ascii=False))
    print("states", d["graph"]["n_states"], "edges", d["graph"]["n_edges"])
