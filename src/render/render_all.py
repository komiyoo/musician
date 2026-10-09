"""Step 2 — render every part to build/stems/<part>.wav (Surge XT + sfizz, with fallback).

Run:  python -m src.render.render_all [--fallback]
"""
from __future__ import annotations

import argparse

from src import config as C
from src.render import sfizz_render, surge_render


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback", action="store_true", help="force the numpy sketch synth for all parts")
    ap.add_argument("parts", nargs="*", default=list(C.PARTS))
    a = ap.parse_args(argv)
    for part in a.parts:
        eng = C.PARTS[part]["engine"]
        mod = surge_render if eng == "surge" else sfizz_render
        path = mod.render(part, force_fallback=a.fallback)
        tag = (C.STEMS_DIR / f"{part}.engine").read_text()
        print(f"[render] {part:7s} [{tag}] -> {path}")


if __name__ == "__main__":
    main()
