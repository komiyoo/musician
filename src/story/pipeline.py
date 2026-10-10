"""Isolated story jobs reusing the existing MIDI, rendering and mixing stages."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from src import config as C
from src.score import write_score as W
from src.story.compose import compose, timeline_for
from src.types import INSTRUMENTS, StorySpec


@contextmanager
def profile(story: StorySpec, timeline, output: Path, source: Path, soundfont: str | None = None,
            narration: bool = False):
    settings, levels, sections = {}, {}, {}
    melodic, percussion = 0, 0
    levels_by_role = {'theme': -22, 'root': -27, 'third': -28, 'seventh': -28, 'color': -30,
                      'arpeggio': -26, 'pulse': -27, 'counter': -25, 'pad': -30, 'percussion': -27}
    for part in story.parts:
        ins = INSTRUMENTS[part.instrument]
        data = part.model_dump(exclude_none=True)
        for name in ('sfz', 'soundfont', 'patch'):
            if data.get(name):
                path = Path(data[name]).expanduser()
                data[name] = str((source.parent / path).resolve())
        if soundfont and not any(data.get(k) for k in ('soundfont', 'sfz', 'patch')) and part.engine in ('auto', 'fluidsynth'):
            data['soundfont'] = str(Path(soundfont).expanduser().resolve())
        engine = part.engine
        if engine == 'auto':
            engine = ('sfizz' if data.get('sfz') else 'fluidsynth' if data.get('soundfont') else
                      'surge' if data.get('patch') or ins.family in ('pad', 'bass', 'arp') else 'sfizz')
        if ins.drum_note is not None:
            channel, port = 9, (len(story.parts) + 14) // 15 + percussion
            percussion += 1
        else:
            channel, port = melodic % 15, melodic // 15
            channel += channel >= 9
            melodic += 1
        settings[part.id] = {**data, 'engine': engine, 'program': ins.program, 'family': ins.family,
                             'channel': channel, 'port': port}
        levels[part.id] = levels_by_role[part.role] + part.gain_db
    bar, key_changes = 1, {}
    for section in story.sections:
        sections[section.id] = (bar, bar + section.bars - 1)
        key_changes[bar] = section.key
        bar += section.bars
    updates = dict(KEY=story.sections[0].key, KEY_CHANGES=key_changes, BPM=60_000_000 / timeline.tempos[0],
                   N_BARS=len(timeline.tempos), BEATS_PER_BAR=4, RIT_BPM={i: 60_000_000 / t for i, t in enumerate(timeline.tempos, 1)},
                   TAIL_SECONDS=story.tail_s, SECTIONS=sections, SEED=story.seed, PARTS=settings,
                   LOUDNESS_TARGETS=levels, MASTER_TARGET_LUFS=story.master_lufs, NARRATION_MODE=narration,
                   MIDI_DIR=output / 'midi', BUILD_DIR=output / 'build', STEMS_DIR=output / 'stems',
                   SFZ_BUILD_DIR=output / 'sfz', OUT_DIR=output)
    with C.RENDER_LOCK:
        previous = {key: getattr(C, key) for key in updates}
        try:
            for key, value in updates.items():
                setattr(C, key, value)
            yield
        finally:
            for key, value in previous.items():
                setattr(C, key, value)


def run(source: str | Path, out: str | Path | None = None, *, fallback: bool = False, strict: bool = False,
        midi_only: bool = False, video: bool = False, soundfont: str | None = None,
        voice: str | None = None, font: str | None = None, midi: str | Path | None = None,
        sheet_preview: bool = False) -> dict:
    if fallback and strict:
        raise ValueError('--fallback and --strict are mutually exclusive')
    if midi_only and video:
        raise ValueError('--video requires audio rendering')
    if sheet_preview:
        try:
            import verovio  # noqa: F401
        except ImportError as error:
            raise RuntimeError('--sheet-preview requires: uv sync --extra notation') from error
    if video and not shutil.which('ffmpeg'):
        raise RuntimeError('video export requires ffmpeg on PATH')
    if video:
        from src.story.video import resolve_font
        font = resolve_font(font)
    source = Path(source).resolve()
    if source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('story JSON exceeds 2 MiB')
    story = StorySpec.model_validate_json(source.read_text(encoding='utf-8'))
    timeline = timeline_for(story)
    if out is None:
        parent = C.OUT_DIR / 'story'
        parent.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix=f'{source.stem}-', dir=parent))
    else:
        output = Path(out).resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ValueError(f'output must be an empty directory: {output}')
        output.mkdir(parents=True, exist_ok=True)
    with profile(story, timeline, output, source, soundfont or os.getenv('CTM_SOUNDFONT'), bool(voice)):
        tracks = None
        if midi is not None:
            from src.story.midi_import import load
            score, manifest, tracks = load(Path(midi).resolve(), story, timeline)
        else:
            score, manifest = compose(story)
        snapshot = story.model_copy(update={'parts': [part.model_copy(update={
            key: C.PARTS[part.id].get(key) for key in ('sfz', 'soundfont', 'patch')}) for part in story.parts]})
        manifest.story = snapshot
        C.MIDI_DIR.mkdir()
        files, full = W.midi_files(score, tracks=tracks)
        for part, data in files.items():
            (C.MIDI_DIR / f'{part}.mid').write_bytes(data)
        (C.MIDI_DIR / 'full.mid').write_bytes(full)
        (output / 'score.json').write_text(manifest.model_dump_json(indent=2), encoding='utf-8')
        (output / 'story.json').write_text(snapshot.model_dump_json(indent=2), encoding='utf-8')
        from src.story.notation import export
        export(manifest, output, preview=sheet_preview)
        print(f'[story] {story.title}: {C.N_BARS} bars, {len(files)} parts, {len(score.notes)} notes, '
              f'{manifest.timeline.duration_s:.3f}s', flush=True)
        if not midi_only:
            from src.render.render_all import render_part
            from src.mix import mix
            for part in files:
                render_part(part, fallback, strict)
                print(f'[story] rendered {part}: {(C.STEMS_DIR / f"{part}.engine").read_text()}', flush=True)
            mix.main(['--out', str(output / 'final.wav'), *(['--voice', voice] if voice else [])])
    if video:
        from src.story.video import render_video
        render_video(manifest, output / 'final.wav', output / 'final.mp4', font_path=font)
    result = {'output': str(output), 'duration_s': manifest.timeline.duration_s,
              'bars': len(manifest.timeline.tempos), 'parts': len(story.parts), 'notes': len(score.notes),
              'midi': str(output / 'midi' / 'full.mid'), 'score': str(output / 'score.json'),
              'musicxml': str(output / 'notation' / 'full.musicxml'), 'handoff': str(output / 'HANDOFF.md')}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('spec', nargs='?', help='validated story JSON (see examples/originally-you.json)')
    parser.add_argument('--schema', action='store_true', help='print the JSON Schema generated from StorySpec')
    parser.add_argument('--out', help='empty output directory; default: unique directory under out/story/')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--fallback', action='store_true', help='use draft instruments for every part')
    modes.add_argument('--strict', action='store_true', help='require the configured real instruments')
    parser.add_argument('--midi-only', action='store_true')
    parser.add_argument('--midi', help='render an edited named type-1 MIDI instead of composing new notes')
    parser.add_argument('--sheet-preview', action='store_true', help='also engrave printable SVG/HTML scores (notation extra)')
    parser.add_argument('--video', action='store_true', help='also export a synchronized 1280x720 MP4')
    parser.add_argument('--soundfont', help='default SF2/SF3 file for parts without explicit sources')
    parser.add_argument('--voice', help='narration WAV used to duck the music')
    parser.add_argument('--font', help='font file with Chinese glyphs for the video')
