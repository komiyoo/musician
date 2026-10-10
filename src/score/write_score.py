"""Step 1 — 作曲：把 22 小节的 D 小调铺底音乐写成音符事件 + 标准 MIDI。

Composition is fully separated from timbre: this module only knows pitches,
durations, velocities and controller curves.  Rendering happens later.

Outputs
  midi/full.mid          type-1 MIDI, track 0 = tempo map, one track per part
  midi/<part>.mid        one file per part (tempo map + that part) for renderers
  build/score.json       the raw note-event list (handy for debugging / DAW import)

Run:  python -m src.score.write_score
"""
from __future__ import annotations

import json
import io
import math
import random
from dataclasses import asdict

import mido

from src import config as C
from src.types import Note, CC

TPB = 480  # ticks per beat


# --------------------------------------------------------------------- harmony
# MIDI numbers: D3=50, D4=62, D5=74
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"])}


def pc(name: str) -> int:
    return NOTE[name]


# chord symbol -> (root, third, fifth) pitch classes
CHORDS = {
    "Dm": ("D", "F", "A"),
    "Bb": ("Bb", "D", "F"),
    "F": ("F", "A", "C"),
    "C": ("C", "E", "G"),
    "Gm": ("G", "Bb", "D"),
    "A": ("A", "C#", "E"),
    "Asus": ("A", "D", "E"),
    "Dm9": ("D", "F", "A", "C", "E"),
    "Gm9": ("G", "Bb", "D", "F", "A"),
    "Fmaj9": ("F", "A", "C", "E", "G"),
    "Bbmaj7": ("Bb", "D", "F", "A"),
    "A7": ("A", "C#", "E", "G"),
    "C13": ("C", "E", "G", "Bb", "D", "A"),
    "Dsus2": ("D", "E", "A"),
}

# 22 bars: intro 1-6 | develop 7-18 | resolve 19-22 (cadence on Dm)
PROGRESSION = [
    "Dm", "Bb", "F", "C", "Dm", "Bb",                                 # intro
    "Dm", "Bb", "F", "C", "Gm", "Bb", "F", "A", "Dm", "Bb", "Gm", "A",  # develop
    "Bb", "Gm", "Asus", "Dm",                                         # resolve
]
assert len(PROGRESSION) == C.N_BARS

# Pad voicings (close, smooth voice-leading around D4).  One chord per bar.
PAD_VOICINGS = {
    "Dm": [50, 57, 62, 65, 69],   # D3 A3 D4 F4 A4
    "Bb": [46, 58, 62, 65, 70],   # Bb2 Bb3 D4 F4 Bb4
    "F": [53, 57, 60, 65, 69],    # F3 A3 C4 F4 A4
    "C": [48, 55, 60, 64, 67],    # C3 G3 C4 E4 G4
    "Gm": [43, 55, 62, 67, 70],   # G2 G3 D4 G4 Bb4
    "A": [45, 57, 61, 64, 69],    # A2 A3 C#4 E4 A4
    "Asus": [45, 57, 62, 64, 69], # A2 A3 D4 E4 A4
}

# Violin melody, bars 7-22: list of (pitch, beats) per bar, sums to 4.
D4, E4, F4, G4, A4, Bb4, C5, Cs5, D5, E5, F5, G5, A5 = 62, 64, 65, 67, 69, 70, 72, 73, 74, 76, 77, 79, 81
MELODY = {
    7: [(A4, 2), (D5, 1), (E5, 1)],
    8: [(F5, 3), (D5, 1)],
    9: [(C5, 2), (A4, 1), (C5, 1)],
    10: [(E5, 2), (D5, 1), (C5, 1)],
    11: [(D5, 3), (Bb4, 1)],
    12: [(F5, 2), (D5, 2)],
    13: [(C5, 2), (F5, 2)],
    14: [(E5, 3), (Cs5, 1)],
    15: [(D5, 2), (F5, 1), (A5, 1)],
    16: [(F5, 4)],
    17: [(G5, 2), (F5, 1), (D5, 1)],
    18: [(E5, 2), (Cs5, 2)],
    19: [(D5, 4)],
    20: [(Bb4, 2), (D5, 2)],
    21: [(E5, 2), (Cs5, 2)],
    22: [(D5, 4)],
}


def section_of(bar: int) -> str:
    for name, (a, b) in C.SECTIONS.items():
        if a <= bar <= b:
            return name
    raise ValueError(bar)


