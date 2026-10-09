"""Step 2b — 原声音色：用 sfizz_render (CLI) 渲染 piano / strings / drums。

For each part:
  1. src.render.sfz_prepare builds build/sfz/<part>.sfz from the sample pack +
     instruments/<part>.sfz (our attack / filter / velocity-curve overrides);
  2. `sfizz_render --sfz build/sfz/<part>.sfz --midi midi/<part>.mid --wav ...`;
  3. the result is trimmed/padded to the song length -> build/stems/<part>.wav.

If sfizz_render or the samples are missing, the numpy fallback synth renders
the same MIDI so the pipeline still produces audio.

Run:  python -m src.render.sfizz_render [piano violin viola cello drums]
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import numpy as np
import soundfile as sf

from src import config as C
from src.render.midi_io import write_stem
from src.render.sfz_prepare import prepare

SFIZZ_PARTS = [p for p, d in C.PARTS.items() if d["engine"] == "sfizz"]


def sfizz_available() -> bool:
    return shutil.which(C.SFIZZ_RENDER) is not None


def render_sfizz(part: str, sfz, sr: int) -> np.ndarray:
    raw = C.STEMS_DIR / f"{part}.sfizz_raw.wav"
    C.STEMS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [C.SFIZZ_RENDER, "--sfz", str(sfz), "--midi", str(C.MIDI_DIR / f"{part}.mid"),
           "--wav", str(raw), "--samplerate", str(sr), "--quality", "3", "--polyphony", "128"]
    print("[sfizz]", " ".join(cmd))
    subprocess.run(cmd, check=True)
    audio, file_sr = sf.read(raw, always_2d=True, dtype="float32")
    if file_sr != sr:
        raise RuntimeError(f"sfizz wrote {file_sr} Hz, expected {sr}")
    return audio.T


def render(part: str, sr: int = C.SAMPLE_RATE, force_fallback: bool = False) -> str:
    reason = None
    if force_fallback:
        reason = "fallback forced"
    elif not sfizz_available():
        reason = f"'{C.SFIZZ_RENDER}' not on PATH"
    else:
        sfz = prepare(part)
        if sfz is None:
            reason = f"samples for {part} not found under {C.SAMPLES_DIR}"
        else:
            try:
                return write_stem(part, render_sfizz(part, sfz, sr), sr, "sfizz")
            except Exception as e:  # noqa: BLE001
                reason = f"sfizz failed: {e}"
    print(f"[sfizz] {part}: {reason} -> fallback synth")
    from src.render.fallback_synth import render_part
    return write_stem(part, render_part(part, sr), sr, "fallback")


def main(argv=None):
    parts = (argv or sys.argv[1:]) or SFIZZ_PARTS
    for p in parts:
        print("[sfizz] wrote", render(p))


if __name__ == "__main__":
    main()
