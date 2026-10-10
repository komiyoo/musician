"""Fallback renderer: tiny numpy synth so the pipeline is audible with no
Surge XT / sfizz / sample packs installed.

It reads the SAME per-part MIDI files as the real renderers, so the musical
result (notes, rit, velocity jitter, CC11 hairpins) is identical — only the
timbre is a sketch.  Attack times mirror the SFZ defs (strings 0.28 s etc.).
"""
from __future__ import annotations

import zlib
from bisect import bisect_right

import numpy as np

from src import config as C
from src.render.midi_io import read_part, cc_curve, total_samples

SR = C.SAMPLE_RATE


def mtof(p: float) -> float:
    return 440.0 * 2 ** ((p - 69) / 12)


def adsr(n: int, sr: int, a: float, d: float, s: float, r: float, hold: int) -> np.ndarray:
    """Envelope of total length n; note held for `hold` samples then released."""
    env = np.zeros(n, dtype=np.float32)
    na, nd, nr = max(1, int(a * sr)), max(1, int(d * sr)), max(1, int(r * sr))
    hold = max(1, min(hold, n))
    seg = np.concatenate([
        np.linspace(0, 1, na, endpoint=False),
        np.linspace(1, s, nd, endpoint=False),
        np.full(max(0, hold - na - nd), s),
    ])[:hold]
    env[:hold] = seg
    last = env[hold - 1]
    rel = last * np.exp(-np.arange(n - hold) / nr * 5)
    env[hold:] = rel
    return env


def additive(f0: float, n: int, sr: int, partials, detune_cents=(0.0,), phase_rng=None, vib=0.0):
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float64)
    # Integrate frequency modulation so vibrato depth stays constant on long notes.
    omega = 2 * np.pi * 5.3
    phase_time = t + vib * (1 - np.cos(omega * t)) / omega if vib else t
    for dc in detune_cents:
        f = f0 * 2 ** (dc / 1200)
        for k, amp in partials:
            fk = f * k
            if fk >= sr * 0.45:
                break
            ph = phase_rng.uniform(0, 2 * np.pi) if phase_rng is not None else 0.0
            out += amp * np.sin(2 * np.pi * fk * phase_time + ph)
    return out / max(1, len(detune_cents))


SAW = [(k, 1.0 / k) for k in range(1, 24)]
SOFT = [(k, 1.0 / k ** 2) for k in range(1, 12)]


def one_pole_lp(x: np.ndarray, cutoff: float, sr: int) -> np.ndarray:
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / sr)
    return lfilter([1 - a], [1, -a], x)