def chord_pcs(sym: str) -> list[int]:
    return [pc(n) for n in CHORDS[sym]]


def nearest(pcl: int, around: int) -> int:
    """MIDI note with pitch class `pcl` closest to `around`."""
    cands = [o * 12 + pcl for o in range(0, 11)]
    return min(cands, key=lambda n: abs(n - around))


# --------------------------------------------------------------------- builder
class Score:
    def __init__(self, seed: int = C.SEED):
        self.notes: list[Note] = []
        self.ccs: list[CC] = []
        self.rng = random.Random(seed)

    def add(self, part: str, start: float, dur: float, pitch: int, vel: float, jitter: int = 6,
            *, notation_dur: float | None = None):
        v = int(round(vel + self.rng.uniform(-jitter, jitter)))  # 每个音轻微随机力度
        self.notes.append(Note(part, round(start, 4), round(dur, 4), int(pitch), max(1, min(127, v)),
                               notation_dur=notation_dur))

    def cc(self, part: str, beat: float, cc: int, value: float):
        self.ccs.append(CC(part, round(beat, 4), cc, int(max(0, min(127, round(value))))))


def bar_start(bar: int) -> float:
    return (bar - 1) * C.BEATS_PER_BAR


def compose() -> Score:
    s = Score()
    for bar, sym in enumerate(PROGRESSION, start=1):
        sec = section_of(bar)
        b0 = bar_start(bar)
        r, t, f = chord_pcs(sym)

        # ---------------------------------------------------- 1. pad: 1 chord / bar
        pad_vel = {"intro": 58, "develop": 66, "resolve": 60}[sec]
        if bar == C.N_BARS:
            pad_vel = 54
        for p in PAD_VOICINGS[sym]:
            s.add("pad", b0, 4.0, p, pad_vel, jitter=4)

        # ---------------------------------------------------- 2. piano: eighths (2 notes / beat)
        bass = nearest(r, 45)                       # around A2
        pattern = [bass, bass + 7 if (bass + 7) % 12 == f else nearest(f, bass + 7),
                   bass + 12, nearest(t, bass + 15), nearest(f, bass + 19),
                   nearest(t, bass + 15), bass + 12, nearest(f, bass + 7)]
        if sec in ("intro", "develop"):
            base = 62 if sec == "intro" else 68
            for i, p in enumerate(pattern):
                accent = 8 if i in (0, 4) else 0
                s.add("piano", b0 + i * 0.5, 0.55, p, base + accent)
        else:
            # resolve: piano slows — quarters, then halves, then one held chord
            if bar in (19, 20):
                for i, p in enumerate([pattern[0], pattern[2], pattern[3], pattern[4]]):
                    s.add("piano", b0 + i, 1.05, p, 60 - (bar - 19) * 6)
            elif bar == 21:
                s.add("piano", b0, 2.1, pattern[0], 54)
                s.add("piano", b0, 2.1, pattern[3], 50)
                s.add("piano", b0 + 2, 2.1, pattern[2], 50)
                s.add("piano", b0 + 2, 2.1, nearest(pc("C#"), 61), 52)  # leading tone A -> Dm
            else:  # bar 22: final Dm, rolled
                for i, p in enumerate([38, 50, 57, 62, 65, 69]):
                    s.add("piano", b0 + i * 0.08, 4.0 - i * 0.08, p, 52, jitter=3)

        # ---------------------------------------------------- 3. strings
        if bar >= 7:
            mel = MELODY[bar]
            pos = b0
            for p, d in mel:
                s.add("violin", pos, d, p, 70)
                pos += d
            # viola: inner voices (3rd + 5th), half notes, C4..A4
            for half in (0, 2):
                s.add("viola", b0 + half, 2.0, nearest(t, 64), 62)
                s.add("viola", b0 + half, 2.0, nearest(f, 60), 58)
            # cello: roots, whole notes around D3
            s.add("cello", b0, 4.0, nearest(r, 46), 66)

        # ---------------------------------------------------- bass (Surge XT)
        if 7 <= bar <= 21:
            root = nearest(r, 38)  # around D2
            if bar <= 18:
                for off, d, v in [(0, 1.5, 84), (1.5, 0.5, 70), (2, 1.5, 80), (3.5, 0.5, 68)]:
                    s.add("bass", b0 + off, d * 0.95, root, v)
            else:
                s.add("bass", b0, 4.0, root, 70 - (bar - 19) * 6)
        if bar == C.N_BARS:
            s.add("bass", b0, 4.0, 38, 56)

        # ---------------------------------------------------- 4. arp: sixteenths (4 notes / beat)
        if 7 <= bar <= 19:
            tones = sorted({nearest(x, 74) for x in (r, t, f)})
            t0, t1, t2 = tones
            cell_a = [t0, t1, t2, t0 + 12, t1 + 12, t0 + 12, t2, t1]   # up, peak, back down
            cell_b = [t0, t1, t2, t0 + 12, t2, t1, t0, t1]            # answering, lower
            seq = cell_a + cell_b                                      # 16 sixteenths / bar
            for i in range(16):
                fade = 1.0 if bar < 19 else (1 - i / 20)
                vel = (60 + (6 if i % 4 == 0 else 0)) * fade
                s.add("arp", b0 + i * 0.25, 0.22, seq[i % 16], vel, jitter=5)

        # ---------------------------------------------------- 5. drums (GM: 36 kick, 37 rim, 38 snare, 42 closed hat)
        if 7 <= bar <= 18:
            for beat in (0, 2):                              # kick on 1 and 3
                s.add("drums", b0 + beat, 0.25, 36, 92)
            for i in range(8):                               # closed hat every half beat
                s.add("drums", b0 + i * 0.5, 0.125, 42, 64 if i % 2 == 0 else 50, jitter=8)
            if 11 <= bar <= 14:                              # later: rim on 2 & 4
                for beat in (1, 3):
                    s.add("drums", b0 + beat, 0.25, 37, 66)
            if 15 <= bar <= 18:                              # then a soft snare on 2 & 4
                for beat in (1, 3):
                    s.add("drums", b0 + beat, 0.25, 38, 62)
            if bar == 18:                                    # tiny pickup into the cadence
                s.add("drums", b0 + 3.5, 0.25, 38, 48)
                s.add("drums", b0 + 3.75, 0.25, 38, 54)
        elif bar == 19:
            s.add("drums", b0, 0.25, 36, 78)
            for i in range(8):
                s.add("drums", b0 + i * 0.5, 0.125, 42, 50 - i * 3, jitter=4)
        elif bar == C.N_BARS:
            s.add("drums", b0, 0.25, 36, 60)

    # ------------------------------------------------ string dynamics (CC11 expression)
    # crescendo in over bars 7-10, full through 18, diminuendo out 19-22
    start, peak_until, end = bar_start(7), bar_start(19), bar_start(23)
    for part in ("violin", "viola", "cello"):
        steps = 64
        for i in range(steps + 1):
            beat = start + (peak_until - start) * i / steps
            ramp = min(1.0, (beat - start) / (4 * C.BEATS_PER_BAR))
            s.cc(part, beat, 11, 40 + 87 * ramp)
        for i in range(1, steps + 1):
            beat = peak_until + (end - peak_until) * i / steps
            s.cc(part, beat, 11, 127 - 97 * i / steps)
    # also shape note velocities so sample layers follow the hairpins
    for n in s.notes:
        if n.part in ("violin", "viola", "cello"):
            if n.start < bar_start(11):
                k = 0.65 + 0.35 * (n.start - bar_start(7)) / (4 * C.BEATS_PER_BAR)
            elif n.start >= bar_start(19):
                k = 1.0 - 0.45 * (n.start - bar_start(19)) / (4 * C.BEATS_PER_BAR)
            else:
                k = 1.0
            n.vel = max(1, min(127, int(n.vel * k)))
    return s


