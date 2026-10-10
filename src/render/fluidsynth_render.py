"""Optional SF2/SF3 rendering through the FluidSynth command-line player."""
from pathlib import Path
import shutil
import subprocess

import soundfile as sf

from src import config as C
from src.render.midi_io import write_stem


def render(part: str, sr: int = C.SAMPLE_RATE, force_fallback: bool = False, strict: bool = False) -> str:
    try:
        if force_fallback:
            raise RuntimeError('fallback forced')
        if not shutil.which(C.FLUIDSYNTH):
            raise RuntimeError('FluidSynth executable is unavailable')
        font = C.PARTS[part].get('soundfont')
        if not font or not Path(font).is_file():
            raise RuntimeError('configured SoundFont is unavailable')
        C.STEMS_DIR.mkdir(parents=True, exist_ok=True)
        raw = C.STEMS_DIR / f'{part}.fluid_raw.wav'
        subprocess.run([C.FLUIDSYNTH, '-ni', '-r', str(sr), '-g', '0.8', '-T', 'wav', '-O', 'float',
                        '-F', str(raw), str(font), str(C.MIDI_DIR / f'{part}.mid')],
                       check=True, capture_output=True, timeout=max(60, C.total_seconds() * 4))
        audio, file_sr = sf.read(raw, dtype='float32', always_2d=True)
        if file_sr != sr:
            raise RuntimeError(f'FluidSynth wrote {file_sr} Hz, expected {sr}')
        path = write_stem(part, audio.T, sr, 'fluidsynth')
        raw.unlink()
        return path
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        if strict:
            raise RuntimeError(f'{part}: {error}') from error
        print(f'[fluidsynth] {part}: {error} -> fallback synth')
        from src.render.fallback_synth import render_part
        return write_stem(part, render_part(part, sr), sr, 'fallback')
