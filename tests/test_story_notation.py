"""Read the delivered score, round-trip an edit, and reject destructive timeline changes."""
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

import mido
import numpy as np
import soundfile as sf

from src.score import write_score as W
from src.story.compose import compose
from src.story.midi_import import load
from src.story.notation import export
from src.story.pipeline import profile, run
from src.types import StorySpec


class NotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.story = StorySpec.model_validate({
            'title': '交接测试', 'tail_s': 0.5,
            'parts': [{'id': 'clarinet', 'instrument': 'clarinet', 'role': 'theme'},
                      {'id': 'piano', 'instrument': 'piano', 'role': 'arpeggio'},
                      {'id': 'snare', 'instrument': 'snare', 'role': 'percussion'}],
            'motifs': {'theme': [{'pitch': p, 'beats': b} for p, b in [(62, 3), (65, 3), (69, 2)]]},
            'sections': [{'id': 'scene', 'label': 'Scene', 'start_s': 0, 'end_s': 4.5, 'bars': 2,
                          'chords': ['Dm9', 'A7'], 'parts': ['clarinet', 'piano', 'snare'],
                          'themes': [{'motif': 'theme', 'part': 'clarinet'}]}],
        })
        self.source = self.root / 'source.json'
        self.source.write_text(self.story.model_dump_json(), encoding='utf-8')
        self.score, self.manifest = compose(self.story)

    def midi(self):
        with profile(self.story, self.manifest.timeline, self.root, self.source):
            return mido.MidiFile(file=io.BytesIO(W.midi_files(self.score)[1]))

    def save(self, midi):
        path = self.root / 'edit.mid'
        midi.save(path)
        return path

    def read(self, midi):
        path = self.save(midi)
        with profile(self.story, self.manifest.timeline, self.root, self.source):
            return load(path, self.story, self.manifest.timeline)

    def test_readable_score_has_concert_pitches_ties_rests_and_percussion(self):
        from music21 import converter
        with profile(self.story, self.manifest.timeline, self.root, self.source):
            export(self.manifest, self.root)
        root = ET.parse(self.root / 'notation/full.musicxml').getroot()
        self.assertEqual(len(root.findall('part-list/score-part')), 3)
        self.assertEqual([len(p.findall('measure')) for p in root.findall('part')], [2, 2, 2])
        clarinet = root.findall('part')[0]
        self.assertIsNone(clarinet.find('.//transpose'))
        self.assertEqual(clarinet.findtext('.//pitch/step'), 'D')
        self.assertEqual({t.get('type') for t in clarinet.findall('.//tie')}, {'start', 'stop'})
        snare = root.findall('part')[2]
        self.assertIsNotNone(snare.find('.//unpitched'))
        self.assertEqual(root.findall('part-list/score-part')[2].findtext('.//midi-unpitched'), '39')
        self.assertIsNotNone(snare.find('.//rest'))
        for r in snare.findall('.//note'):
            if r.find('rest') is not None:
                self.assertNotEqual(r.get('print-object'), 'no')
        parsed = converter.parse(self.root / 'notation/full.musicxml')
        self.assertEqual(parsed.parts[0].highestTime, 8)
        pitches = [n.pitch.midi for n in parsed.parts[0].recurse().notes if n.isNote]
        self.assertEqual(pitches, [62, 65, 65, 69])
        self.assertTrue((self.root / 'instruments.csv').read_text('utf-8-sig').startswith('track_id,'))

    def test_empty_bars_show_full_measure_rests(self):
        self.manifest.notes = [n for n in self.manifest.notes if n.part != 'clarinet']
        with profile(self.story, self.manifest.timeline, self.root, self.source):
            export(self.manifest, self.root)
        measures = ET.parse(self.root / 'notation/clarinet.musicxml').findall('part/measure')
        self.assertEqual(len(measures), 2)
        for measure in measures:
            rests = [n for n in measure.findall('note') if n.find('rest') is not None]
            self.assertEqual(len(rests), 1)
            self.assertEqual(rests[0].find('rest').get('measure'), 'yes')
            self.assertNotEqual(rests[0].get('print-object'), 'no')

    def test_edit_preserves_notes_controllers_bends_and_port_routing(self):
        midi = self.midi()
        track = midi.tracks[1]
        track[0].name = 'Clarinet [clarinet]'
        for message in track:
            if message.type in ('note_on', 'note_off') and message.note == 62:
                message.note = 64
                if message.type == 'note_on':
                    message.velocity = 88
        track.insert(2, mido.Message('pitchwheel', channel=0, pitch=1000))
        track.insert(3, mido.Message('control_change', channel=0, control=64, value=90))
        for track in midi.tracks:
            for message in track:
                message.time *= 3
        midi.ticks_per_beat *= 3
        score, manifest, tracks = self.read(midi)
        first = next(n for n in score.notes if n.part == 'clarinet')
        self.assertEqual((first.pitch, first.vel, first.start), (64, 88, 0))
        self.assertAlmostEqual(manifest.notes[0].end_s, round(2.84 * W.TPB) / W.TPB / 2)
        with profile(self.story, self.manifest.timeline, self.root, self.source):
            singles, full = W.midi_files(score, tracks=tracks)
        messages = list(mido.MidiFile(file=io.BytesIO(singles['clarinet'])))
        self.assertTrue(any(m.type == 'pitchwheel' and m.pitch == 1000 for m in messages))
        self.assertTrue(any(m.type == 'control_change' and m.control == 64 and m.value == 90 for m in messages))
        self.assertEqual([m.port for m in messages if m.type == 'midi_port'], [0])
        self.assertAlmostEqual(mido.MidiFile(file=io.BytesIO(full)).length, 4.5)

    def test_round_trip_current_piece_retains_tick_accurate_performance(self):
        source = Path(__file__).resolve().parents[1] / 'examples/originally-you.json'
        story = StorySpec.model_validate_json(source.read_text())
        score, manifest = compose(story)
        with profile(story, manifest.timeline, self.root, source):
            original = W.midi_files(score)[1]
            path = self.root / 'original.mid'
            path.write_bytes(original)
            again, new_manifest, tracks = load(path, story, manifest.timeline)
        expected = sorted((n.part, round(n.start * W.TPB), round((n.start + n.dur) * W.TPB), n.pitch, n.vel)
                          for n in score.notes)
        actual = sorted((n.part, round(n.start * W.TPB), round((n.start + n.dur) * W.TPB), n.pitch, n.vel)
                        for n in again.notes)
        self.assertEqual(expected, actual)
        self.assertEqual(len(tracks), 23)
        self.assertAlmostEqual(new_manifest.timeline.duration_s, 186, delta=0.001)

    def test_bad_edits_are_rejected(self):
        baseline = self.midi()
        for case in ('tempo', 'meter', 'unknown', 'missing', 'duplicate', 'unclosed', 'unmatched', 'multi_channel', 'range', 'system'):
            midi = deepcopy(baseline)
            with self.subTest(case=case):
                if case == 'tempo':
                    next(m for m in midi.tracks[0] if m.type == 'set_tempo').tempo = 600_000
                elif case == 'meter':
                    next(m for m in midi.tracks[0] if m.type == 'time_signature').numerator = 3
                elif case == 'unknown':
                    midi.tracks[1][0].name = 'unknown'
                elif case == 'missing':
                    midi.tracks.pop()
                elif case == 'duplicate':
                    midi.tracks.append(deepcopy(midi.tracks[1]))
                elif case == 'unclosed':
                    off = next(m for m in midi.tracks[1] if m.type == 'note_off')
                    index = midi.tracks[1].index(off)
                    midi.tracks[1].pop(index)
                    midi.tracks[1][index].time += off.time
                elif case == 'unmatched':
                    midi.tracks[1].insert(2, mido.Message('note_off', note=90, time=1))
                elif case == 'multi_channel':
                    midi.tracks[1].insert(2, mido.Message('pitchwheel', channel=3))
                elif case == 'range':
                    midi.tracks[1].insert(2, mido.Message('note_on', note=1, velocity=100))
                else:
                    midi.tracks[1].insert(2, mido.Message('sysex', data=[1, 2]))
                with self.assertRaises(ValueError):
                    self.read(midi)

    def test_missing_tempo_uses_story_and_empty_parts_are_allowed(self):
        midi = self.midi()
        midi.tracks[0] = mido.MidiTrack(m for m in midi.tracks[0] if m.type != 'set_tempo')
        midi.tracks[-1] = mido.MidiTrack([mido.MetaMessage('track_name', name='snare')])
        score, _, tracks = self.read(midi)
        self.assertNotIn('snare', {n.part for n in score.notes})
        self.assertIn('snare', tracks)

    def test_cli_import_renders_edited_audio_and_new_score_without_composing(self):
        midi = self.midi()
        for message in midi.tracks[1]:
            if message.type in ('note_on', 'note_off') and message.note == 62:
                message.note = 64
        path = self.save(midi)
        output = self.root / 'rendered'
        with mock.patch('src.story.pipeline.compose', side_effect=AssertionError('must use edited notes')):
            result = run(self.source, output, midi=path, fallback=True)
        audio, sr = sf.read(output / 'final.wav')
        self.assertEqual(audio.shape, (round(4.5 * sr), 2))
        self.assertGreater(np.max(np.abs(audio)), 0.01)
        manifest = json.loads((output / 'score.json').read_text())
        self.assertEqual(next(n['pitch'] for n in manifest['notes'] if n['part'] == 'clarinet'), 64)
        self.assertTrue(Path(result['musicxml']).is_file())
        self.assertTrue((output / 'story.json').is_file())
        with self.assertRaisesRegex(ValueError, 'empty directory'):
            run(self.source, output, midi=path, midi_only=True)


if __name__ == '__main__':
    unittest.main()