# --------------------------------------------------------------------- MIDI I/O
def tempo_track() -> mido.MidiTrack:
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name="conductor", time=0))
    tr.append(mido.MetaMessage("time_signature", numerator=C.BEATS_PER_BAR, denominator=4, time=0))
    key = C.KEY.replace(" minor", "m").replace(" major", "")
    tr.append(mido.MetaMessage("key_signature", key=key, time=0))
    last_tick, last_tempo = 0, None
    for bar, tempo in enumerate(C.tempo_map().tempos, 1):
        tick = int(bar_start(bar) * TPB)
        if bar in C.KEY_CHANGES:
            tr.append(mido.MetaMessage("key_signature", key=C.KEY_CHANGES[bar], time=tick - last_tick))
            last_tick = tick
        if tempo != last_tempo:
            tick = int(bar_start(bar) * TPB)
            tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=tick - last_tick))
            last_tick, last_tempo = tick, tempo
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def part_track(score: Score, part: str) -> mido.MidiTrack:
    ch = C.PARTS[part]["channel"]
    ev = []  # (tick, order, msg)
    for n in score.notes:
        if n.part != part:
            continue
        on = int(round(n.start * TPB))
        off = int(round((n.start + n.dur) * TPB))
        ev.append((on, 1, mido.Message("note_on", channel=ch, note=n.pitch, velocity=n.vel)))
        ev.append((off, 0, mido.Message("note_off", channel=ch, note=n.pitch, velocity=0)))
    for c in score.ccs:
        if c.part == part:
            ev.append((int(round(c.beat * TPB)), -1, mido.Message("control_change", channel=ch, control=c.cc, value=c.value)))
    keyswitch = C.PARTS[part].get("keyswitch")
    if keyswitch is not None:
        ev.extend([(0, -2, mido.Message("note_on", channel=ch, note=keyswitch, velocity=1)),
                   (1, 0, mido.Message("note_off", channel=ch, note=keyswitch, velocity=0))])
    ev.sort(key=lambda e: (e[0], e[1]))
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=part, time=0))
    if "port" in C.PARTS[part]:
        tr.append(mido.MetaMessage("midi_port", port=C.PARTS[part]["port"], time=0))
    if ch != 9:
        tr.append(mido.Message("program_change", channel=ch, program=C.PARTS[part]["program"], time=0))
    # sensible start state for samplers
    tr.append(mido.Message("control_change", channel=ch, control=7, value=100, time=0))
    if part in ("violin", "viola", "cello"):
        tr.append(mido.Message("control_change", channel=ch, control=11, value=40, time=0))
    else:
        tr.append(mido.Message("control_change", channel=ch, control=11, value=127, time=0))
    last = 0
    for tick, _, msg in ev:
        tr.append(msg.copy(time=tick - last))
        last = tick
    end_tick = math.ceil(C.tempo_map().beat_at(C.total_seconds()) * TPB)
    tr.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - last)))
    return tr


