"""Step 3a — 响度对齐：each stem -> its target loudness before any FX.

Uses pyloudnorm (ITU-R BS.1770 integrated LUFS).  `--mode dbfs` switches to a
plain RMS-dBFS match for very short / sparse stems.
"""
from __future__ import annotations

import numpy as np
import pyloudnorm as pyln

from src import config as C


def measure_lufs(audio: np.ndarray, sr: int) -> float:
    """audio: (channels, samples)."""
    meter = pyln.Meter(sr)
    return float(meter.integrated_loudness(audio.T.astype(np.float64)))


def measure_rms_dbfs(audio: np.ndarray) -> float:
    rms = np.sqrt(np.mean(np.square(audio[np.abs(audio) > 1e-5]))) if np.any(np.abs(audio) > 1e-5) else 0.0
    return 20 * np.log10(rms) if rms > 0 else -np.inf


def align(audio: np.ndarray, sr: int, target: float, mode: str = "lufs") -> tuple[np.ndarray, float, float]:
    """Return (gained audio, measured, applied_gain_db)."""
    measured = measure_lufs(audio, sr) if mode == "lufs" else measure_rms_dbfs(audio)
    if not np.isfinite(measured):
        return audio, measured, 0.0
    gain_db = target - measured
    return (audio * 10 ** (gain_db / 20)).astype(np.float32), measured, gain_db


def target_for(part: str) -> float:
    return C.LOUDNESS_TARGETS.get(part, -26.0)
