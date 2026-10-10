"""Regression checks for shared timing, MIDI metadata and renderer controls."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mido
import numpy as np

from src import config as C
from src.feel import compose as CP
from src.feel.spec import build_spec
from src.render import surge_render
from src.render.fallback_synth import additive


class FallbackSynthTests(unittest.TestCase):
    def test_vibrato_depth_stays_bounded_on_long_notes(self):
        sr, fundamental = 12000, 220
        for duration in (1, 5):
            for depth in (0, 0.002, 0.004):
                with self.subTest(duration=duration, depth=depth):
                    signal = additive(fundamental, duration * sr, sr, [(1, 1)], vib=depth)
                    indices = np.flatnonzero((signal[:-1] <= 0) & (signal[1:] > 0))
                    crossings = indices - signal[indices] / (signal[indices + 1] - signal[indices])
                    frequency = sr / np.diff(crossings)
                    # Measure pitch from zero crossings, independently of the synthesis formula.
                    self.assertLess(np.max(np.abs(frequency / fundamental - 1)), depth + 0.0003)
                    if depth:
                        self.assertGreater(np.ptp(frequency), fundamental * depth)


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.config = mock.patch.dict(C.__dict__)
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_reported_duration_includes_ritardando(self):
        spec = build_spec('', preview=False)
        CP.apply_config(spec)
        self.assertAlmostEqual(spec.duration_s, C.total_seconds(), delta=0.051)

    def test_explicit_long_duration_is_not_truncated(self):
        spec = build_spec('', preview=False, duration_s=186)
        CP.apply_config(spec)
        self.assertAlmostEqual(C.total_seconds(), 186, delta=0.001)
        self.assertGreater(spec.bars, 64)

    def test_major_key_is_written_to_midi(self):
        spec = build_spec('', {'mood': 100})
        _, data = CP.midi_files(spec, CP.compose(spec))
        mid = mido.MidiFile(file=io.BytesIO(data))
        self.assertEqual([m.key for tr in mid.tracks for m in tr if m.type == 'key_signature'], ['F'])

    def test_surgepy_forwards_expression_and_bend(self):
        synth = mock.Mock()
        synth.getFactoryDataPath.return_value = '/missing'
        synth.getBlockSize.return_value = 4
        synth.getOutput.return_value = np.zeros((2, 4), dtype=np.float32)
        messages = [mido.Message('control_change', channel=2, control=11, value=66),
                    mido.Message('pitchwheel', channel=2, pitch=512),
                    mido.Message('note_on', channel=2, note=60, velocity=70),
                    mido.Message('note_off', channel=2, note=60, time=0.001)]
        with mock.patch.dict('sys.modules', {'surgepy': SimpleNamespace(createSurge=lambda sr: synth)}), \
                mock.patch.object(surge_render, 'resolve_patch', return_value=None), \
                mock.patch.object(surge_render, 'read_part', return_value=([], [], messages)), \
                mock.patch.object(surge_render, 'total_samples', return_value=4):
            surge_render.render_surgepy('pad', 1000)
        synth.channelController.assert_called_once_with(2, 11, 66)
        synth.pitchBend.assert_called_once_with(2, 512)
        synth.playNote.assert_called_once_with(2, 60, 70)
        synth.releaseNote.assert_called_once_with(2, 60, 64)

    def test_cache_changes_with_patch_and_actual_backend(self):
        from src.render.render_all import render_signature
        with tempfile.TemporaryDirectory() as directory:
            patch = Path(directory) / 'pad.fxp'
            patch.write_bytes(b'first')
            with mock.patch.object(surge_render, 'resolve_patch', return_value=patch), \
                    mock.patch.object(surge_render, 'available_backend', return_value='fallback'):
                before = render_signature('pad')
                patch.write_bytes(b'second patch')
                self.assertNotEqual(before, render_signature('pad'))
                before = render_signature('pad')
            with mock.patch.object(surge_render, 'resolve_patch', return_value=patch), \
                    mock.patch.object(surge_render, 'available_backend', return_value='pedalboard'):
                self.assertNotEqual(before, render_signature('pad'))

    def test_failed_native_audio_is_retried_instead_of_cached(self):
        from src.feel.render import cached_stem
        with tempfile.TemporaryDirectory() as directory:
            stem = Path(directory) / 'pad.wav'
            stem.write_bytes(b'cached audio')
            self.assertFalse(cached_stem(stem, 'pedalboard'))
            stem.with_suffix('.engine').write_text('fallback')
            self.assertTrue(cached_stem(stem, 'fallback'))
            self.assertFalse(cached_stem(stem, 'pedalboard'))
            stem.with_suffix('.engine').write_text('surge-pedalboard')
            self.assertTrue(cached_stem(stem, 'pedalboard'))


if __name__ == '__main__':
    unittest.main()
