"""Piano-roll film and spectral history, both timed from the delivered audio."""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.types import INSTRUMENTS, StoryManifest

FONT_CANDIDATES = [
    '/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    'C:/Windows/Fonts/msyh.ttc',
]
COLORS = [(80, 206, 210), (242, 182, 105), (171, 158, 239), (119, 194, 153),
          (224, 131, 140), (112, 174, 232)]


def resolve_font(path: str | None = None) -> str:
    candidates = [path] if path else FONT_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError('A Chinese font is required for the bilingual video; use --font or install Noto Sans CJK')


def spectral_history(audio: np.ndarray, sr: int, fps: int, bins: int = 256) -> np.ndarray:
    """One windowed spectrum per video frame, in dB relative to full scale."""
    mono = audio.mean(axis=1)
    size = 2048
    window = np.hanning(size)
    padded = np.pad(mono, (size // 2, size))
    frequencies = np.fft.rfftfreq(size, 1 / sr)
    bands = np.geomspace(40, min(16000, sr * 0.48), bins)
    rows = []
    for frame in range(math.ceil(len(mono) / sr * fps)):
        start = round(frame * sr / fps)
        magnitude = np.abs(np.fft.rfft(padded[start:start + size] * window)) / (window.sum() / 2)
        db = 20 * np.log10(np.maximum(np.interp(bands, frequencies, magnitude), 1e-8))
        rows.append(np.clip((db + 75) / 65, 0, 1))
    intensity = np.asarray(rows, dtype=np.float32)
    return np.stack([8 + 235 * intensity ** 2, 14 + 195 * intensity, 24 + 180 * np.sqrt(intensity)], axis=-1).astype('uint8')


class Storyboard:
    def __init__(self, manifest: StoryManifest, audio_path: Path, *, font_path=None, width=1280, height=720, fps=30):
        if width < 640 or height < 480 or width % 2 or height % 2 or not 1 <= fps <= 60:
            raise ValueError('video needs even dimensions >= 640x480 and 1..60 fps')
        audio, sr = sf.read(audio_path, dtype='float32', always_2d=True)
        self.duration = len(audio) / sr
        if abs(self.duration - manifest.timeline.duration_s) > 1 / sr + 1e-6:
            raise ValueError('audio duration does not match the score timeline')
        self.manifest, self.width, self.height, self.fps = manifest, width, height, fps
        self.max_frequency = min(16000, sr * 0.48)
        self.spectrum = spectral_history(audio, sr, fps)
        font = resolve_font(font_path)
        scale = width / 1280
        self.title_font = ImageFont.truetype(font, max(18, round(26 * scale)))
        self.caption_font = ImageFont.truetype(font, max(14, round(21 * scale)))
        self.font = ImageFont.truetype(font, max(10, round(13 * scale)))
        self.small = ImageFont.truetype(font, max(9, round(11 * scale)))
        self.left, self.right = int(width * 0.21), width - 24
        self.top, self.bottom = 112, height - 206
        self.row = (self.bottom - self.top) / len(manifest.story.parts)
        self.parts = {p.id: (i, p) for i, p in enumerate(manifest.story.parts)}
        self.window = 18.0
        yy, xx = np.mgrid[0:height, 0:width]
        glow = np.exp(-((xx - width * 0.6) ** 2 / (width * 0.7) ** 2 + (yy - height * 0.25) ** 2 / (height * 0.5) ** 2))
        self.background = Image.fromarray(np.stack([6 + 7 * glow, 11 + 11 * glow, 19 + 15 * glow], axis=-1).astype('uint8'))
        draw = ImageDraw.Draw(self.background)
        draw.text((24, 20), manifest.story.title, font=self.title_font, fill=(232, 238, 240))
        draw.text((24, 64), f'{len(manifest.timeline.tempos)} BARS  /  {len(self.parts)} PARTS  /  MIDI SCORE',
                  font=self.small, fill=(138, 157, 166))
        for part_id, (i, part) in self.parts.items():
            ins = INSTRUMENTS[part.instrument]
            y = self.top + i * self.row
            color = COLORS[i % len(COLORS)]
            draw.line((24, y + self.row, self.right, y + self.row), fill=(30, 43, 52))
            draw.rectangle((24, y + 4, 27, y + self.row - 3), fill=color)
            zh = ins.zh + ('·短弓' if part.articulation == 'staccato' else '')
            draw.text((34, y + 1), zh, font=self.font, fill=(204, 213, 218))
            draw.text((self.left * 0.5, y + 1), ins.en, font=self.small, fill=(142, 160, 171))

    def frame(self, seconds: float) -> Image.Image:
        image = self.background.copy()
        draw = ImageDraw.Draw(image)
        story = self.manifest.story
        section = next((s for s in story.sections if s.start_s <= seconds < s.end_s), story.sections[-1])
        total = round(self.duration)
        clock = f'{int(seconds) // 60}:{int(seconds) % 60:02d} / {total // 60}:{total % 60:02d}'
        draw.text((self.width - 180, 26), clock, font=self.font, fill=(183, 203, 213))
        draw.text((self.left, 64), section.label, font=self.font, fill=(188, 219, 220))
        for s in story.sections:
            x = 24 + (self.width - 48) * s.start_s / self.duration
            end = 24 + (self.width - 48) * min(seconds, s.end_s) / self.duration
            draw.line((x, 95, 24 + (self.width - 48) * s.end_s / self.duration, 95), fill=(47, 63, 73), width=3)
            if end > x:
                draw.line((x, 95, end, 95), fill=(86, 192, 193), width=3)
            draw.line((x, 92, x, 99), fill=(143, 169, 178))
        start = seconds - 4
        pixels = (self.right - self.left) / self.window
        for tick in range(max(0, math.ceil(start)), math.ceil(start + self.window)):
            x = self.left + (tick - start) * pixels
            draw.line((x, self.top, x, self.bottom), fill=(28, 40, 51))
        halo = Image.new('RGB', image.size)
        lights = ImageDraw.Draw(halo)
        blocks = []
        for note in self.manifest.notes:
            if note.end_s < start or note.start_s > start + self.window:
                continue
            i, part = self.parts[note.part]
            ins = INSTRUMENTS[part.instrument]
            x0 = max(self.left, self.left + (note.start_s - start) * pixels)
            x1 = min(self.right, self.left + (note.end_s - start) * pixels)
            if x1 <= x0:
                continue
            y = self.top + i * self.row + 3 + (1 - (note.pitch - ins.low) / max(1, ins.high - ins.low)) * max(0, self.row - 9)
            active = note.start_s <= seconds < note.end_s
            intensity = 0.45 + 0.55 * note.vel / 127
            color = tuple(round(c * intensity) for c in COLORS[i % len(COLORS)])
            rectangle = (x0, y, max(x0 + 1, x1), y + max(3, self.row * 0.28))
            if active:
                lights.rectangle(rectangle, fill=color)
            blocks.append((rectangle, color, active))
        image = Image.blend(image, Image.fromarray(np.minimum(np.asarray(image, dtype='uint16') +
                            np.asarray(halo.filter(ImageFilter.GaussianBlur(5))), 255).astype('uint8')), 0.65)
        draw = ImageDraw.Draw(image)
        for rectangle, color, active in blocks:
            draw.rounded_rectangle(rectangle, radius=2, fill=color, outline=(228, 249, 246) if active else None)
        playhead = self.left + 4 * pixels
        draw.line((playhead, self.top, playhead, self.bottom), fill=(230, 243, 239), width=1)
        caption = next((c for c in story.captions if c.start_s <= seconds < c.end_s), None)
        if caption:
            for offset, text, font in [(16, caption.text, self.caption_font), (49, caption.translation, self.font)]:
                # The contract bounds captions; shrink long lines to preserve every character.
                box = font.getbbox(text)
                text_image = Image.new('RGBA', (max(1, box[2] + 4), max(1, box[3] + 4)))
                ImageDraw.Draw(text_image).text((0, 0), text, font=font, fill=(227, 235, 235, 255))
                if text_image.width > self.width - 64:
                    text_image = text_image.resize((self.width - 64, text_image.height), Image.Resampling.LANCZOS)
                image.paste(text_image, ((self.width - text_image.width) // 2, int(self.bottom + offset)), text_image)
        frame = min(len(self.spectrum) - 1, max(0, round(seconds * self.fps)))
        history = self.spectrum[max(0, frame - 89):frame + 1]
        padded = np.pad(history, ((90 - len(history), 0), (0, 0), (0, 0)))
        waterfall = Image.fromarray(padded).resize((self.width - 48, 76), Image.Resampling.BILINEAR)
        image.paste(waterfall, (24, self.height - 98))
        ImageDraw.Draw(image).text((24, self.height - 20), f'AUDIO SPECTRUM  ·  40 Hz → {self.max_frequency / 1000:g} kHz',
                                  font=self.small, fill=(117, 147, 161))
        return image


def render_video(manifest: StoryManifest, audio_path: Path, output: Path, *, font_path=None,
                 width=1280, height=720, fps=30) -> Path:
    if output.exists():
        raise ValueError(f'video already exists: {output}')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('video export requires ffmpeg')
    board = Storyboard(manifest, audio_path, font_path=font_path, width=width, height=height, fps=fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.mp4', dir=output.parent, delete=False) as file:
        temporary = Path(file.name)
    command = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0', '-i', str(audio_path),
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '192k', '-t', f'{board.duration:.9f}', '-movflags', '+faststart', str(temporary)]
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
            try:
                for frame in range(math.ceil(board.duration * fps)):
                    process.stdin.write(board.frame(frame / fps).tobytes())
                process.stdin.close()
                code = process.wait(timeout=60)
                if code:
                    raise RuntimeError(f'ffmpeg exited with code {code}')
            except BaseException as error:
                process.kill()
                process.wait(timeout=10)
                with suppress(BrokenPipeError):
                    process.stdin.close()
                errors.seek(0)
                message = errors.read().decode(errors='replace')[-2000:]
                if isinstance(error, (BrokenPipeError, RuntimeError)):
                    raise RuntimeError(f'video encoding failed: {message or error}') from error
                raise
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'[story] video: {output}', flush=True)
    return output
