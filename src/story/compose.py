"""Deterministic narrative composition on an exact MIDI tempo timeline."""
from __future__ import annotations

from dataclasses import asdict
from itertools import accumulate

from src.score import write_score as W
from src.types import INSTRUMENTS, StorySpec, StoryManifest, TempoMap, TimedNote


def timeline_for(story: StorySpec) -> TempoMap:
    tempos = []
    for index, section in enumerate(story.sections):
        seconds = section.end_s - section.start_s - (story.tail_s if index == len(story.sections) - 1 else 0)
        weights = [1 + (section.slowdown - 1) * i / max(1, section.bars - 1) for i in range(section.bars)]
        unit = seconds * 1_000_000 / (4 * sum(1 / w for w in weights))
        tempos.extend(round(unit / w) for w in weights)
    return TempoMap(tuple(tempos), tail_s=story.tail_s)


def compose(story: StorySpec) -> tuple[W.Score, StoryManifest]:
    timeline = timeline_for(story)
    score = W.Score(seed=story.seed)
    parts = {part.id: part for part in story.parts}
    previous = {}
    section_beat = 0

    def voice(part, pitch_class, center=None, previous_pitch=None):
        ins = INSTRUMENTS[part.instrument]
        around = ins.center if center is None else center
        candidates = [p for p in range(ins.low, ins.high + 1) if p % 12 == pitch_class]
        if not candidates:
            raise ValueError(f'{part.id}: chord pitch outside instrument range')
        if previous_pitch is not None:
            # Bound arpeggios around their home register to prevent octave drift.
            candidates = [p for p in candidates if abs(p - ins.center) <= 12] or candidates
            around = previous_pitch
        return min(candidates, key=lambda p: abs(p - around))

    def add(part, beat, duration, pitch, velocity, end):
        ins = INSTRUMENTS[part.instrument]
        if part.articulation == 'tremolo':
            for i in range(max(1, int(duration / 0.25))):
                score.add(part.id, beat + i * 0.25, min(0.2, end - beat - i * 0.25), pitch, velocity,
                          jitter=3, notation_dur=0.25)
            return
        gate = {'legato': 1.02, 'staccato': 0.42, 'pizzicato': 0.45, 'sustain': 0.96}[part.articulation]
        length = duration * gate
        if ins.family in ('woodwind', 'brass'):
            length = min(length, max(0.05, duration - 0.16))
        length = min(length, end - beat)
        if length > 0:
            score.add(part.id, beat, length, pitch, velocity, jitter=3,
                      notation_dur=min(duration, end - beat))

    for section in story.sections:
        section_end = section_beat + section.bars * 4
        theme_spans = {p: [] for p in section.parts}
        for part_id in section.parts:
            if parts[part_id].role == 'arpeggio':
                previous.pop(part_id, None)
        for use in section.themes:
            part = parts[use.part]
            start = section_beat + use.beat
            end = start + sum(n.beats for n in story.motifs[use.motif]) * use.stretch
            if any(start < b and end > a for a, b in theme_spans[use.part]):
                raise ValueError(f'overlapping themes for {use.part}')
            theme_spans[use.part].append((start, end))
            for note in story.motifs[use.motif]:
                duration = note.beats * use.stretch
                add(part, start, duration, note.pitch + use.transpose, 57 + section.energy * 35, end)
                start += duration

        for local_bar in range(section.bars):
            beat = section_beat + local_bar * 4
            chord = W.chord_pcs(section.chords[local_bar % len(section.chords)])
            end_energy = section.energy if section.end_energy is None else section.end_energy
            energy = section.energy + (end_energy - section.energy) * local_bar / max(1, section.bars - 1)
            answers = [p for p in section.parts if parts[p].role == 'counter'
                       and local_bar < section.exits.get(p, section.bars)]
            for part_id in section.parts:
                part = parts[part_id]
                ins = INSTRUMENTS[part.instrument]
                if local_bar >= section.exits.get(part_id, section.bars):
                    continue
                for off in range(4):
                    score.cc(part_id, beat + off, 11, 55 + energy * 65)
                if any(beat < b and beat + 4 > a for a, b in theme_spans[part_id]) or part.role == 'theme':
                    continue
                velocity = 43 + 43 * energy
                end = min(beat + 4.04, section_end)
                root = voice(part, chord[0]) if ins.drum_note is None else ins.drum_note

                if part.role == 'percussion':
                    if ins.family == 'timpani':
                        for off in ([0, 2, 3.5] if energy > 0.85 else [0, 2]):
                            add(part, beat + off, 0.45, root, velocity + 8, end)
                    elif ins.drum_note == 49:
                        if local_bar == 0 or local_bar % 4 == 3:
                            add(part, beat, 1, 49, velocity - 10, end)
                    else:
                        for off in ([1, 3, 3.5, 3.75] if energy > 0.85 else [1, 3]):
                            add(part, beat + off, 0.15, ins.drum_note or root, velocity - (15 if off > 3 else 0), end)
                elif part.role in ('root', 'third', 'seventh'):
                    degree = {'root': 0, 'third': 1, 'seventh': 3 if len(chord) > 3 else 1}[part.role]
                    pitch = voice(part, chord[degree], previous.get(part_id, ins.center))
                    add(part, beat, 4, pitch, velocity, end)
                    previous[part_id] = pitch
                elif part.role == 'color':
                    if local_bar % 2 == 0:
                        for off, pc in zip([1, 3], chord[4:] or [chord[2]]):
                            add(part, beat + off, 0.8, voice(part, pc), velocity - 12, end)
                elif part.role == 'counter':
                    if local_bar % 2 == 1 and part_id == answers[(local_bar // 2) % len(answers)]:
                        for off, pc in zip([2, 3], [chord[2], chord[1]]):
                            add(part, beat + off, 0.9, voice(part, pc), velocity, end)
                elif part.role == 'arpeggio':
                    step = 0.5 if energy >= 0.65 else 1
                    for i in range(int(4 / step)):
                        pitch = voice(part, chord[i % min(4, len(chord))],
                                      previous_pitch=previous.get(part_id, ins.center))
                        add(part, beat + i * step, step * 1.04, pitch, velocity + (5 if i == 0 else 0), end)
                        previous[part_id] = pitch
                    if ins.family == 'piano':
                        score.cc(part_id, beat, 64, 100)
                        score.cc(part_id, beat + 3.8, 64, 0)
                elif part.role == 'pulse':
                    accents = list(accumulate([0, *section.accents]))[:-1]
                    offsets = sorted(set(accents + ([i / 2 for i in range(8)] if energy > 0.8 else [])))
                    for i, off in enumerate(offsets):
                        pc = chord[(i % 2) * 2]
                        add(part, beat + off, 0.45, voice(part, pc), velocity + (10 if off in accents else -12), end)
                elif part.role == 'pad':
                    pitch = voice(part, chord[3] if len(chord) > 3 else chord[1])
                    old = previous.get(part_id, pitch)
                    if old != pitch:
                        add(part, beat, 0.5, old, velocity - 8, end)
                    add(part, beat + (0.5 if old != pitch else 0), 3.5 if old != pitch else 4, pitch, velocity - 8, end)
                    previous[part_id] = pitch
        section_beat = section_end

    notes = [TimedNote(**asdict(n), start_s=timeline.seconds_at(round(n.start * W.TPB) / W.TPB),
                       end_s=timeline.seconds_at(round((n.start + n.dur) * W.TPB) / W.TPB)) for n in score.notes]
    manifest = StoryManifest(story=story, timeline=timeline, notes=notes, ccs=score.ccs)
    return score, manifest
