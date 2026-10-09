"""Shared helpers: read a per-part MIDI file into timed events (seconds)."""
from __future__ import annotations

from dataclasses import dataclass

import mido
import numpy as np
import soundfile as sf

from src import config as C


@dataclass
class NoteEv:
    t_on: float
    t_off: float
    pitch: int
    vel: int


def read_part(part: str):
    """Return (notes, cc_events, messages_with_abs_seconds) for midi/<part>.mid."""
    mid = mido.MidiFile(C.MIDI_DIR / f"{part}.mid")
    t = 0.0
    open_notes: dict[int, list[tuple[float, int]]] = {}
    notes: list[NoteEv] = []
    ccs: list[tuple[float, int, int]] = []
    msgs: list[mido.Message] = []
    for msg in mid:  # iterating a MidiFile yields delta times in seconds (tempo-aware)
        t += msg.time
        if msg.is_meta:
            continue
        msgs.append(msg.copy(time=t))
        if msg.type == "note_on" and msg.velocity > 0:
            open_notes.setdefault(msg.note, []).append((t, msg.velocity))
        elif msg.type in ("note_off", "note_on"):
            if open_notes.get(msg.note):
                t_on, v = open_notes[msg.note].pop(0)
                notes.append(NoteEv(t_on, t, msg.note, v))
        elif msg.type == "control_change":
            ccs.append((t, msg.control, msg.value))
    notes.sort(key=lambda n: n.t_on)
    return notes, ccs, msgs


def cc_curve(ccs, control: int, n_samples: int, sr: int, default: int = 127) -> np.ndarray:
    """Piecewise-linear 0..1 curve for a controller (e.g. CC11 expression)."""
    pts = [(t, v) for t, c, v in ccs if c == control]
    if not pts:
        return np.full(n_samples, default / 127.0, dtype=np.float32)
    ts = np.array([p[0] for p in pts]) * sr
    vs = np.array([p[1] for p in pts]) / 127.0
    return np.interp(np.arange(n_samples), ts, vs).astype(np.float32)


def total_samples(sr: int = C.SAMPLE_RATE) -> int:
    return int(round(C.total_seconds() * sr))


def fit_length(audio: np.ndarray, n: int) -> np.ndarray:
    """audio shaped (channels, samples) -> exactly n samples, stereo."""
    if audio.ndim == 1:
        audio = audio[None, :]
    if audio.shape[0] == 1:
        audio = np.vstack([audio, audio])
    audio = audio[:2]
    if audio.shape[1] >= n:
        return audio[:, :n].astype(np.float32)
    out = np.zeros((2, n), dtype=np.float32)
    out[:, : audio.shape[1]] = audio
    return out


def write_stem(part: str, audio: np.ndarray, sr: int = C.SAMPLE_RATE, tag: str = "") -> str:
    C.STEMS_DIR.mkdir(parents=True, exist_ok=True)
    path = C.STEMS_DIR / f"{part}.wav"
    sf.write(path, fit_length(audio, total_samples(sr)).T, sr, subtype="FLOAT")
    (C.STEMS_DIR / f"{part}.engine").write_text(tag or "unknown")
    return str(path)
