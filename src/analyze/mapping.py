"""Code metrics / diff hunks → note events (Score), D minor · 100 BPM.

Reuses the main score's building blocks (Score, chord tables, pad voicings) so the
result plugs straight into midi_io → render_all → mix with the same part names,
loudness targets and FX chains.

Mapping (repo mode) — inspired by repo2music:
  file            → a section of 1–4 bars (∝ LOC); files play in path order
  function        → a piano motif inside its file's section (slot ∝ function LOC)
  nesting depth   → register of that motif (deeper = higher: A3 … E5)
  complexity      → motif length (more notes) · arp rhythm density (♩→♪→16th)
  imports         → percussion hits at the start of the section
                    (stdlib = rim, third-party = kick, local/relative = snare)
  control density → hi-hat subdivision (none / ♩ / ♪ / 16th) + bass drive
  comment ratio   → pad velocity + brightness (CC74, used by the fallback pad filter)
  repeated code   → violin echo of the function's motif (an octave up, delayed)
  classes         → sustained cello roots
Consonance layer — codephon-inspired:
  tension t∈[0,1] = 0.45·complexity + 0.35·nesting + 0.20·(1 − comment ratio)  (each normalized)
  every motif note gets a viola partner below it:
    t < 0.35  consonant  diatonic 3rds / 6ths / 5ths in D minor, legato, soft
    t < 0.65  mixed      mostly 3rds, every 3rd dyad a 4th or 7th
    t ≥ 0.65  dissonant  minor 2nds / tritones, staccato, +velocity ("harsh")
  t also picks the chords (Gm on even bars from 0.45, dominant A from 0.70),
  the arp's chromatic substitutions, motif / hat velocities and articulation.
Hot-file priority + caps:
  heat = Σcomplexity × (1 + git churn) (churn = lines added+removed in `git log`).
  When files don't fit --max-bars the hottest are kept (path order preserved), bars are
  scaled to the budget, and per-section function slots go to the hottest functions.
  The result is always ≤ max_bars (intro + body + 2-bar cadence).
Mapping (diff mode) — CodeSonify-inspired:
  each hunk       → a ½- or 1-bar motif: added lines rise (piano/arp/violin by file type),
                    removed lines fall (cello); the contour slope follows the hunk's
                    complexity delta (decision tokens + indentation, added − removed):
                    more complexity = steeper climb, higher start, dissonant dyad;
                    simplification = gentle rise that settles with a consonant 3rd
  new file in diff→ kick; every hunk → hat; deletion-heavy bar → Gm
  heat track      → (optional) viola ostinato whose subdivision / velocity / CC11 follow
                    churn density (changed lines per bar)
  over budget     → keep the hottest hunks by size × (1 + complexity), in diff order
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter
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
        self.dropped: list[str] = []  # files / hunks that did not fit --max-bars
        self.heat: list[float] = []   # diff mode: normalized churn density per body bar

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


# =========================================================================== consonance
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def tension(cx: float, depth: float, comment_ratio: float) -> float:
    """codephon-style tension score: 0 = calm / consonant, 1 = knotty / dissonant."""
    c = clamp01((cx - 2) / 10)                # cx 2 → 0 · cx 12 → 1
    d = clamp01((depth - 1) / 4)              # depth ≤1 → 0 · depth 5 → 1
    k = clamp01(1 - comment_ratio / 0.30)     # ≥30 % comments → 0
    return round(0.45 * c + 0.35 * d + 0.20 * k, 3)


def color(t: float) -> str:
    return "consonant" if t < 0.35 else "mixed" if t < 0.65 else "dissonant"


def dyad_partner(p: int, t: float, i: int, hv: int) -> int:
    """Second voice under melody note p, chosen by tension t (codephon consonance layer)."""
    deg = to_degree(p)
    sel = (hv >> (3 * i)) & 7
    if t < 0.35:                                   # 3rds / 6ths / 5ths, all diatonic
        return from_degree(deg - (2, 2, 5, 4, 2, 5, 4, 2)[sel])
    if t < 0.65:
        if i % 3 == 2:                             # 4th / 7th: open, a bit unresolved
            return from_degree(deg - (3, 6)[sel & 1])
        return from_degree(deg - 2)
    return p - (1, 6, 1, 6, 11, 1, 6, 2)[sel]       # m2 / tritone / M7 / M2 rubs


# =========================================================================== repo mode
def _file_cx(fm) -> float:
    if fm.functions:
        return sum(f.complexity for f in fm.functions) / len(fm.functions)
    return 1 + fm.complexity / max(1, fm.loc / 20)


def file_heat(fm) -> float:
    """complexity × churn priority (churn 0 outside git → plain complexity)."""
    cx_total = max(1, fm.complexity, sum(f.complexity for f in fm.functions))
    return float(cx_total * (1 + fm.churn))


def _allocate_bars(files, budget: int) -> tuple[list[tuple[object, int]], list[object]]:
    """Fit files into `budget` bars. Returns ([(file, bars)], dropped files)."""
    budget = max(1, budget)
    dropped = []
    if len(files) > budget:   # too many files: keep the hottest, preserve path order
        keep = set(id(f) for f in sorted(files, key=lambda f: (-file_heat(f), -f.loc))[:budget])
        dropped = [f for f in files if id(f) not in keep]
        files = [f for f in files if id(f) in keep]
    alloc = [[f, max(1, min(4, round(f.loc / 80)))] for f in files]
    total = sum(a[1] for a in alloc)
    if total > budget:            # scale ∝ budget, then trim the coldest long sections
        k = budget / total
        for a in alloc:
            a[1] = max(1, min(4, round(a[1] * k)))
        while sum(a[1] for a in alloc) > budget:
            max(alloc, key=lambda a: (a[1], -file_heat(a[0])))[1] -= 1
    total = sum(a[1] for a in alloc)
    target = min(6, budget)
    if 0 < total < target:        # tiny repo: stretch so there is something to hear
        k = math.ceil(target / total)
        for a in alloc:
            a[1] = min(4, a[1] * k)
        while sum(a[1] for a in alloc) > budget:
            max(alloc, key=lambda a: a[1])[1] -= 1
    return [(f, n) for f, n in alloc], dropped


def _motif(s: Score, tl: Timeline, f, start: float, dur: float, echo: bool, t_file: float,
           comment_ratio: float) -> str:
    n = max(1, min(8, 1 + (f.complexity - 1) // 2, int(dur / 0.25)))
    step = max(0.25, math.floor(dur / n / 0.25) * 0.25)
    n = min(n, int(dur / step))
    depth = min(f.max_depth, 4)
    t = round(0.5 * t_file + 0.5 * tension(f.complexity, f.max_depth, comment_ratio), 3)
    base = 57 + 5 * depth                                 # A3, D4, G4, C5, E5 — deeper = higher
    sym = tl.chord_at(start)
    pcs = chord_pcs(sym)
    hv = h32(f.name)
    first = nearest(pcs[hv % 3], base)
    deg = to_degree(first)
    notes = []
    legato = 0.95 - 0.45 * t                              # knotty code → clipped, staccato
    for i in range(n):
        if i == 0:
            p = first
        else:
            bits = (hv >> (2 * i)) & 3
            deg += (1, 2, -1, -2)[bits] if depth else (1, -1, 2, -1)[bits]
            p = from_degree(deg)
        if t >= 0.65 and i == n - 2:
            p += 1                                         # chromatic rub = "this code is knotty"
        p = max(45, min(88, p))
        vel = 56 + 4 * depth + 18 * t + (8 if i == 0 else 0)
        d = step * (0.5 if f.is_async else legato)
        notes.append((start + i * step, d, p, vel))
        s.add("piano", start + i * step, d, p, vel)
        # consonance layer: viola partner a 3rd/5th (calm) … m2/tritone (tense) below
        q2 = max(48, min(81, dyad_partner(p, t, i, hv)))
        if q2 != p:
            s.add("viola", start + i * step, max(0.25, d), q2,
                  48 + 10 * t + (10 if t >= 0.65 else 0), jitter=4)
    if echo:                                               # repeated snippet → canon echo
        delay = min(1.0, max(0.5, step))
        for t0, d, p, v in notes:
            if t0 + delay < start + dur + 1.0:
                s.add("violin", t0 + delay, max(d, step), min(93, p + 12), v - 14)
    return color(t)


def compose_repo(files, max_bars: int = 48):
    max_bars = max(4, int(max_bars))
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
    alloc, dropped = _allocate_bars(files, body)
    hmax = max([file_heat(f) for f, _ in alloc] or [1.0])
    for fm, k in alloc:
        cx = _file_cx(fm)
        cr = min(1.0, fm.comment_ratio / 0.35)
        t = tension(cx, fm.max_depth, fm.comment_ratio)
        first_bar = len(tl.chords) + 1
        for j in range(k):
            sym = CYCLE[(first_bar - 1 + j) % 4]
            if t >= 0.70 and j == k - 1:
                sym = "A"
            elif t >= 0.45 and j % 2 == 1:
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
            _bass_bar(s, b0 + 4 * j, sym, drive, 72 + 14 * t)
            if fm.classes:
                s.add("cello", b0 + 4 * j, 4.0, nearest(chord_pcs(sym)[0], 46), 56 + 4 * min(fm.classes, 4))

        # arp: complexity → rhythm density; tension → chromatic substitutions
        grid = 1.0 if cx < 3 else 0.5 if cx < 6 else 0.25
        tt = 1.0 if fm.parse_error else t
        every = 0 if tt < 0.45 else 4 if tt < 0.7 else 2       # how often a note is bent off-chord
        for j in range(k):
            sym = tl.chords[first_bar - 1 + j]
            tones = sorted({nearest(x, 74) for x in chord_pcs(sym)})
            seq = tones + [tones[0] + 12] + tones[::-1][1:]
            for i in range(int(4 / grid)):
                p = seq[i % len(seq)]
                if every and i % every == every - 1:
                    p += 1 if (i // every) % 2 == 0 else 6     # minor 2nd / tritone
                s.add("arp", b0 + 4 * j + i * grid, grid * (0.9 - 0.3 * tt), p,
                      50 + 14 * tt + (6 if i % 4 == 0 else 0), jitter=4)

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
                s.add("drums", b0 + i / sub, 0.1, HAT, (56 if i % sub == 0 else 44) + 12 * t, jitter=6)

        # melody: one motif per function (hottest functions win the slots)
        funcs = list(fm.functions)
        max_slots = int(beats / 0.5)
        if len(funcs) > max_slots:
            keep = set(id(f) for f in sorted(funcs, key=lambda f: -(f.complexity * max(1, f.loc)))[:max_slots])
            funcs = [f for f in funcs if id(f) in keep]
        colors = Counter()
        if not funcs:   # script / data module: chord-tone quarter notes, register by depth
            for i in range(beats):
                sym = tl.chord_at(b0 + i)
                p = nearest(chord_pcs(sym)[i % 3], 62 + 3 * min(fm.max_depth, 4))
                s.add("piano", b0 + i, 0.9, p, 58)
                s.add("viola", b0 + i, 0.9, max(48, dyad_partner(p, t, i, h32(fm.path))), 48, jitter=4)
            colors[color(t)] += 1
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
                colors[_motif(s, tl, f, b0 + starts[i], min(end, beats) - starts[i], f.has_dup, t,
                              fm.comment_ratio)] += 1

        plan.append({"file": fm.path, "bars": [first_bar, first_bar + k - 1],
                     "chords": tl.chords[first_bar - 1:first_bar - 1 + k],
                     "functions": len(funcs), "mean_complexity": round(cx, 2), "arp_grid": grid,
                     "hat_subdiv": sub, "imports": len(fm.imports), "comment_ratio": fm.comment_ratio,
                     "dup_ratio": fm.dup_ratio, "tension": t, "color": color(t),
                     "motif_colors": dict(colors), "churn": fm.churn,
                     "heat": round(file_heat(fm) / hmax, 3)})
    _cadence(s, tl)
    assert len(tl.chords) <= max_bars, (len(tl.chords), max_bars)
    tl.dropped = [f.path for f in dropped]
    return s, tl, plan


# =========================================================================== diff mode
ADD_PART = {".py": "piano", ".js": "arp", ".ts": "arp", ".tsx": "arp", ".jsx": "arp", ".mjs": "arp",
            ".md": "violin", ".rst": "violin", ".txt": "violin"}
ADD_BASE = {"piano": 62, "arp": 74, "violin": 74}
DECISION_RE = re.compile(r"\b(if|elif|else if|for|while|except|catch|case|and|or)\b|&&|\|\||\?")


CODE_EXT = {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
            ".h", ".cc", ".cpp", ".hpp", ".rb", ".sh", ".kt", ".swift", ".cs", ".php"}


def lines_cx(lines: list[str], path: str = ".py") -> float:
    """Rough complexity of a bag of changed lines: decision tokens + deepest indentation.

    Prose / config files (README, YAML, …) count as 0 so English "and/or" doesn't read as branching."""
    if not lines or Path(path).suffix not in CODE_EXT:
        return 0.0
    cx, deepest = 0.0, 0
    for ln in lines:
        code = ln.split("#", 1)[0].split("//", 1)[0]
        cx += len(DECISION_RE.findall(code))
        if code.strip():
            exp = ln.expandtabs(4)
            deepest = max(deepest, (len(exp) - len(exp.lstrip(" "))) // 4)
    return cx + 0.5 * min(6, deepest)


def hunk_delta(h) -> float:
    """Complexity added − complexity removed."""
    return lines_cx(h.added, h.path) - lines_cx(h.removed, h.path)


def hunk_dn(h) -> float:
    """Complexity delta normalized by hunk size → [-1, 1] (≈0.5 for typical new code)."""
    return max(-1.0, min(1.0, hunk_delta(h) / max(3.0, 0.5 * h.size)))


def hunk_heat(h) -> float:
    return h.size * (1 + lines_cx(h.added, h.path) + lines_cx(h.removed, h.path))


def compose_diff(hunks, max_bars: int = 32, heat: bool = False):
    max_bars = max(4, int(max_bars))
    s = Score(C.SEED)
    _strings_init(s)
    tl = Timeline()
    budget_beats = 4 * max(1, max_bars - 3)
    sized = [(h, 2.0 if h.size <= 8 else 4.0) for h in hunks]
    if sum(b for _, b in sized) > budget_beats:          # keep the hottest hunks, diff order
        used, keep = 0.0, set()
        for i in sorted(range(len(sized)), key=lambda i: -hunk_heat(sized[i][0])):
            b = sized[i][1]
            if used + b > budget_beats:
                b = 2.0                                  # squeeze a big hunk into ½ bar
                if used + b > budget_beats:
                    continue
                sized[i] = (sized[i][0], b)
            used += b
            keep.add(i)
        sized = [x for i, x in enumerate(sized) if i in keep]
    slots, t = [], 4.0                                   # bar 1 = intro
    for h, beats in sized:
        slots.append((h, t, beats))
        t += beats
    body_bars = max(1, math.ceil((t - 4.0) / 4))
    # chords: cycle, deletion-heavy bars → Gm
    tl.add_bar("Dm")
    _pad_bar(s, 0, "Dm", 50)
    bar_churn = []
    for j in range(body_bars):
        bar_beat = 4.0 + 4 * j
        hs = [h for h, st, b in slots if bar_beat <= st < bar_beat + 4]
        add = sum(len(h.added) for h in hs)
        rem = sum(len(h.removed) for h in hs)
        bar_churn.append(add + rem)
        sym = "Gm" if rem > add else CYCLE[(j + 1) % 4]
        tl.add_bar(sym)
        _pad_bar(s, bar_beat, sym, 50)
        _bass_bar(s, bar_beat, sym, 1 if len(hs) < 2 else 2, 72)
        s.add("drums", bar_beat, 0.25, KICK, 70)

    prev_path = None
    plan = []
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
        delta = hunk_delta(h)
        dn = hunk_dn(h)                                  # normalized complexity delta
        # added lines: rise from a chord tone; slope + start register follow the complexity delta
        slope = 1.25 + 1.0 * dn                          # 0.25 (simplified) … 2.25 degrees/note
        deg0 = to_degree(nearest(pcs[h32(h.path) % 3], ADD_BASE[part])) + round(2 * dn)
        lines = h.added or [""]
        tt = clamp01(0.25 + 0.6 * dn)                     # tension of this hunk
        for i in range(n_add):
            ln = lines[i * len(lines) // max(1, n_add)]
            deg = deg0 + round(i * slope + (h32(ln) % 2 if i else 0) * 0.5)
            p = max(45, min(93, from_degree(deg)))
            vel = 58 + min(24, len(ln.strip()) // 3) + 10 * max(0.0, dn)
            s.add(part, st + i * step, step * (0.9 - 0.3 * max(0.0, dn)), p, vel)
            if i == n_add - 1 and n_add > 1:             # final note: consonant 3rd / harsh rub
                s.add(part, st + i * step, step * 0.9, max(45, dyad_partner(p, tt, i, h32(h.path))), vel - 8)
        # removed lines: fall in the low strings, steeper when the removed code was knotty
        rslope = 1.0 + min(1.5, lines_cx(h.removed, h.path) / max(4.0, len(h.removed)))
        deg0 = to_degree(nearest(pcs[0], 50))
        lines = h.removed or [""]
        for i in range(n_rem):
            ln = lines[i * len(lines) // max(1, n_rem)]
            deg = deg0 - round(i * rslope + (h32(ln) % 2 if i else 0) * 0.5)
            s.add("cello", st + (n_add + i) * step, step * 0.95, max(36, from_degree(deg)),
                  62 + min(20, len(ln.strip()) // 4))
        plan.append({"file": h.path, "beat": st, "beats": beats, "added": len(h.added),
                     "removed": len(h.removed), "context": h.context, "cx_delta": round(delta, 2), "dn": round(dn, 2),
                     "color": color(tt)})

    if heat and bar_churn:                               # churn-density heat track (viola)
        cmax = max(bar_churn) or 1
        for j, c in enumerate(bar_churn):
            hv = c / cmax
            b0 = 4.0 + 4 * j
            sub = 0 if hv < 0.15 else 1 if hv < 0.4 else 2 if hv < 0.75 else 4
            s.cc("viola", b0, 11, 40 + 80 * hv)
            if not sub:
                continue
            fifth = nearest(chord_pcs(tl.chords[j + 1])[2], 64)
            for i in range(4 * sub):
                s.add("viola", b0 + i / sub, 0.9 / sub, fifth + (12 if hv > 0.9 and i % 2 else 0),
                      46 + 30 * hv + (6 if i % sub == 0 else 0), jitter=4)
        tl.heat = [round(c / cmax, 3) for c in bar_churn]
    _cadence(s, tl)
    assert len(tl.chords) <= max_bars, (len(tl.chords), max_bars)
    return s, tl, plan, len(hunks) - len(slots)
