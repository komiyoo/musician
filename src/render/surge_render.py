"""Step 2a — 电子音色：用 Surge XT 渲染 pad / arp / bass。

Backends, tried in order (first one available wins):

  1. surgepy   — Surge XT's own Python binding (build Surge with
                 -DSURGE_BUILD_PYTHON_BINDINGS=ON).  Loads factory .fxp patches
                 *by path*, so presets/surge_presets.json is applied directly.
  2. pedalboard — Spotify pedalboard hosting the Surge XT VST3/AU
                 (CTM_SURGE_PLUGIN=/path/to/Surge\\ XT.vst3).  Patch comes from
                 presets/<part>.vstpreset (plugin.load_preset) or
                 presets/<part>.state (raw plugin state captured with
                 scripts/capture_surge_state.py).  Otherwise Surge's init patch.
  3. fallback  — numpy sketch synth (src/render/fallback_synth.py).

Run:  python -m src.render.surge_render [pad arp bass]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

from src import config as C
from src.render.midi_io import read_part, total_samples, write_stem

SURGE_PARTS = [p for p, d in C.PARTS.items() if d["engine"] == "surge"]
PRESETS = json.loads((C.PRESETS_DIR / "surge_presets.json").read_text())

FACTORY_CANDIDATES = [
    os.environ.get("CTM_SURGE_FACTORY", ""),
    "/usr/share/surge-xt/patches_factory",
    "/usr/local/share/surge-xt/patches_factory",
    "/Library/Application Support/Surge XT/patches_factory",
    "C:/ProgramData/Surge XT/patches_factory",
]


def resolve_patch(part: str, factory_hint: str | None = None) -> Path | None:
    rel = PRESETS.get(part, {}).get("patch")
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p if p.exists() else None
    for base in [factory_hint, *FACTORY_CANDIDATES]:
        if base and (Path(base) / rel).exists():
            return Path(base) / rel
    return None


# ------------------------------------------------------------------ backend 1
def render_surgepy(part: str, sr: int) -> np.ndarray:
    import surgepy  # type: ignore

    s = surgepy.createSurge(sr)
    patch = resolve_patch(part, os.path.join(s.getFactoryDataPath(), "patches_factory"))
    if patch:
        s.loadPatch(str(patch))
        print(f"[surge] {part}: surgepy + patch {patch.name}")
    else:
        print(f"[surge] {part}: surgepy, patch not found -> init patch")
    notes, _, _ = read_part(part)
    bs = s.getBlockSize()
    n_blocks = total_samples(sr) // bs + 1
    events = sorted([(n.t_on, 1, n.pitch, n.vel) for n in notes] + [(n.t_off, 0, n.pitch, 0) for n in notes])
    out = np.zeros((2, n_blocks * bs), dtype=np.float32)
    ei = 0
    for b in range(n_blocks):
        t_block_end = (b + 1) * bs / sr
        while ei < len(events) and events[ei][0] < t_block_end:
            _, on, pitch, vel = events[ei]
            (s.playNote if on else s.releaseNote)(0, pitch, vel if on else 0)
            ei += 1
        s.process()
        out[:, b * bs:(b + 1) * bs] = s.getOutput()
    return out


# ------------------------------------------------------------------ backend 2
PREROLL = 0.5  # s — Surge applies a newly set patch on its first audio block


def render_pedalboard(part: str, sr: int) -> np.ndarray:
    import pedalboard
    from src.render.juce_state import state_from_fxp

    plugin = pedalboard.load_plugin(C.SURGE_PLUGIN)
    vst = C.PRESETS_DIR / f"{part}.vstpreset"
    state = C.PRESETS_DIR / f"{part}.state"
    patch = resolve_patch(part)
    if vst.exists():
        plugin.load_preset(str(vst))
        print(f"[surge] {part}: pedalboard + {vst.name}")
    elif state.exists():
        plugin.raw_state = state.read_bytes()
        print(f"[surge] {part}: pedalboard + captured state {state.name}")
    elif patch:
        # headless patch-by-name: .fxp chunk -> JUCE VST3 state (see juce_state.py)
        plugin.raw_state = state_from_fxp(plugin.raw_state, patch)
        print(f"[surge] {part}: pedalboard + factory patch '{patch.parent.name}/{patch.stem}'")
    else:
        print(f"[surge] {part}: pedalboard, patch not found -> Surge init patch "
              f"(set CTM_SURGE_FACTORY or capture one with scripts/capture_surge_state.py {part})")
    _, _, msgs = read_part(part)
    msgs = [m.copy(time=m.time + PREROLL) for m in msgs if m.type in ("note_on", "note_off", "control_change")]
    audio = plugin(msgs, duration=C.total_seconds() + PREROLL, sample_rate=sr, num_channels=2)
    return np.asarray(audio, dtype=np.float32)[:, int(PREROLL * sr):]


VST3_CANDIDATES = [
    "/usr/lib/vst3/Surge XT.vst3",
    "/usr/local/lib/vst3/Surge XT.vst3",
    str(Path.home() / ".vst3/Surge XT.vst3"),
    "/Library/Audio/Plug-Ins/VST3/Surge XT.vst3",
    "C:/Program Files/Common Files/VST3/Surge XT.vst3",
]


def surge_plugin_path() -> str:
    """CTM_SURGE_PLUGIN, else the standard install location (if present)."""
    for cand in [C.SURGE_PLUGIN, *VST3_CANDIDATES]:
        if cand and Path(cand).exists():
            C.SURGE_PLUGIN = cand
            return cand
    return ""


def available_backend() -> str:
    try:
        import surgepy  # noqa: F401
        return "surgepy"
    except ImportError:
        pass
    if surge_plugin_path():
        return "pedalboard"
    return "fallback"


def render(part: str, sr: int = C.SAMPLE_RATE, force_fallback: bool = False) -> str:
    backend = "fallback" if force_fallback else available_backend()
    try:
        if backend == "surgepy":
            audio = render_surgepy(part, sr)
        elif backend == "pedalboard":
            audio = render_pedalboard(part, sr)
        else:
            raise RuntimeError("Surge XT not available")
        gain = 10 ** (PRESETS.get(part, {}).get("gain_db", 0.0) / 20)
        return write_stem(part, audio * gain, sr, f"surge-{backend}")
    except Exception as e:  # noqa: BLE001
        if backend != "fallback":
            print(f"[surge] {part}: {backend} failed ({e}); using fallback synth")
        else:
            print(f"[surge] {part}: Surge XT not found -> fallback synth")
        from src.render.fallback_synth import render_part
        return write_stem(part, render_part(part, sr), sr, "fallback")


def main(argv=None):
    parts = (argv or sys.argv[1:]) or SURGE_PARTS
    for p in parts:
        print("[surge] wrote", render(p))


if __name__ == "__main__":
    main()
