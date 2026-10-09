"""Step 3b — 每轨效果链 (Spotify pedalboard), applied AFTER loudness alignment.

Design rule for a narration bed: carve a "voice pocket" around 1.5-4 kHz on
every melodic part, keep lows clean (HPF), and push depth with reverb so the
music sits *behind* the speaker.
"""
from __future__ import annotations

from pedalboard import (
    Chorus, Compressor, Delay, Gain, HighpassFilter, HighShelfFilter,
    LowpassFilter, PeakFilter, Pedalboard, Reverb,
)

from src import config as C

BEAT = 60.0 / C.BPM            # 0.6 s
DOTTED_EIGHTH = BEAT * 0.75     # 0.45 s


def voice_pocket(db: float = -3.0, hz: float = 2500.0, q: float = 0.8) -> PeakFilter:
    return PeakFilter(cutoff_frequency_hz=hz, gain_db=db, q=q)


def chain(part: str) -> Pedalboard:
    if part == "pad":
        return Pedalboard([
            HighpassFilter(140), LowpassFilter(7000), voice_pocket(-4.0),
            Chorus(rate_hz=0.25, depth=0.2, centre_delay_ms=9, feedback=0.0, mix=0.35),
            Reverb(room_size=0.85, damping=0.6, wet_level=0.38, dry_level=0.7, width=1.0),
        ])
    if part == "piano":
        return Pedalboard([
            HighpassFilter(90), voice_pocket(-3.0, 3000), HighShelfFilter(8000, gain_db=-2.0),
            Compressor(threshold_db=-24, ratio=2.0, attack_ms=15, release_ms=180),
            Reverb(room_size=0.55, damping=0.5, wet_level=0.22, dry_level=0.85, width=0.9),
        ])
    if part == "violin":
        return Pedalboard([
            HighpassFilter(220), voice_pocket(-3.5, 2800, 1.0),
            Reverb(room_size=0.75, damping=0.55, wet_level=0.32, dry_level=0.8, width=0.8),
        ])
    if part == "viola":
        return Pedalboard([
            HighpassFilter(160), voice_pocket(-3.0, 2200),
            Reverb(room_size=0.75, damping=0.6, wet_level=0.3, dry_level=0.8, width=0.8),
        ])
    if part == "cello":
        return Pedalboard([
            HighpassFilter(45), LowpassFilter(5000), PeakFilter(300, -2.0, 1.0),
            Reverb(room_size=0.7, damping=0.65, wet_level=0.25, dry_level=0.85, width=0.6),
        ])
    if part == "bass":
        return Pedalboard([
            HighpassFilter(35), LowpassFilter(900),
            Compressor(threshold_db=-22, ratio=3.0, attack_ms=10, release_ms=120),
        ])
    if part == "arp":
        return Pedalboard([
            HighpassFilter(320), voice_pocket(-5.0, 2600), HighShelfFilter(9000, gain_db=-3.0),
            Delay(delay_seconds=DOTTED_EIGHTH, feedback=0.28, mix=0.2),
            Reverb(room_size=0.7, damping=0.5, wet_level=0.3, dry_level=0.75, width=1.0),
        ])
    if part == "drums":
        return Pedalboard([
            HighpassFilter(30), voice_pocket(-2.0, 3500),
            Compressor(threshold_db=-20, ratio=2.5, attack_ms=20, release_ms=150),
            Reverb(room_size=0.35, damping=0.7, wet_level=0.12, dry_level=0.95, width=0.7),
        ])
    return Pedalboard([Gain(0.0)])


def master_chain() -> Pedalboard:
    return Pedalboard([
        HighpassFilter(28),
        voice_pocket(-2.5, 2500, 0.7),    # leave room for 中文旁白 intelligibility
        Compressor(threshold_db=-20, ratio=1.8, attack_ms=30, release_ms=250),
    ])


def limiter(audio, sr: int, ceiling_db: float = C.MASTER_CEILING_DB,
            lookahead_ms: float = 5.0, release_ms: float = 120.0):
    """Transparent look-ahead peak limiter (numpy).

    pedalboard.Limiter (JUCE) adds make-up gain (~+4.7 dB), which would undo
    the master loudness target, so we use a plain gain-computer instead.
    """
    import numpy as np
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    from scipy.signal import lfilter

    ceiling = 10 ** (ceiling_db / 20)
    peak = np.max(np.abs(audio), axis=0)
    g = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9))
    la = max(1, int(lookahead_ms * sr / 1000))
    held = minimum_filter1d(g, size=2 * la + 1)          # every sample within ±la of a peak is reduced
    att = uniform_filter1d(held, size=la | 1)             # smooth attack ramp (still <= gain needed at peak)
    a = np.exp(-1 / (release_ms * sr / 1000))
    rel, _ = lfilter([1 - a], [1, -a], att, zi=[a * att[0]])                # slow release
    g = np.minimum(att, rel)
    out = audio * g[None, :]
    return np.clip(out, -ceiling, ceiling).astype(np.float32)
