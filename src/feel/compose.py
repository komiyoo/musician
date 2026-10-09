"""ArrangementSpec → 音符 → MIDI（复用 write_score 的 Score、和弦表、pad 排列与 MIDI 写出）。

Every part is composed with its own seeded RNG, so changing one knob only changes the
MIDI of the parts it actually affects — that is what lets render.py re-render only those.
"""
from __future__ import annotations

import io
import zlib

import mido

from src import config as C
from src.feel.spec import ArrangementSpec
from src.score import write_score as W
from src.score.write_score import chord_pcs, nearest

# D natural minor (= F major) scale pitch classes; C# is used over A / Asus.
SCALE = [2, 4, 5, 7, 9, 10, 0]


def apply_config(spec: ArrangementSpec) -> None:
    """Point src.config's form (tempo, length, rit, sections) at this spec."""
    C.BPM = spec.bpm
    C.N_BARS = spec.bars
    C.RIT_BPM = dict(spec.rit_bpm)
    C.TAIL_SECONDS = spec.tail_s
    C.SECTIONS = {s.name: tuple(s.bars) for s in spec.sections}


def _section(spec: ArrangementSpec, bar: int):
    for s in spec.sections:
        if s.bars[0] <= bar <= s.bars[1]:
            return s
    return spec.sections[-1]


def _scale_tones(lo: int, hi: int, sym: str) -> list[int]:
    pcs = set(SCALE)
    if sym in ("A", "Asus"):
        pcs = (pcs - {0}) | {1}            # leading tone C#
    return [p for p in range(lo, hi + 1) if p % 12 in pcs]


def _melody(spec: ArrangementSpec, s: W.Score, bar: int, sym: str, b0: float, state: dict, is_last: bool):
    """Simple singable line: chord tone on the downbeat, scale steps in between, A4..A5."""
    rhythms = [[2, 1, 1], [3, 1], [2, 2], [2, 1, 1], [1, 1, 2], [4]]
    rng = state["rng"]
    if is_last:
        rh = [4]
    else:
        rh = rhythms[rng.randrange(len(rhythms))] if bar % 4 else [2, 2]
    tones = _scale_tones(69, 81, sym)
    chord = [p for p in tones if p % 12 in chord_pcs(sym)]
    prev = state.get("prev", 74)
    pos = b0
    for i, d in enumerate(rh):
        if i == 0:
            p = min(chord, key=lambda x: (abs(x - prev), x))
            if is_last:
                p = nearest(chord_pcs(sym)[0], 74)
        else:
            idx = tones.index(prev) if prev in tones else 0
            step = rng.choice([-1, 1, 1, 2, -2])
            p = tones[max(0, min(len(tones) - 1, idx + step))]
        s.add("violin", pos, d, p, 70)
        prev = p
        pos += d
    state["prev"] = prev


