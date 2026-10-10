"""Exercise actual encoding and synchronization when FFmpeg and fonts exist."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from src.story.compose import compose
from src.story.video import FONT_CANDIDATES, Storyboard, render_video, spectral_history
from src.types import StorySpec


class VideoTests(unittest.TestCase):
    def test_spectral_history_comes_from_audio(self):
        sr = 8000
        t = np.arange(sr) / sr
        quiet = spectral_history(np.zeros((sr, 2)), sr, 10)
        tone = spectral_history(np.column_stack([np.sin(2 * np.pi * 440 * t)] * 2), sr, 10)
        self.assertEqual(quiet.shape, tone.shape)
        self.assertGreater(float(tone.mean()), float(quiet.mean()))
        peak = np.argmax(tone[5, :, 1])
        bands = np.geomspace(40, sr * 0.48, tone.shape[1])
        self.assertAlmostEqual(bands[peak], 440, delta=20)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe') and
                         any(Path(f).is_file() for f in FONT_CANDIDATES), 'FFmpeg and a CJK font are required')
    def test_video_has_audio_and_matches_duration(self):
        story = StorySpec.model_validate({
            'title': '灯 · Light', 'tail_s': 0.2,
            'parts': [{'id': 'piano', 'instrument': 'piano', 'role': 'arpeggio'}],
            'sections': [{'id': 'calm', 'label': '平静', 'start_s': 0, 'end_s': 2, 'bars': 1,
                          'chords': ['Dm9'], 'parts': ['piano']}],
            'captions': [{'start_s': 0, 'end_s': 2, 'text': '留一盏灯。', 'translation': 'Leave a light on.'}],
        })
        _, manifest = compose(story)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'audio.wav'
            t = np.arange(16000) / 8000
            sf.write(audio, np.column_stack([0.1 * np.sin(2 * np.pi * 440 * t)] * 2), 8000)
            board = Storyboard(manifest, audio, width=640, height=480, fps=10)
            self.assertEqual(board.frame(0.5).size, (640, 480))
            output = render_video(manifest, audio, root / 'story.mp4', width=640, height=480, fps=10)
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                                       '-of', 'json', str(output)]))
            self.assertEqual({s['codec_type'] for s in probe['streams']}, {'audio', 'video'})
            self.assertAlmostEqual(float(probe['format']['duration']), 2, delta=0.1)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                render_video(manifest, audio, output, width=640, height=480, fps=10)
            with mock.patch('src.story.video.subprocess.Popen') as spawn:
                spawn.return_value.stdin.write.side_effect = BrokenPipeError('encoder stopped')
                with self.assertRaisesRegex(RuntimeError, 'encoding failed'):
                    render_video(manifest, audio, root / 'failed.mp4', width=640, height=480, fps=10)
                spawn.return_value.kill.assert_called_once()
            self.assertEqual(list(root.glob('*.mp4')), [output])
            sf.write(audio, np.zeros((8000, 2)), 8000)
            with self.assertRaisesRegex(ValueError, 'duration does not match'):
                Storyboard(manifest, audio, width=640, height=480, fps=10)

    @unittest.skipUnless(shutil.which('ffmpeg') and any(Path(f).is_file() for f in FONT_CANDIDATES),
                         'FFmpeg and a CJK font are required')
    def test_cli_renders_a_short_story_with_video(self):
        from musician.cli import main
        story = {'title': 'Short story', 'tail_s': 0,
                 'parts': [{'id': 'piano', 'instrument': 'piano', 'role': 'arpeggio'}],
                 'sections': [{'id': 'end', 'label': '收尾', 'start_s': 0, 'end_s': 2,
                               'bars': 1, 'chords': ['Dm9'], 'parts': ['piano']}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'story.json'
            source.write_text(json.dumps(story))
            main(['story', str(source), '--out', str(root / 'result'), '--fallback', '--video'])
            self.assertTrue((root / 'result' / 'final.mp4').is_file())
            report = json.loads((root / 'result' / 'final.report.json').read_text())
            self.assertEqual(report['_master']['duration_s'], 2)


if __name__ == '__main__':
    unittest.main()