def render_part(part: str, sr: int = SR) -> np.ndarray:
    notes, ccs, _ = read_part(part)
    N = total_samples(sr)
    L = np.zeros(N + sr * 4)
    R = np.zeros(N + sr * 4)
    rng = np.random.default_rng(C.SEED + zlib.crc32(part.encode()) % 1000)
    bright = sorted((t, v) for t, c, v in ccs if c == 74)   # CC74 brightness (analyze mode)
    settings = C.PARTS.get(part, {})
    instrument = settings.get('instrument', part)
    family = settings.get('family', '')
    articulation = settings.get('articulation', 'sustain')
    pedal = [(t, v) for t, c, v in ccs if c == 64]
    pedal_times = [t for t, _ in pedal]
    pedal_up = [t for t, v in pedal if v < 64]

    for i, nt in enumerate(notes):
        s0 = int(nt.t_on * sr)
        hold = max(1, int((nt.t_off - nt.t_on) * sr))
        pi = bisect_right(pedal_times, nt.t_off) - 1
        if pi >= 0 and pedal[pi][1] >= 64:
            release = bisect_right(pedal_up, nt.t_off)
            off = pedal_up[release] if release < len(pedal_up) else N / sr
            hold = max(hold, int((off - nt.t_on) * sr))
        v = (nt.vel / 127.0) ** 1.6
        f0 = mtof(nt.pitch)
        pan = 0.5
        if instrument == "pad":
            n = hold + int(1.4 * sr)
            sig = additive(f0, n, sr, SAW[:10], (-7, 0, 7), rng)
            cc74 = next((v for t, v in reversed(bright) if t <= nt.t_on + 1e-6), None)
            cutoff = 1400 if cc74 is None else 500 + 3300 * cc74 / 127
            sig = one_pole_lp(sig, cutoff, sr)
            env = adsr(n, sr, 0.9, 0.5, 0.85, 1.2, hold)
            pan = 0.35 + 0.3 * ((nt.pitch % 5) / 4)
        elif instrument == "piano":
            n = hold + int(1.2 * sr)
            t = np.arange(n) / sr
            sig = np.zeros(n)
            for k in range(1, 9):
                fk = f0 * k * np.sqrt(1 + 0.0004 * k * k)
                if fk > sr * 0.45:
                    break
                sig += (1 / k ** 1.3) * np.sin(2 * np.pi * fk * t) * np.exp(-t * (1.2 + 0.9 * k) * (f0 / 220) ** 0.3)
            env = adsr(n, sr, 0.012, 0.05, 1.0, 0.35, hold)
            pan = 0.3 + 0.4 * (nt.pitch - 36) / 48
        elif instrument in ("violin", "viola", "cello"):
            n = hold + int(0.6 * sr)
            sig = additive(f0, n, sr, SAW[:16], (-4, 4), rng, vib=0.004)
            sig = one_pole_lp(sig, {"violin": 3200, "viola": 2400, "cello": 1600}[instrument], sr)
            attack = 0.012 if articulation in ('staccato', 'pizzicato', 'tremolo') else 0.28
            env = adsr(n, sr, attack, 0.2, 0.9, 0.45, hold)
            pan = {"violin": 0.38, "viola": 0.58, "cello": 0.66}[instrument]
        elif instrument == "bass":
            n = hold + int(0.25 * sr)
            sig = additive(f0, n, sr, [(1, 1.0), (2, 0.25), (3, 0.08)])
            env = adsr(n, sr, 0.01, 0.2, 0.8, 0.12, hold)
        elif instrument == "arp":
            n = hold + int(0.35 * sr)
            t = np.arange(n) / sr
            sig = additive(f0, n, sr, SOFT[:6]) * np.exp(-t * 7)
            env = adsr(n, sr, 0.004, 0.05, 1.0, 0.15, hold)
            pan = 0.25 if i % 2 == 0 else 0.75                 # ping-pong
        elif instrument == "drums" or family == 'drums':
            sig, n = drum_hit(nt.pitch, sr, rng)
            env = np.ones(n)
            pan = {42: 0.62, 37: 0.45, 38: 0.48}.get(nt.pitch, 0.5)
        elif family in ('woodwind', 'brass', 'choir', 'strings', 'mallet', 'pluck', 'timpani'):
            n = hold + int(0.6 * sr)
            t = np.arange(n) / sr
            partials = SAW[:8] if family in ('brass', 'strings') else SOFT[:5]
            if instrument == 'clarinet':
                partials = [(k, 1 / k) for k in (1, 3, 5, 7)]
            elif family == 'mallet':
                partials = [(1, 1), (2.7, 0.2), (4.1, 0.06)]
            sig = additive(f0, n, sr, partials, phase_rng=rng, vib=0.002 if family in ('choir', 'woodwind') else 0)
            attack = 0.12 if family in ('choir', 'strings') else 0.025
            if family in ('mallet', 'pluck', 'timpani'):
                sig *= np.exp(-t * (4 if family == 'pluck' else 2))
                attack = 0.005
            env = adsr(n, sr, attack, 0.1, 0.8, 0.4, hold)
        else:
            raise ValueError(f'no fallback instrument for {part}')
        x = (sig * env * v).astype(np.float64)
        e = min(len(L), s0 + len(x))
        x = x[: e - s0]
        L[s0:e] += x * np.cos(pan * np.pi / 2)
        R[s0:e] += x * np.sin(pan * np.pi / 2)

    out = np.vstack([L[:N], R[:N]])
    out *= cc_curve(ccs, 11, N, sr)[None, :]
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.5).astype(np.float32)


def drum_hit(note: int, sr: int, rng) -> tuple[np.ndarray, int]:
    if note == 49:
        n = int(2.4 * sr)
        t = np.arange(n) / sr
        noise = rng.standard_normal(n)
        return (noise - one_pole_lp(noise, 2500, sr)) * np.exp(-t * 3), n
    if note == 36:   # kick
        n = int(0.45 * sr)
        t = np.arange(n) / sr
        f = 48 + 90 * np.exp(-t * 28)
        sig = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 7)
        sig[: int(0.003 * sr)] *= np.linspace(0, 1, int(0.003 * sr))
        return sig, n
    if note == 42:   # closed hat
        n = int(0.09 * sr)
        t = np.arange(n) / sr
        noise = rng.standard_normal(n)
        hp = noise - one_pole_lp(noise, 6000, sr)
        return hp * np.exp(-t * 55) * 0.5, n
    if note == 37:   # rim / cross-stick
        n = int(0.12 * sr)
        t = np.arange(n) / sr
        sig = np.sin(2 * np.pi * 1700 * t) * np.exp(-t * 60) + 0.3 * rng.standard_normal(n) * np.exp(-t * 90)
        return sig * 0.6, n
    # snare (38 and others)
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    body = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)
    sig = 0.6 * body + 0.5 * (noise - one_pole_lp(noise, 1500, sr)) * np.exp(-t * 18)
    return sig * 0.7, n