def compose_part(spec: ArrangementSpec, part: str) -> W.Score:
    s = W.Score(seed=C.SEED + zlib.crc32(part.encode()) % 10_000)
    state = {"rng": s.rng}
    dev, res = spec.sections[1], spec.sections[2]
    bright = spec.knobs["brightness"] / 100
    for bar, sym in enumerate(spec.progression, start=1):
        sec = _section(spec, bar)
        if part not in sec.parts:
            continue
        b0 = W.bar_start(bar)
        r, t, f = chord_pcs(sym)
        last = bar == spec.bars
        in_res = sec.name == "resolve"
        res_i = bar - res.bars[0]          # 0.. in the resolve section

        if part == "pad":
            voicing = list(W.PAD_VOICINGS[sym])
            if bright >= 0.67:             # brighter: drop the low doubling, add an octave on top
                voicing = voicing[1:] + [voicing[-2] + 12]
            elif bright < 0.34:            # darker: keep it low and close
                voicing = voicing[:-1]
            vel = {"intro": 58, "develop": 66, "resolve": 60}[sec.name] - (6 if last else 0)
            if bar == 1:
                s.cc("pad", 0, 74, spec.pad_brightness_cc74)
            for p in voicing:
                s.add("pad", b0, 4.0, p, vel, jitter=4)

        elif part == "piano":
            bass = nearest(r, 45)
            pattern = [bass, nearest(f, bass + 7), bass + 12, nearest(t, bass + 15), nearest(f, bass + 19),
                       nearest(t, bass + 15), bass + 12, nearest(f, bass + 7)]
            soft = -8 if spec.voice_mode == "不抢" else 0
            if not in_res:
                base = (62 if sec.name == "intro" else 68) + soft
                if spec.piano_rhythm == "eighth" and sec.name == "develop":
                    for i, p in enumerate(pattern):
                        s.add("piano", b0 + i * 0.5, 0.55, p, base + (8 if i in (0, 4) else 0))
                else:
                    for i, p in enumerate([pattern[0], pattern[2], pattern[3], pattern[4]]):
                        s.add("piano", b0 + i, 1.05, p, base + (6 if i == 0 else 0))
            elif last:
                for i, p in enumerate([nearest(r, 38), nearest(r, 50), nearest(f, 57), nearest(r, 62),
                                       nearest(t, 65), nearest(f, 69)]):
                    s.add("piano", b0 + i * 0.08, 4.0 - i * 0.08, p, 52 + soft, jitter=3)
            elif bar == spec.bars - 1:
                s.add("piano", b0, 2.1, pattern[0], 54 + soft)
                s.add("piano", b0, 2.1, pattern[3], 50 + soft)
                s.add("piano", b0 + 2, 2.1, pattern[2], 50 + soft)
                s.add("piano", b0 + 2, 2.1, nearest(t, 61), 52 + soft)
            else:
                for i, p in enumerate([pattern[0], pattern[2], pattern[3], pattern[4]]):
                    s.add("piano", b0 + i, 1.05, p, 60 - res_i * 6 + soft)

        elif part == "violin":
            _melody(spec, s, bar, sym, b0, state, last)

        elif part == "viola":
            for half in (0, 2):
                s.add("viola", b0 + half, 2.0, nearest(t, 64), 62)
                s.add("viola", b0 + half, 2.0, nearest(f, 60), 58)

        elif part == "cello":
            s.add("cello", b0, 4.0, nearest(r, 46), 66)

        elif part == "bass":
            root = nearest(r, 38)
            if in_res:
                s.add("bass", b0, 4.0, root, 70 - res_i * 6)
            elif spec.knobs["density"] >= 60:
                for off, d, v in [(0, 1.5, 84), (1.5, 0.5, 70), (2, 1.5, 80), (3.5, 0.5, 68)]:
                    s.add("bass", b0 + off, d * 0.95, root, v)
            else:
                for off, v in ((0, 80), (2, 72)):
                    s.add("bass", b0 + off, 1.9, root, v)

        elif part == "arp":
            center = {"低": 62, "中": 74, "高": 86}[spec.arp["register"]]
            tones = sorted({nearest(x, center) for x in (r, t, f)})
            t0, t1, t2 = tones
            seq = [t0, t1, t2, t0 + 12, t1 + 12, t0 + 12, t2, t1, t0, t1, t2, t0 + 12, t2, t1, t0, t1]
            step = 0.25 if spec.arp["rate"] == "16th" else 0.5
            n = int(4 / step)
            for i in range(n):
                vel = (60 + (6 if i % 4 == 0 else 0))
                s.add("arp", b0 + i * step, step * 0.88, seq[(i * (16 // n)) % 16], vel, jitter=5)

        elif part == "drums":
            for beat in (0, 2):
                s.add("drums", b0 + beat, 0.25, 36, 88)
            hat_step = 0.5 if spec.drums == "full" else 1.0
            for i in range(int(4 / hat_step)):
                s.add("drums", b0 + i * hat_step, 0.125, 42, 62 if i % 2 == 0 else 50, jitter=8)
            if spec.drums == "full":
                half = dev.bars[0] + (dev.bars[1] - dev.bars[0]) // 2
                note = 37 if bar < half else 38
                for beat in (1, 3):
                    s.add("drums", b0 + beat, 0.25, note, 64)
            if bar == dev.bars[1]:
                s.add("drums", b0 + 3.5, 0.25, 38, 48)
                s.add("drums", b0 + 3.75, 0.25, 38, 54)

    # drums: one soft kick on the final chord if drums are used at all
    if part == "drums" and spec.drums != "off":
        s.add("drums", W.bar_start(spec.bars), 0.25, 36, 60)

    # strings: CC11 crescendo into the development, diminuendo through the resolve
    if part in ("violin", "viola", "cello"):
        a, peak, end = W.bar_start(dev.bars[0]), W.bar_start(res.bars[0]), W.bar_start(spec.bars + 1)
        ramp_beats = min(4, dev.bars[1] - dev.bars[0] + 1) * C.BEATS_PER_BAR
        for i in range(65):
            beat = a + (peak - a) * i / 64
            s.cc(part, beat, 11, 40 + 87 * min(1.0, (beat - a) / ramp_beats))
        for i in range(1, 65):
            s.cc(part, peak + (end - peak) * i / 64, 11, 127 - 97 * i / 64)
    return s


def compose(spec: ArrangementSpec) -> W.Score:
    apply_config(spec)
    full = W.Score()
    for part in spec.parts:
        ps = compose_part(spec, part)
        full.notes += ps.notes
        full.ccs += ps.ccs
    return full


def _midi_bytes(mf: mido.MidiFile) -> bytes:
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def midi_files(spec: ArrangementSpec, score: W.Score) -> tuple[dict[str, bytes], bytes]:
    """Per-part MIDI (tempo map + that part) for the renderers, plus full.mid for DAWs."""
    apply_config(spec)
    full = mido.MidiFile(type=1, ticks_per_beat=W.TPB)
    full.tracks.append(W.tempo_track())
    per_part = {}
    for part in C.PARTS:
        if not any(n.part == part for n in score.notes):
            continue
        tr = W.part_track(score, part)
        full.tracks.append(tr)
        single = mido.MidiFile(type=1, ticks_per_beat=W.TPB)
        single.tracks.append(W.tempo_track())
        single.tracks.append(tr)
        per_part[part] = _midi_bytes(single)
    return per_part, _midi_bytes(full)
