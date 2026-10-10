"""The same integer MIDI tempo map drives notes, audio and video."""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import cached_property
from itertools import accumulate


@dataclass
class Note:
    part: str
    start: float
    dur: float
    pitch: int
    vel: int
    notation_dur: float | None = field(default=None, kw_only=True)  # ungated quarter-note beats


@dataclass
class CC:
    part: str
    beat: float
    cc: int
    value: int


@dataclass
class TimedNote(Note):
    start_s: float
    end_s: float


@dataclass(frozen=True)
class TempoMap:
    tempos: tuple[int, ...]  # microseconds per quarter note, one entry per bar
    beats_per_bar: int = 4
    tail_s: float = 0.0

    def __post_init__(self):
        if not self.tempos or any(type(t) is not int or not 1 <= t <= 0xFFFFFF for t in self.tempos):
            raise ValueError("tempos must contain positive MIDI microseconds per beat")
        if self.beats_per_bar < 1 or not math.isfinite(self.tail_s) or self.tail_s < 0:
            raise ValueError("invalid meter or tail duration")

    @classmethod
    def from_bpms(cls, bpms, beats_per_bar=4, tail_s=0.0):
        return cls(tuple(round(60_000_000 / bpm) for bpm in bpms), beats_per_bar, tail_s)

    @cached_property
    def boundaries(self) -> tuple[float, ...]:
        return (0.0, *accumulate(t * self.beats_per_bar / 1_000_000 for t in self.tempos))

    @property
    def duration_s(self) -> float:
        return self.boundaries[-1] + self.tail_s

    def seconds_at(self, beat: float) -> float:
        if not math.isfinite(beat) or beat < 0:
            raise ValueError("beat must be finite and nonnegative")
        bar = min(int(beat // self.beats_per_bar), len(self.tempos) - 1)
        return self.boundaries[bar] + (beat - bar * self.beats_per_bar) * self.tempos[bar] / 1_000_000

    def beat_at(self, seconds: float) -> float:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("seconds must be finite and nonnegative")
        bar = min(bisect_right(self.boundaries, seconds) - 1, len(self.tempos) - 1)
        return bar * self.beats_per_bar + (seconds - self.boundaries[bar]) * 1_000_000 / self.tempos[bar]
