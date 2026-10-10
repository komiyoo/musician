"""Step 2 — render every part to build/stems/<part>.wav (Surge XT + sfizz, with fallback).

Run:  python -m src.render.render_all [--fallback]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from src import config as C
from src.render import sfizz_render, surge_render, fluidsynth_render
from src.render.midi_io import write_stem


def render_part(part: str, fallback: bool = False, strict: bool = False) -> str:
    engine = C.PARTS[part]['engine']
    if fallback or engine == 'fallback':
        if strict:
            raise RuntimeError(f'{part}: strict rendering cannot use fallback audio')
        from src.render.fallback_synth import render_part as synthesize
        return write_stem(part, synthesize(part, C.SAMPLE_RATE), C.SAMPLE_RATE, 'fallback')
    modules = {'surge': surge_render, 'sfizz': sfizz_render, 'fluidsynth': fluidsynth_render}
    return modules[engine].render(part, sr=C.SAMPLE_RATE, strict=strict)


def render_signature(part: str) -> str:
    """Invalidate cached audio when its renderer, patch or sample assets change."""
    settings = C.PARTS[part]
    paths = [*Path(__file__).parent.glob('*.py'), *C.INSTRUMENTS_DIR.glob('*.sfz'),
             C.PRESETS_DIR / 'surge_presets.json', C.PRESETS_DIR / f'{part}.state',
             C.PRESETS_DIR / f'{part}.vstpreset']
    if settings['engine'] == 'surge':
        backend = surge_render.available_backend()
        patch = surge_render.resolve_patch(part)
        if patch:
            paths.append(patch)
        plugin = surge_render.surge_plugin_path()
        if plugin:
            paths.append(Path(plugin))
    elif settings['engine'] == 'fluidsynth':
        binary = shutil.which(C.FLUIDSYNTH)
        font = settings.get('soundfont')
        backend = 'fluidsynth' if binary and font and Path(font).is_file() else 'fallback'
        if binary:
            paths.append(Path(binary))
    else:
        backend = 'sfizz' if sfizz_render.sfizz_available() else 'fallback'
        binary = shutil.which(C.SFIZZ_RENDER)
        if binary:
            paths.append(Path(binary))
        source = C.SAMPLE_SOURCES.get(settings.get('instrument', part), {})
        available = bool(settings.get('sfz') and Path(settings['sfz']).is_file())
        if source.get('wav_dir'):
            paths.append(C.SAMPLES_DIR / source['wav_dir'])
            available |= (C.SAMPLES_DIR / source['wav_dir']).is_dir()
        elif source.get('sfz_glob'):
            packs = sorted(C.SAMPLES_DIR.glob(source['sfz_glob']))
            paths.extend(p.parent for p in packs)
            available |= bool(packs)
        if not available:
            backend = 'fallback'
    for name in ('sfz', 'soundfont'):
        if settings.get(name):
            asset = Path(settings[name])
            paths.append(asset.parent if name == 'sfz' else asset)
    assets = []
    for path in paths:
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for file in files:
            if file.is_file():
                stat = file.stat()
                assets.append((str(file), stat.st_size, stat.st_mtime_ns))
            else:
                assets.append((str(file), None, None))
    return json.dumps({'backend': backend, 'settings': settings, 'assets': assets}, sort_keys=True)


def effects_signature() -> str:
    paths = [C.ROOT / 'src' / 'mix' / name for name in ('fx.py', 'loudness.py')]
    return hashlib.sha256(b''.join(path.read_bytes() for path in paths)).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback", action="store_true", help="force the numpy sketch synth for all parts")
    ap.add_argument("--strict", action="store_true", help="fail instead of substituting a draft instrument")
    ap.add_argument("parts", nargs="*", default=list(C.PARTS))
    a = ap.parse_args(argv)
    for part in a.parts:
        path = render_part(part, a.fallback, a.strict)
        tag = (C.STEMS_DIR / f"{part}.engine").read_text()
        print(f"[render] {part:7s} [{tag}] -> {path}")


if __name__ == "__main__":
    main()
