"""Fallback renderer: tiny numpy synth so the pipeline is audible with no
Surge XT / sfizz / sample packs installed.

It reads the SAME per-part MIDI files as the real renderers, so the musical
result (notes, rit, velocity jitter, CC11 hairpins) is identical — only the
timbre is a sketch.  Attack times mirror the SFZ defs (strings 0.28 s etc.).
"""
from __future__ import annotations

import zlib

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
    vib_mod = 1 + vib * np.sin(2 * np.pi * 5.3 * t) if vib else 1.0
    for dc in detune_cents:
        f = f0 * 2 ** (dc / 1200)
        for k, amp in partials:
            fk = f * k
            if fk >= sr * 0.45:
                break
            ph = phase_rng.uniform(0, 2 * np.pi) if phase_rng is not None else 0.0
            out += amp * np.sin(2 * np.pi * fk * t * vib_mod + ph)
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

    for i, nt in enumerate(notes):
        s0 = int(nt.t_on * sr)
        hold = max(1, int((nt.t_off - nt.t_on) * sr))
        v = (nt.vel / 127.0) ** 1.6
        f0 = mtof(nt.pitch)
        pan = 0.5
        if part == "pad":
            n = hold + int(1.4 * sr)
            sig = additive(f0, n, sr, SAW[:10], (-7, 0, 7), rng)
            sig = one_pole_lp(sig, 1400, sr)
            env = adsr(n, sr, 0.9, 0.5, 0.85, 1.2, hold)
            pan = 0.35 + 0.3 * ((nt.pitch % 5) / 4)
        elif part == "piano":
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
        elif part in ("violin", "viola", "cello"):
            n = hold + int(0.6 * sr)
            sig = additive(f0, n, sr, SAW[:16], (-4, 4), rng, vib=0.004)
            sig = one_pole_lp(sig, {"violin": 3200, "viola": 2400, "cello": 1600}[part], sr)
            env = adsr(n, sr, 0.28, 0.2, 0.9, 0.45, hold)   # 0.28 s soft bow attack
            pan = {"violin": 0.38, "viola": 0.58, "cello": 0.66}[part]
        elif part == "bass":
            n = hold + int(0.25 * sr)
            sig = additive(f0, n, sr, [(1, 1.0), (2, 0.25), (3, 0.08)])
            env = adsr(n, sr, 0.01, 0.2, 0.8, 0.12, hold)
        elif part == "arp":
            n = hold + int(0.35 * sr)
            t = np.arange(n) / sr
            sig = additive(f0, n, sr, SOFT[:6]) * np.exp(-t * 7)
            env = adsr(n, sr, 0.004, 0.05, 1.0, 0.15, hold)
            pan = 0.25 if i % 2 == 0 else 0.75                 # ping-pong
        elif part == "drums":
            sig, n = drum_hit(nt.pitch, sr, rng)
            env = np.ones(n)
            pan = {42: 0.62, 37: 0.45, 38: 0.48}.get(nt.pitch, 0.5)
        else:
            continue
        x = (sig * env * v).astype(np.float64)
        e = min(len(L), s0 + len(x))
        x = x[: e - s0]
        L[s0:e] += x * np.cos(pan * np.pi / 2)
        R[s0:e] += x * np.sin(pan * np.pi / 2)

    out = np.vstack([L[:N], R[:N]])
    if part in ("violin", "viola", "cello"):
        out *= cc_curve(ccs, 11, N, sr)[None, :]   # expression hairpins
    peak = np.max(np.abs(out)) or 1.0
    return (out / peak * 0.5).astype(np.float32)


def drum_hit(note: int, sr: int, rng) -> tuple[np.ndarray, int]:
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
