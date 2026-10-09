"""Pick a Surge XT patch in its GUI and save it for headless rendering.

    python scripts/capture_surge_state.py pad     # opens Surge XT's editor

Browse to the patch you want (e.g. Pads > MKS-70 Warm Pad), close the window,
and the plugin state is written to presets/pad.state.  surge_render.py's
pedalboard backend loads it via `plugin.raw_state`.
Requires a desktop session and CTM_SURGE_PLUGIN pointing at Surge XT.vst3.
"""
import sys
from pathlib import Path

import pedalboard

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config as C  # noqa: E402

part = sys.argv[1] if len(sys.argv) > 1 else "pad"
plugin = pedalboard.load_plugin(C.SURGE_PLUGIN)
state = C.PRESETS_DIR / f"{part}.state"
if state.exists():
    plugin.raw_state = state.read_bytes()
plugin.show_editor()
state.write_bytes(plugin.raw_state)
print("saved", state)
