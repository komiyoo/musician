"""Validate named, fixed-timeline MIDI edits without discarding performance events."""
from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

import mido

from src import config as C
from src.score import write_score as W
from src.types import INSTRUMENTS, Note, StoryManifest, StorySpec, TempoMap, TimedNote


def load(path: Path, story: StorySpec, timeline: TempoMap) -> tuple[W.Score, StoryManifest, dict[str, mido.MidiTrack]]:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('edited MIDI exceeds 16 MiB')
    try:
        midi = mido.MidiFile(path)
    except (EOFError, KeyError, OSError) as error:
        raise ValueError(f'cannot read edited MIDI: {error}') from error
    if midi.type != 1 or midi.ticks_per_beat <= 0:
        raise ValueError('edited MIDI must be type 1 with a positive ticks-per-beat division')
    if sum(len(track) for track in midi.tracks) > 250_000:
        raise ValueError('edited MIDI exceeds 250,000 events')
    score = W.Score(seed=story.seed)
    parts = {part.id: part for part in story.parts}
    tracks, tempos = {}, {}
    beats_per_bar = timeline.beats_per_bar
    music_end = len(timeline.tempos) * beats_per_bar * W.TPB
    tail_end = math.ceil(timeline.beat_at(timeline.duration_s) * W.TPB)
    for track in midi.tracks:
        names = {m.name.strip() for m in track if m.type == 'track_name'}
        if len(names) > 1:
            raise ValueError('a MIDI track must have one stable track name')
        name = next(iter(names), '')
        suffix = re.search(r'\[([a-z][a-z0-9_-]{0,47})\]$', name)
        part_id = suffix.group(1) if suffix else name
        channel_events = [m for m in track if not m.is_meta]
        if any(not hasattr(m, 'channel') for m in channel_events):
            raise ValueError(f'{name}: SysEx/system messages are unsupported')
        if len({m.channel for m in channel_events}) > 1:
            raise ValueError(f'{name}: each instrument must use one channel in its own track')
        if part_id not in parts and channel_events:
            raise ValueError(f'unknown MIDI track {name!r}; keep original IDs or [id] suffixes')
        part = parts.get(part_id)
        if part is not None and part_id in tracks:
            raise ValueError(f'duplicate MIDI track: {part_id}')
        route = C.PARTS[part_id] if part is not None else None
        ins = INSTRUMENTS[part.instrument] if part is not None else None
        active, events = defaultdict(deque), []
        absolute = 0
        for message in track:
            absolute += message.time
            tick = round(absolute * W.TPB / midi.ticks_per_beat)
            if tick > tail_end + 1:
                raise ValueError(f'{name}: MIDI events extend beyond the story duration')
            if message.type == 'set_tempo':
                if message.tempo <= 0 or (tick in tempos and tempos[tick] != message.tempo):
                    raise ValueError('conflicting or invalid MIDI tempo events')
                tempos[tick] = message.tempo
            elif message.type == 'time_signature':
                if (message.numerator, message.denominator) != (4, 4):
                    raise ValueError('edited MIDI must retain the 4/4 time signature')
            if part is None or message.is_meta:
                continue
            if message.type == 'program_change':
                if tick != 0 or (route['channel'] != 9 and message.program != ins.program):
                    raise ValueError(f'{name}: program changes must match the story instrument at beat zero')
                continue
            if message.type == 'control_change' and message.control in (0, 32) and message.value != 0:
                raise ValueError(f'{name}: non-GM bank changes require configuring the story sound source')
            if message.type == 'note_on' and message.velocity > 0:
                if tick >= music_end:
                    raise ValueError(f'{name}: note starts outside the score')
                if not ins.low <= message.note <= ins.high and message.note != part.keyswitch:
                    raise ValueError(f'{name}: pitch {message.note} is outside the instrument range')
                active[message.note].append((tick, message.velocity))
            elif message.type in ('note_on', 'note_off'):
                if message.note not in active:
                    # Notation programs commonly emit zero-velocity cleanup notes at bar boundaries.
                    if message.velocity == 0:
                        events.append((tick, message.copy(channel=route['channel'])))
                        continue
                    raise ValueError(f'{name}: note-off has no matching note-on')
                start, velocity = active[message.note].popleft()
                if not active[message.note]:
                    del active[message.note]
                if tick <= start or tick > music_end:
                    raise ValueError(f'{name}: note duration is zero or extends past the last bar')
                keyswitch = message.note == part.keyswitch and start == 0 and tick <= 1 and velocity == 1
                if not keyswitch:
                    if not ins.low <= message.note <= ins.high:
                        raise ValueError(f'{name}: keyswitch is only supported at the beginning')
                    score.notes.append(Note(part_id, start / W.TPB, (tick - start) / W.TPB, message.note, velocity))
            elif message.type == 'control_change':
                score.cc(part_id, tick / W.TPB, message.control, message.value)
            events.append((tick, message.copy(channel=route['channel'])))
        if active:
            raise ValueError(f'{name}: unclosed MIDI notes')
        if part is None:
            continue
        normalized = mido.MidiTrack([
            mido.MetaMessage('track_name', name=part_id),
            mido.MetaMessage('midi_port', port=route['port']),
        ])
        if route['channel'] != 9:
            normalized.append(mido.Message('program_change', channel=route['channel'], program=ins.program))
        for control, value in ((7, 100), (11, 127)):
            if not any(t == 0 and m.type == 'control_change' and m.control == control for t, m in events):
                normalized.append(mido.Message('control_change', channel=route['channel'], control=control, value=value))
        last = 0
        for tick, message in events:
            normalized.append(message.copy(time=tick - last))
            last = tick
        normalized.append(mido.MetaMessage('end_of_track', time=max(0, tail_end - last)))
        tracks[part_id] = normalized
    missing = parts.keys() - tracks.keys()
    if missing:
        raise ValueError(f'missing MIDI tracks (retain empty named tracks for rests): {", ".join(sorted(missing))}')
    if tempos:
        tempos.setdefault(0, 500_000)  # Standard MIDI default before the first explicit tempo.
        ticks = sorted(tempos)
        checks = sorted(set(ticks) | {i * beats_per_bar * W.TPB for i in range(len(timeline.tempos))})
        for tick in checks:
            expected = timeline.tempos[min(tick // (beats_per_bar * W.TPB), len(timeline.tempos) - 1)]
            actual = tempos[ticks[bisect_right(ticks, tick) - 1]]
            if abs(60_000_000 / expected - 60_000_000 / actual) > 0.02:
                raise ValueError(f'MIDI tempo differs from story near beat {tick / W.TPB:g}; export the complete tempo map')
    score.notes.sort(key=lambda n: (n.part, n.start, n.pitch))
    notes = [TimedNote(**asdict(n), start_s=timeline.seconds_at(n.start),
                       end_s=timeline.seconds_at(n.start + n.dur)) for n in score.notes]
    return score, StoryManifest(story=story, timeline=timeline, notes=notes, ccs=score.ccs), tracks
