"""List Surge XT factory patches (3000+ incl. 3rd-party banks) so you can pick pad/arp/bass.

    python scripts/list_surge_presets.py            # all categories + counts
    python scripts/list_surge_presets.py Pads       # patches in a category
    python scripts/list_surge_presets.py --grep warm
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.render.surge_render import FACTORY_CANDIDATES  # noqa: E402

roots = [Path(p) for p in FACTORY_CANDIDATES if p and Path(p).is_dir()]
for extra in ("patches_3rdparty",):
    roots += [r.parent / extra for r in roots if (r.parent / extra).is_dir()]
if not roots:
    sys.exit("Surge XT factory patches not found; set CTM_SURGE_FACTORY=/path/to/patches_factory")
args = sys.argv[1:]
fxps = [p for r in roots for p in r.rglob("*.fxp")]
if not args:
    from collections import Counter
    for cat, n in sorted(Counter(p.relative_to(p.parents[1]).parts[0] if len(p.parts) > 1 else "" for p in fxps).items()):
        print(f"{n:5d}  {cat}")
    print(f"{len(fxps)} patches total")
elif args[0] == "--grep":
    for p in sorted(fxps):
        if args[1].lower() in str(p).lower():
            print(p)
else:
    for p in sorted(fxps):
        if p.parent.name == args[0]:
            print(f"{args[0]}/{p.name}")
