"""Central configuration: paths, tempo map, parts, loudness targets.

所有路径都可以用环境变量覆盖，方便在不同机器上运行。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from src.types import TempoMap

ROOT = Path(__file__).resolve().parents[1]
MIDI_DIR = ROOT / "midi"
BUILD_DIR = ROOT / "build"
STEMS_DIR = BUILD_DIR / "stems"
SFZ_BUILD_DIR = BUILD_DIR / "sfz"
OUT_DIR = ROOT / "out"
INSTRUMENTS_DIR = ROOT / "instruments"
PRESETS_DIR = ROOT / "presets"

SAMPLES_DIR = Path(os.environ.get("CTM_SAMPLES_DIR", ROOT / "samples"))
SURGE_PLUGIN = os.environ.get("CTM_SURGE_PLUGIN", "")  # path to "Surge XT.vst3"
SFIZZ_RENDER = os.environ.get("CTM_SFIZZ_RENDER", "sfizz_render")
FLUIDSYNTH = os.environ.get("CTM_FLUIDSYNTH", "fluidsynth")

SAMPLE_RATE = int(os.environ.get("CTM_SAMPLE_RATE", "48000"))
SEED = int(os.environ.get("CTM_SEED", "20261009"))
RENDER_LOCK = threading.RLock()  # configuration is shared by the existing renderers

# ---------------------------------------------------------------- musical form
KEY = "D minor"
KEY_CHANGES = {}        # optional 1-based bar -> MIDI key signature
BPM = 100
BEATS_PER_BAR = 4
N_BARS = 22
SECTIONS = {            # 1-based inclusive bar ranges
    "intro": (1, 6),     # pad + piano
    "develop": (7, 18),  # + strings, bass, arp, drums
    "resolve": (19, 22), # piano slows (ritardando), cadence on Dm
}
# Ritardando in the resolve section: per-bar BPM. Everything else is 100 BPM.
RIT_BPM = {19: 96, 20: 90, 21: 84, 22: 76}
TAIL_SECONDS = 3.6       # let the final Dm chord + reverb ring out (total ≈ 58 s)

# ---------------------------------------------------------------- parts
# engine: which renderer is first-class for this part.
PARTS = {
    "pad":    {"engine": "surge", "channel": 0, "program": 89},  # GM 90 Warm Pad (fallback)
    "piano":  {"engine": "sfizz", "channel": 1, "program": 0},
    "violin": {"engine": "sfizz", "channel": 2, "program": 48},
    "viola":  {"engine": "sfizz", "channel": 3, "program": 48},
    "cello":  {"engine": "sfizz", "channel": 4, "program": 48},
    "bass":   {"engine": "surge", "channel": 5, "program": 38},
    "arp":    {"engine": "surge", "channel": 6, "program": 81},
    "drums":  {"engine": "sfizz", "channel": 9, "program": 0},
}

# Loudness targets (integrated LUFS) applied to each stem before FX.
LOUDNESS_TARGETS = {
    "piano": -21.0,
    "violin": -22.0,
    "viola": -24.0,   # inner voice sits under the violin melody
    "cello": -24.0,
    "bass": -25.0,
    "drums": -25.0,
    "pad": -29.0,
    "arp": -29.0,
}
MASTER_TARGET_LUFS = -18.0   # bed under narration; dialog usually sits ~-16..-14
MASTER_CEILING_DB = -1.0
NARRATION_MODE = True

# Sample packs (NOT committed). See README for download links.
SAMPLE_SOURCES = {
    # pack SFZ that will be flattened and wrapped by instruments/<part>.sfz
    "piano": {"sfz_glob": "SalamanderGrandPiano*/SalamanderGrandPiano-V3*.sfz"},
    "drums": {"sfz_glob": "SamsSonor/**/SamsSonor.sfz"},
    # VSCO-2 CE ships raw WAVs on GitHub -> regions are generated from filenames
    # VSCO names notes with middle C = C3, hence octave_offset=1
    "violin": {"wav_dir": "VSCO-2-CE/Strings/Violin Section/susVib", "octave_offset": 1},
    "viola":  {"wav_dir": "VSCO-2-CE/Strings/Viola Section/susvib", "octave_offset": 1},
    "cello":  {"wav_dir": "VSCO-2-CE/Strings/Cello Section/susvib", "octave_offset": 1},
}


def bar_bpm(bar: int) -> float:
    return float(RIT_BPM.get(bar, BPM))


def beat_to_seconds(beat: float) -> float:
    """Absolute beat (0-based quarter notes) -> seconds, honoring the rit."""
    return tempo_map().seconds_at(beat)


def tempo_map() -> TempoMap:
    return TempoMap.from_bpms((bar_bpm(bar) for bar in range(1, N_BARS + 1)), BEATS_PER_BAR, TAIL_SECONDS)


def total_seconds() -> float:
    return tempo_map().duration_s