def midi_files(score: Score, parts=None, *, tracks: dict[str, mido.MidiTrack] | None = None) -> tuple[dict[str, bytes], bytes]:
    """Full MIDI keeps port routing; isolated instrument files always use port zero."""
    full = mido.MidiFile(type=1, ticks_per_beat=TPB)
    full.tracks.append(tempo_track())
    files = {}
    for part in C.PARTS if parts is None else parts:
        tr = tracks[part] if tracks is not None else part_track(score, part)
        full.tracks.append(tr)
        single = mido.MidiFile(type=1, ticks_per_beat=TPB)
        single.tracks.append(tempo_track())
        single.tracks.append(mido.MidiTrack(m.copy(port=0) if m.type == "midi_port" else m.copy() for m in tr))
        buf = io.BytesIO()
        single.save(file=buf)
        files[part] = buf.getvalue()
    buf = io.BytesIO()
    full.save(file=buf)
    return files, buf.getvalue()


def write(score: Score) -> dict:
    C.MIDI_DIR.mkdir(parents=True, exist_ok=True)
    C.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    files, full = midi_files(score)
    for part, data in files.items():
        (C.MIDI_DIR / f"{part}.mid").write_bytes(data)
    (C.MIDI_DIR / "full.mid").write_bytes(full)
    counts = {p: sum(n.part == p for n in score.notes) for p in C.PARTS}
    meta = {
        "key": C.KEY, "bpm": C.BPM, "bars": C.N_BARS, "sections": C.SECTIONS,
        "progression": PROGRESSION, "rit_bpm": C.RIT_BPM,
        "duration_s": round(C.total_seconds(), 2),
        "notes": [asdict(n) for n in score.notes], "ccs": [asdict(c) for c in score.ccs],
    }
    (C.BUILD_DIR / "score.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return counts


def main():
    score = compose()
    counts = write(score)
    print(f"[score] {C.KEY}, {C.BPM} BPM, {C.N_BARS} bars, ~{C.total_seconds():.1f}s")
    print("[score] progression:", " | ".join(PROGRESSION))
    for p, n in counts.items():
        print(f"[score]   {p:7s} {n:4d} notes -> midi/{p}.mid")
    print("[score] full arrangement -> midi/full.mid")


if __name__ == "__main__":
    main()
