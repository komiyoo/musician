"""Story invariants: chronology, motifs, registers and routing."""
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mido
import numpy as np
import soundfile as sf

from pydantic import ValidationError

from src.types import StorySpec, INSTRUMENTS

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'originally-you.json'


class StoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.story = StorySpec.model_validate_json(EXAMPLE.read_text())

    def test_invalid_spec_is_rejected(self):
        data = self.story.model_dump()
        for change in [lambda d: d['parts'][0].update(id='../escape'),
                       lambda d: d['sections'][1].update(start_s=26),
                       lambda d: d['sections'][0].update(parts=['missing']),
                       lambda d: d['sections'][0].update(energy=float('nan')),
                       lambda d: d['sections'][0]['themes'][0].update(transpose=48),
                       lambda d: d['captions'][1].update(start_s=0)]:
            altered = copy.deepcopy(data)
            change(altered)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                StorySpec.model_validate(altered)

    def test_example_sections_match_picture_times(self):
        from src.story.compose import timeline_for
        timeline = timeline_for(self.story)
        self.assertEqual(sum(s.bars for s in self.story.sections), 58)
        self.assertEqual(len(self.story.parts), 23)
        self.assertEqual(len(self.story.captions), 19)
        bar = 0
        for section in self.story.sections:
            self.assertAlmostEqual(timeline.seconds_at(bar * 4), section.start_s, delta=0.001)
            bar += section.bars
        self.assertAlmostEqual(timeline.duration_s, 186, delta=0.001)
        for beat in [0, 6.5, 36, 97.25, 220, 232]:
            self.assertAlmostEqual(timeline.beat_at(timeline.seconds_at(beat)), beat)

    def test_theme_returns_and_all_parts_stay_in_range(self):
        from src.story.compose import compose
        score, manifest = compose(self.story)
        self.assertEqual({n.part for n in score.notes}, {p.id for p in self.story.parts})
        parts = {p.id: INSTRUMENTS[p.instrument] for p in self.story.parts}
        for note in manifest.notes:
            instrument = parts[note.part]
            self.assertTrue(instrument.low <= note.pitch <= instrument.high, note)
            self.assertTrue(0 <= note.start_s < note.end_s <= manifest.timeline.duration_s, note)
        motif = [n.pitch for n in self.story.motifs['friend']]
        opening = sorted((n for n in score.notes if n.part == 'clarinet' and n.start < 8), key=lambda n: n.start)
        self.assertEqual([n.pitch for n in opening], motif)
        boss_start = sum(s.bars for s in self.story.sections[:5]) * 4
        boss = sorted((n for n in score.notes if n.part == 'horn' and boss_start <= n.start < boss_start + 16), key=lambda n: n.start)
        self.assertEqual([n.pitch for n in boss], [p - 12 for p in motif])
        self.assertEqual([n.start - boss_start for n in boss], [0, 2, 4, 8, 10, 12])
        ending_start = sum(s.bars for s in self.story.sections[:6]) * 4
        ending = sorted((n for n in score.notes if n.part == 'clarinet' and ending_start <= n.start < ending_start + 8), key=lambda n: n.start)
        self.assertEqual([n.pitch for n in ending], motif)
        again, _ = compose(self.story)
        self.assertEqual(score.notes, again.notes)
        self.assertTrue(any(c.cc == 64 and c.value == 0 for c in score.ccs))

    def test_arpeggios_keep_a_connected_register(self):
        from src.story.compose import compose
        score, _ = compose(self.story)
        opening = sorted((n for n in score.notes if n.part == 'piano' and n.start < 8), key=lambda n: n.start)
        jumps = [abs(a.pitch - b.pitch) for a, b in zip(opening, opening[1:])]
        self.assertLessEqual(max(jumps), 12)
        self.assertLessEqual(max(n.pitch for n in opening) - min(n.pitch for n in opening), 24)
        for part in ('piano', 'harp'):
            notes = sorted((n for n in score.notes if n.part == part), key=lambda n: n.start)
            connected = [abs(a.pitch - b.pitch) for a, b in zip(notes, notes[1:])
                         if b.start - a.start <= 4.01]
            self.assertLessEqual(max(connected), 12, part)

    def test_countermelodies_take_turns(self):
        from src.story.compose import compose
        story = StorySpec.model_validate({
            'title': 'Woodwind answers', 'tail_s': 0,
            'parts': [{'id': name, 'instrument': name, 'role': 'counter'}
                      for name in ('flute', 'oboe', 'sax')],
            'sections': [{'id': 'answers', 'label': 'Answers', 'start_s': 0, 'end_s': 12,
                          'bars': 6, 'chords': ['F'], 'parts': ['flute', 'oboe', 'sax']}],
        })
        score, _ = compose(story)
        self.assertEqual({n.part for n in score.notes}, {'flute', 'oboe', 'sax'})
        for beat in {n.start for n in score.notes}:
            self.assertEqual(sum(n.start == beat for n in score.notes), 1)
        story.sections[0].exits = {'oboe': 1}
        score, _ = compose(story)
        self.assertEqual({n.part for n in score.notes}, {'flute', 'sax'})
        self.assertEqual(len(score.notes), 6)

    def test_midi_ports_and_config_are_isolated(self):
        from src import config as C
        from src.score import write_score as W
        from src.story.compose import compose
        from src.story.pipeline import profile
        original_parts, original_duration = C.PARTS, C.total_seconds()
        score, manifest = compose(self.story)
        with tempfile.TemporaryDirectory() as directory:
            with profile(self.story, manifest.timeline, Path(directory), EXAMPLE):
                routes = [(v['port'], v['channel']) for v in C.PARTS.values()]
                self.assertEqual(len(routes), len(set(routes)))
                per_part, full = W.midi_files(score)
            self.assertIs(C.PARTS, original_parts)
            self.assertEqual(C.total_seconds(), original_duration)
            with self.assertRaises(RuntimeError):
                with profile(self.story, manifest.timeline, Path(directory), EXAMPLE):
                    raise RuntimeError('renderer failed')
            self.assertIs(C.PARTS, original_parts)
        midi = mido.MidiFile(file=io.BytesIO(full))
        self.assertEqual(len(midi.tracks), 24)
        self.assertAlmostEqual(midi.length, 186, delta=0.01)
        for data in per_part.values():
            single = mido.MidiFile(file=io.BytesIO(data))
            self.assertEqual([m.port for t in single.tracks for m in t if m.type == 'midi_port'], [0])

    def test_each_draft_instrument_produces_audio(self):
        from src import config as C
        from src.render.fallback_synth import render_part
        from src.render.midi_io import NoteEv
        from src.story.compose import timeline_for
        from src.story.pipeline import profile
        with tempfile.TemporaryDirectory() as directory, profile(self.story, timeline_for(self.story), Path(directory), EXAMPLE):
            for part in self.story.parts:
                ins = INSTRUMENTS[part.instrument]
                with self.subTest(part=part.id), \
                        mock.patch('src.render.fallback_synth.read_part', return_value=([NoteEv(0, 0.2, ins.center, 80)], [], [])), \
                        mock.patch('src.render.fallback_synth.total_samples', return_value=4000):
                    audio = render_part(part.id, sr=8000)
                    self.assertEqual(audio.shape, (2, 4000))
                    self.assertTrue(np.isfinite(audio).all())
                    self.assertGreater(np.max(np.abs(audio)), 0.01)

    def test_strict_missing_instrument_and_existing_output_fail(self):
        from src.render.sfizz_render import render
        from src.story.pipeline import run
        with mock.patch('src.render.sfizz_render.sfizz_available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not on PATH'):
                render('piano', strict=True)
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'keep.txt'
            marker.write_text('user content')
            with self.assertRaisesRegex(ValueError, 'empty directory'):
                run(EXAMPLE, directory, midi_only=True)
            self.assertEqual(marker.read_text(), 'user content')

    def test_fluidsynth_receives_soundfont_and_midi(self):
        from src import config as C
        from src.render.fluidsynth_render import render
        from src.story.compose import timeline_for
        from src.story.pipeline import profile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            font = root / 'test.sf2'
            font.write_bytes(b'fixture')
            with profile(self.story, timeline_for(self.story), root, EXAMPLE, str(font)):
                def render_audio(command, **kwargs):
                    sf.write(command[command.index('-F') + 1], np.ones((800, 2)) * 0.1, 8000)
                with mock.patch('src.render.fluidsynth_render.shutil.which', return_value='/bin/fluidsynth'), \
                        mock.patch('src.render.fluidsynth_render.subprocess.run', side_effect=render_audio) as call, \
                        mock.patch('src.render.midi_io.total_samples', return_value=800):
                    path = render('clarinet', sr=8000, strict=True)
                self.assertEqual(call.call_args.args[0][-2:], [str(font.resolve()), str(root / 'midi' / 'clarinet.mid')])
                self.assertEqual(sf.info(path).frames, 800)
                self.assertEqual((C.STEMS_DIR / 'clarinet.engine').read_text(), 'fluidsynth')


if __name__ == '__main__':
    unittest.main()
