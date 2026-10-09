"""Code metrics / diff hunks → note events (Score), D minor · 100 BPM.

Reuses the main score's building blocks (Score, chord tables, pad voicings) so the
result plugs straight into midi_io → render_all → mix with the same part names,
loudness targets and FX chains.

Mapping (repo mode) — inspired by repo2music:
  file            → a section of 1–4 bars (∝ LOC); files play in path order
  function        → a piano motif inside its file's section (slot ∝ function LOC)
  nesting depth   → register of that motif (deeper = higher: A3 … E5)
  complexity      → motif length (more notes) · arp rhythm density (♩→♪→16th)
                    · harmonic tension (Gm / A chords) · ≥10: chromatic dissonance
  imports         → percussion hits at the start of the section
                    (stdlib = rim, third-party = kick, local/relative = snare)
  control density → hi-hat subdivision (none / ♩ / ♪ / 16th) + bass drive
  comment ratio   → pad velocity + brightness (CC74, used by the fallback pad filter)
  repeated code   → violin echo of the function's motif (an octave up, delayed)
  classes         → sustained cello roots
Mapping (diff mode) — CodeSonify-inspired:
  each hunk       → a ½- or 1-bar motif: added lines ascend (piano/arp/violin by file type),
                    removed lines descend (cello); pitch steps come from a hash of the line text
  new file in diff→ kick; every hunk → hat; deletion-heavy bar → Gm
"""
from __future__ import annotations

import math
import zlib
from pathlib import Path

from src import config as C
from src.score.write_score import PAD_VOICINGS, Score, chord_pcs, nearest

CYCLE = ["Dm", "Bb", "F", "C"]                      # i – VI – III – VII
SCALE = [0, 2, 3, 5, 7, 8, 10]                      # D natural minor, relative to D
D4 = 62
KICK, RIM, SNARE, HAT = 36, 37, 38, 42


def h32(s: str) -> int:
    return zlib.crc32(s.encode("utf-8", "replace"))


def to_degree(pitch: int) -> int:
    """MIDI pitch → nearest D-minor scale degree index (D4 = 0)."""
    best = min(range(-21, 29), key=lambda d: abs(from_degree(d) - pitch))
    return best


def from_degree(deg: int) -> int:
    o, i = divmod(deg, 7)
    return D4 + 12 * o + SCALE[i]


def q(x: float, grid: float = 0.25) -> float:
    return round(x / grid) * grid


