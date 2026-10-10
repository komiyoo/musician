"""Single source for the story JSON contract and instrument catalog."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .music import TempoMap, CC, TimedNote

Identifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')]
Articulation = Literal['sustain', 'legato', 'staccato', 'pizzicato', 'tremolo']
Pitch = Annotated[int, Field(strict=True, ge=0, le=127)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Instrument(Contract):
    zh: str
    en: str
    program: Pitch
    family: str
    low: Pitch
    high: Pitch
    center: Pitch
    drum_note: Pitch | None = None


# Program numbers are zero-based; ranges are sounding (concert) pitches.
INSTRUMENTS = {
    name: Instrument(zh=zh, en=en, program=program, family=family,
                     low=low, high=high, center=register, drum_note=drum)
    for name, zh, en, program, family, low, high, register, drum in [
        ('piano', '钢琴', 'Piano', 0, 'piano', 21, 108, 60, None),
        ('clarinet', '单簧管', 'Clarinet', 71, 'woodwind', 50, 94, 67, None),
        ('bassoon', '巴松', 'Bassoon', 70, 'woodwind', 34, 75, 48, None),
        ('violin', '小提琴', 'Violin', 40, 'strings', 55, 103, 76, None),
        ('viola', '中提琴', 'Viola', 41, 'strings', 48, 88, 62, None),
        ('cello', '大提琴', 'Cello', 42, 'strings', 36, 76, 50, None),
        ('double_bass', '低音提琴', 'Double bass', 43, 'strings', 28, 67, 38, None),
        ('horn', '圆号', 'Horn', 60, 'brass', 34, 77, 55, None),
        ('trumpet', '小号', 'Trumpet', 56, 'brass', 54, 86, 69, None),
        ('trombone', '长号', 'Trombone', 57, 'brass', 40, 77, 52, None),
        ('flute', '长笛', 'Flute', 73, 'woodwind', 60, 96, 79, None),
        ('oboe', '双簧管', 'Oboe', 68, 'woodwind', 58, 91, 72, None),
        ('harp', '竖琴', 'Harp', 46, 'pluck', 24, 103, 67, None),
        ('marimba', '马林巴', 'Marimba', 12, 'mallet', 48, 96, 60, None),
        ('vibraphone', '颤音琴', 'Vibraphone', 11, 'mallet', 53, 89, 72, None),
        ('celesta', '钢片琴', 'Celesta', 8, 'mallet', 60, 108, 84, None),
        ('guitar', '尼龙吉他', 'Nylon guitar', 24, 'pluck', 40, 88, 60, None),
        ('sax', '萨克斯', 'Saxophone', 65, 'woodwind', 49, 80, 65, None),
        ('choir', '人声', 'Choir', 52, 'choir', 48, 84, 65, None),
        ('timpani', '定音鼓', 'Timpani', 47, 'timpani', 36, 57, 38, None),
        ('cymbal', '镲', 'Cymbal', 0, 'drums', 49, 49, 49, 49),
        ('snare', '军鼓', 'Snare', 0, 'drums', 38, 38, 38, 38),
        ('pad', '氛围铺底', 'Pad', 89, 'pad', 36, 96, 62, None),
        ('bass', '合成贝斯', 'Synth bass', 38, 'bass', 24, 60, 38, None),
        ('arp', '合成琶音', 'Synth arp', 81, 'arp', 48, 96, 74, None),
    ]
}


class StoryPart(Contract):
    id: Identifier
    instrument: str = Field(json_schema_extra={'enum': list(INSTRUMENTS)})
    role: Literal['theme', 'root', 'third', 'seventh', 'color', 'arpeggio', 'pulse', 'counter', 'pad', 'percussion']
    articulation: Articulation = 'sustain'
    engine: Literal['auto', 'sfizz', 'fluidsynth', 'surge', 'fallback'] = 'auto'
    sfz: str | None = None
    soundfont: str | None = None
    patch: str | None = None
    keyswitch: Pitch | None = None
    pan: float = Field(default=0, ge=-1, le=1)
    gain_db: float = Field(default=0, ge=-36, le=12)

    @field_validator('instrument')
    @classmethod
    def known_instrument(cls, value):
        if value not in INSTRUMENTS:
            raise ValueError(f'unknown instrument: {value}')
        return value


class MotifNote(Contract):
    pitch: Pitch
    beats: float = Field(gt=0, le=16)


class ThemeUse(Contract):
    motif: Identifier
    part: Identifier
    beat: float = Field(default=0, ge=0)
    transpose: int = Field(default=0, strict=True, ge=-48, le=48)
    stretch: float = Field(default=1, gt=0, le=8)


class StorySection(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=80)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0, le=3600)
    bars: int = Field(strict=True, ge=1, le=256)
    key: Literal['Dm', 'F'] = 'Dm'
    chords: list[str] = Field(min_length=1, max_length=256)
    parts: list[Identifier] = Field(min_length=1, max_length=64)
    energy: float = Field(default=0.5, ge=0, le=1)
    end_energy: float | None = Field(default=None, ge=0, le=1)
    slowdown: float = Field(default=1, ge=0.35, le=1)
    accents: list[float] = Field(default_factory=lambda: [1, 1, 1, 1], min_length=1, max_length=16)
    themes: list[ThemeUse] = Field(default_factory=list, max_length=128)
    exits: dict[Identifier, int] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_section(self):
        from src.score.write_score import CHORDS
        if self.end_s <= self.start_s:
            raise ValueError('section end must be after its start')
        if any(chord not in CHORDS for chord in self.chords):
            raise ValueError('unsupported chord in section')
        if any(not 0 < d <= 4 for d in self.accents) or abs(sum(self.accents) - 4) > 1e-6:
            raise ValueError('accent durations must be positive and sum to four beats')
        if len(set(self.parts)) != len(self.parts):
            raise ValueError('duplicate section part')
        if any(p not in self.parts or type(n) is not int or not 1 <= n <= self.bars for p, n in self.exits.items()):
            raise ValueError('exits must reference active parts and valid bar counts')
        return self


class Caption(Contract):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=240)
    translation: str = Field(default='', max_length=320)


class StorySpec(Contract):
    version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=100)
    seed: int = Field(default=20261009, strict=True, ge=0, le=0xFFFFFFFF)
    tail_s: float = Field(default=3.6, ge=0, le=20)
    master_lufs: float = Field(default=-18, ge=-30, le=-12)
    parts: list[StoryPart] = Field(min_length=1, max_length=64)
    motifs: dict[Identifier, list[MotifNote]] = Field(default_factory=dict)
    sections: list[StorySection] = Field(min_length=1, max_length=64)
    captions: list[Caption] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def consistent_story(self):
        parts = {p.id: p for p in self.parts}
        if len(parts) != len(self.parts) or len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError('part and section IDs must be unique')
        if any(not notes or len(notes) > 128 for notes in self.motifs.values()):
            raise ValueError('a motif needs 1 to 128 notes')
        end = 0.0
        for section in self.sections:
            if abs(section.start_s - end) > 1e-6:
                raise ValueError('sections must be contiguous and start at zero')
            if not set(section.parts) <= parts.keys():
                raise ValueError('section references an unknown part')
            for theme in section.themes:
                if theme.part not in section.parts or theme.motif not in self.motifs:
                    raise ValueError('theme references an inactive part or unknown motif')
                notes = self.motifs[theme.motif]
                limit = section.exits.get(theme.part, section.bars) * 4
                if theme.beat + sum(n.beats for n in notes) * theme.stretch > limit + 1e-6:
                    raise ValueError('theme extends beyond its part or section')
                instrument = INSTRUMENTS[parts[theme.part].instrument]
                if any(not instrument.low <= n.pitch + theme.transpose <= instrument.high for n in notes):
                    raise ValueError('transformed theme is outside the instrument range')
            end = section.end_s
        if self.tail_s >= self.sections[-1].end_s - self.sections[-1].start_s:
            raise ValueError('tail must be shorter than the last section')
        end = 0.0
        for caption in self.captions:
            if caption.start_s < end or not caption.start_s < caption.end_s <= self.sections[-1].end_s:
                raise ValueError('captions must be ordered, non-overlapping and inside the story')
            end = caption.end_s
        return self


class StoryManifest(Contract):
    story: StorySpec
    timeline: TempoMap
    notes: list[TimedNote]
    ccs: list[CC]