class Timeline:
    """Bar-indexed chord plan + helpers shared by both modes."""

    def __init__(self):
        self.chords: list[str] = []   # index 0 = bar 1

    def add_bar(self, sym: str) -> int:
        self.chords.append(sym)
        return len(self.chords)

    def chord_at(self, beat: float) -> str:
        return self.chords[min(int(beat // C.BEATS_PER_BAR), len(self.chords) - 1)]


def _pad_bar(s: Score, b0: float, sym: str, vel: float, bright: float | None = None):
    for p in PAD_VOICINGS[sym]:
        s.add("pad", b0, 4.0, p, vel, jitter=3)
    if bright is not None:
        s.cc("pad", b0, 74, bright)


def _bass_bar(s: Score, b0: float, sym: str, drive: int, vel: float = 78):
    root = nearest(chord_pcs(sym)[0], 38)
    pattern = {0: [(0, 4.0, 0)], 1: [(0, 2.0, 0), (2, 2.0, -6)],
               2: [(0, 1.5, 4), (1.5, 0.5, -10), (2, 1.5, 0), (3.5, 0.5, -12)]}[drive]
    for off, d, dv in pattern:
        s.add("bass", b0 + off, d * 0.95, root, vel + dv)


def _cadence(s: Score, tl: Timeline):
    """Two bars: A (dominant) → Dm, rolled final chord. Matches the main score's ending."""
    b = tl.add_bar("A")
    b0 = (b - 1) * 4
    _pad_bar(s, b0, "A", 54)
    _bass_bar(s, b0, "A", 1, 66)
    for i, p in enumerate([45, 57, 61, 64]):
        s.add("piano", b0 + i, 1.05, p, 56 - 2 * i)
    s.add("drums", b0, 0.25, KICK, 70)
    b = tl.add_bar("Dm")
    b0 = (b - 1) * 4
    _pad_bar(s, b0, "Dm", 50)
    s.add("bass", b0, 4.0, 38, 56)
    for i, p in enumerate([38, 50, 57, 62, 65, 69]):
        s.add("piano", b0 + i * 0.08, 4.0 - i * 0.08, p, 52, jitter=3)
    s.add("drums", b0, 0.25, KICK, 58)


def rit_for(n_bars: int) -> dict[int, float]:
    return {n_bars - 1: 92.0, n_bars: 80.0}


def _strings_init(s: Score, parts=("violin", "viola", "cello")):
    for p in parts:
        s.cc(p, 0, 11, 112)   # part_track starts strings at CC11=40; analyze mode wants a flat level


# =========================================================================== repo mode
def _allocate_bars(files, budget: int) -> list[tuple[object, int]]:
    if len(files) > budget:   # too many files: keep the biggest, preserve path order
        keep = set(id(f) for f in sorted(files, key=lambda f: -f.loc)[:budget])
        files = [f for f in files if id(f) in keep]
    alloc = [[f, max(1, min(4, round(f.loc / 80)))] for f in files]
    while sum(a[1] for a in alloc) > budget:
        max(alloc, key=lambda a: (a[1], a[0].loc))[1] -= 1
    total = sum(a[1] for a in alloc)
    if 0 < total < 6:                      # tiny repo: stretch so there is something to hear
        k = math.ceil(6 / total)
        for a in alloc:
            a[1] = min(4, a[1] * k)
    return [(f, n) for f, n in alloc]


def _file_cx(fm) -> float:
    if fm.functions:
        return sum(f.complexity for f in fm.functions) / len(fm.functions)
    return 1 + fm.complexity / max(1, fm.loc / 20)


def _motif(s: Score, tl: Timeline, f, start: float, dur: float, echo: bool):
    n = max(1, min(8, 1 + (f.complexity - 1) // 2, int(dur / 0.25)))
    step = max(0.25, math.floor(dur / n / 0.25) * 0.25)
    n = min(n, int(dur / step))
    depth = min(f.max_depth, 4)
    base = 57 + 5 * depth                                 # A3, D4, G4, C5, E5 — deeper = higher
    sym = tl.chord_at(start)
    pcs = chord_pcs(sym)
    hv = h32(f.name)
    first = nearest(pcs[hv % 3], base)
    deg = to_degree(first)
    notes = []
    for i in range(n):
        if i == 0:
            p = first
        else:
            bits = (hv >> (2 * i)) & 3
            deg += (1, 2, -1, -2)[bits] if depth else (1, -1, 2, -1)[bits]
            p = from_degree(deg)
        if f.complexity >= 10 and i == n - 2:
            p += 1                                         # chromatic rub = "this code is knotty"
        p = max(45, min(88, p))
        vel = 60 + 4 * depth + (8 if i == 0 else 0)
        d = step * (0.5 if f.is_async else 0.95)
        notes.append((start + i * step, d, p, vel))
        s.add("piano", start + i * step, d, p, vel)
    if echo:                                               # repeated snippet → canon echo
        delay = min(1.0, max(0.5, step))
        for t, d, p, v in notes:
            if t + delay < start + dur + 1.0:
                s.add("violin", t + delay, max(d, step), min(93, p + 12), v - 14)


def compose_repo(files, max_bars: int = 48):
    s = Score(C.SEED)
    _strings_init(s)
    tl = Timeline()
    plan = []
    # bar 1: intro (pad alone + soft quarter hats) — room for a voice-over first line
    tl.add_bar("Dm")
    _pad_bar(s, 0, "Dm", 52)
    for i in range(4):
        s.add("drums", i, 0.125, HAT, 40, jitter=3)

    body = max(1, max_bars - 3)
    for fm, k in _allocate_bars(files, body):
        cx = _file_cx(fm)
        cr = min(1.0, fm.comment_ratio / 0.35)
        first_bar = len(tl.chords) + 1
        for j in range(k):
            sym = CYCLE[(first_bar - 1 + j) % 4]
            if cx >= 10 and j == k - 1:
                sym = "A"
            elif cx >= 6 and j % 2 == 1:
                sym = "Gm"
            if fm.dup_ratio > 0.05 and j == k - 1:
                sym = "Asus"
            if fm.parse_error:
                sym = "A"
            tl.add_bar(sym)
        b0 = (first_bar - 1) * 4
        beats = 4 * k

        # harmony bed
        drive = 0 if fm.control_density < 0.8 else 1 if fm.control_density < 1.5 else 2
        for j in range(k):
            sym = tl.chords[first_bar - 1 + j]
            _pad_bar(s, b0 + 4 * j, sym, 42 + 46 * cr, 25 + 100 * cr)
            _bass_bar(s, b0 + 4 * j, sym, drive)
            if fm.classes:
                s.add("cello", b0 + 4 * j, 4.0, nearest(chord_pcs(sym)[0], 46), 56 + 4 * min(fm.classes, 4))

        # arp: complexity → rhythm density (+ dissonance)
        grid = 1.0 if cx < 3 else 0.5 if cx < 6 else 0.25
        dissonant = cx >= 10 or bool(fm.parse_error)
        for j in range(k):
            sym = tl.chords[first_bar - 1 + j]
            tones = sorted({nearest(x, 74) for x in chord_pcs(sym)})
            seq = tones + [tones[0] + 12] + tones[::-1][1:]
            for i in range(int(4 / grid)):
                p = seq[i % len(seq)]
                if dissonant and i % 4 == 3:
                    p += 1 if (i // 4) % 2 == 0 else 6     # minor 2nd / tritone
                s.add("arp", b0 + 4 * j + i * grid, grid * 0.9, p, 54 + (6 if i % 4 == 0 else 0), jitter=4)

        # drums: section downbeat, imports → hits, control density → hats
        s.add("drums", b0, 0.25, KICK, 88)
        for j in range(1, k):
            s.add("drums", b0 + 4 * j, 0.25, KICK, 72)
        kinds = {"stdlib": RIM, "third": KICK, "local": SNARE}
        for i, kind in enumerate(fm.import_kinds[: max(1, beats * 2 - 2)][:16]):
            s.add("drums", b0 + 0.5 + i * 0.5, 0.2, kinds.get(kind, RIM), 64 + (10 if kind == "third" else 0), jitter=5)
        sub = 0 if fm.control_density < 0.5 else 1 if fm.control_density < 1.0 else 2 if fm.control_density < 1.6 else 4
        if sub:
            for i in range(beats * sub):
                s.add("drums", b0 + i / sub, 0.1, HAT, 56 if i % sub == 0 else 44, jitter=6)

        # melody: one motif per function
        funcs = list(fm.functions)
        max_slots = int(beats / 0.5)
        if len(funcs) > max_slots:
            keep = set(id(f) for f in sorted(funcs, key=lambda f: -f.loc)[:max_slots])
            funcs = [f for f in funcs if id(f) in keep]
        if not funcs:   # script / data module: chord-tone quarter notes, register by depth
            for i in range(beats):
                sym = tl.chord_at(b0 + i)
                s.add("piano", b0 + i, 0.9, nearest(chord_pcs(sym)[i % 3], 62 + 3 * min(fm.max_depth, 4)), 58)
        else:
            total = sum(max(1, f.loc) for f in funcs)
            starts, acc = [], 0.0
            for f in funcs:
                st = q(beats * acc / total, 0.5)
                if starts and st <= starts[-1]:
                    st = starts[-1] + 0.5
                starts.append(st)
                acc += max(1, f.loc)
            for i, f in enumerate(funcs):
                if starts[i] >= beats:
                    break
                end = starts[i + 1] if i + 1 < len(starts) else beats
                _motif(s, tl, f, b0 + starts[i], min(end, beats) - starts[i], f.has_dup)

        plan.append({"file": fm.path, "bars": [first_bar, first_bar + k - 1],
                     "chords": tl.chords[first_bar - 1:first_bar - 1 + k],
                     "functions": len(funcs), "mean_complexity": round(cx, 2), "arp_grid": grid,
                     "hat_subdiv": sub, "imports": len(fm.imports), "comment_ratio": fm.comment_ratio,
                     "dup_ratio": fm.dup_ratio})
    _cadence(s, tl)
    return s, tl, plan


# =========================================================================== diff mode
ADD_PART = {".py": "piano", ".js": "arp", ".ts": "arp", ".tsx": "arp", ".jsx": "arp", ".mjs": "arp",
            ".md": "violin", ".rst": "violin", ".txt": "violin"}
ADD_BASE = {"piano": 62, "arp": 74, "violin": 74}


def compose_diff(hunks, max_bars: int = 32):
    s = Score(C.SEED)
    _strings_init(s)
    tl = Timeline()
    budget_beats = 4 * max(1, max_bars - 3)
    slots, t = [], 4.0                                   # bar 1 = intro
    for h in hunks:
        beats = 2.0 if h.size <= 8 else 4.0
        if t - 4.0 + beats > budget_beats:
            break
        slots.append((h, t, beats))
        t += beats
    body_bars = max(1, math.ceil((t - 4.0) / 4))
    # chords: cycle, deletion-heavy bars → Gm
    tl.add_bar("Dm")
    _pad_bar(s, 0, "Dm", 50)
    for j in range(body_bars):
        bar_beat = 4.0 + 4 * j
        hs = [h for h, st, b in slots if bar_beat <= st < bar_beat + 4]
        add = sum(len(h.added) for h in hs)
        rem = sum(len(h.removed) for h in hs)
        sym = "Gm" if rem > add else CYCLE[(j + 1) % 4]
        tl.add_bar(sym)
        _pad_bar(s, bar_beat, sym, 50)
        _bass_bar(s, bar_beat, sym, 1 if len(hs) < 2 else 2, 72)
        s.add("drums", bar_beat, 0.25, KICK, 70)

    prev_path = None
    for h, st, beats in slots:
        if h.path != prev_path:
            s.add("drums", st, 0.25, KICK, 90)
            prev_path = h.path
        s.add("drums", st, 0.1, HAT, 58)
        if any("import" in ln for ln in h.added + h.removed):
            s.add("drums", st + 0.5, 0.2, RIM, 64)
        part = ADD_PART.get(Path(h.path).suffix, "piano")
        n = min(8, max(2, h.size))
        step = max(0.25, math.floor(beats / n / 0.25) * 0.25)
        n = min(n, int(beats / step))
        n_add = round(n * len(h.added) / h.size)
        n_rem = n - n_add
        sym = tl.chord_at(st)
        pcs = chord_pcs(sym)
        # added lines: ascending from a chord tone
        deg = to_degree(nearest(pcs[h32(h.path) % 3], ADD_BASE[part]))
        lines = h.added or [""]
        for i in range(n_add):
            ln = lines[i * len(lines) // max(1, n_add)]
            if i:
                deg += 1 + h32(ln) % 2
            vel = 60 + min(28, len(ln.strip()) // 3)
            s.add(part, st + i * step, step * 0.9, max(45, min(93, from_degree(deg))), vel)
        # removed lines: descending, low strings
        deg = to_degree(nearest(pcs[0], 50))
        lines = h.removed or [""]
        for i in range(n_rem):
            ln = lines[i * len(lines) // max(1, n_rem)]
            if i:
                deg -= 1 + h32(ln) % 2
            s.add("cello", st + (n_add + i) * step, step * 0.95, max(36, from_degree(deg)), 62 + min(20, len(ln.strip()) // 4))
    _cadence(s, tl)
    plan = [{"file": h.path, "beat": st, "beats": beats, "added": len(h.added), "removed": len(h.removed),
             "context": h.context} for h, st, beats in slots]
    return s, tl, plan, len(hunks) - len(slots)
