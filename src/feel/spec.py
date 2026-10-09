"""文字感觉 + 旋钮 → ArrangementSpec（编曲规格）。

All knobs are 0..100 (UI sliders); `voice` is 0/1/2 = 不抢 / 平衡 / 偏配乐.
Nothing here needs music knowledge from the user: the text only nudges the knobs,
and the knobs map to concrete musical decisions that are written into the spec
(so the JSON shown in the UI explains what was generated).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

DEFAULT_KNOBS = {"mood": 40, "speed": 50, "density": 50, "brightness": 50, "voice": 1}
VOICE_LABELS = ["不抢", "平衡", "偏配乐"]
EXPORT_SECONDS = 58.0
PREVIEW_SECONDS = 20.0
TAIL_FULL, TAIL_PREVIEW = 3.6, 2.4

# keyword -> knob deltas (+ a short human-readable reason)
KEYWORDS: list[tuple[tuple[str, ...], dict, str]] = [
    (("悬疑", "紧张", "黑暗", "深沉", "严肃", "危机", "神秘", "压抑", "低沉", "冷峻", "阴郁", "沉重", "夜晚", "风险"),
     {"mood": -25, "brightness": -20}, "情绪更暗"),
    (("轻松", "欢快", "明亮", "温暖", "希望", "阳光", "活泼", "积极", "开心", "愉快", "清新", "美好", "励志", "快乐"),
     {"mood": +25, "brightness": +20}, "情绪更亮"),
    (("激动", "燃", "热血", "动感", "紧凑", "节奏感", "发布会", "冲刺", "爆发", "速度感", "快节奏"),
     {"speed": +25, "density": +15}, "速度更快"),
    (("舒缓", "平静", "安静", "冥想", "治愈", "抒情", "温柔", "慢节奏", "放松", "沉思", "娓娓道来"),
     {"speed": -25, "density": -15}, "速度更慢"),
    (("史诗", "宏大", "丰富", "饱满", "震撼", "大气", "壮阔", "高潮"),
     {"density": +25}, "层次更密"),
    (("简约", "极简", "干净", "留白", "克制", "简单", "空灵", "低调"),
     {"density": -25}, "层次更疏"),
    (("科技", "未来", "数据", "AI", "芯片", "代码", "电子", "算法", "互联网", "数码", "机器"),
     {"brightness": +10, "density": +10}, "加一点电子琶音的科技感"),
    (("口播", "解说", "旁白", "讲解", "播客", "访谈", "教程", "科普"),
     {"voice": -1}, "给人声让位"),
    (("片头", "开场", "宣传", "预告", "纯音乐", "广告", "混剪", "转场"),
     {"voice": +1}, "音乐可以更突出"),
]


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def parse_feel(text: str, base: dict | None = None) -> tuple[dict, list[str]]:
    """Suggest knob values from free text. Returns (knobs, reasons)."""
    knobs = dict(DEFAULT_KNOBS if base is None else {**DEFAULT_KNOBS, **base})
    reasons: list[str] = []
    t = text or ""
    tl = t.lower()
    for words, delta, why in KEYWORDS:
        hits = [w for w in words if w.lower() in tl]
        if not hits:
            continue
        for k, d in delta.items():
            knobs[k] = knobs[k] + d
        reasons.append(f"识别到「{'、'.join(hits[:3])}」→ {why}")
    for k in ("mood", "speed", "density", "brightness"):
        knobs[k] = int(clamp(knobs[k], 0, 100))
    knobs["voice"] = int(clamp(knobs["voice"], 0, 2))
    if not reasons and t.strip():
        reasons.append("没识别到明显的情绪词，用默认的「科技解说」感觉")
    return knobs, reasons


def script_seconds(text: str) -> float | None:
    """Rough narration length for a pasted 口播稿 (Chinese ≈ 4.5 字/秒). Short descriptions → None."""
    chars = len(re.sub(r"\s|[，。！？、；：,.!?;:\"'“”‘’（）()]", "", text or ""))
    if chars < 120:
        return None
    return round(clamp(chars / 4.5 + 4.0, 30.0, 150.0), 1)


@dataclass
class Section:
    name: str          # intro / develop / resolve
    label: str         # 引入 / 展开 / 收束
    bars: list[int]    # [first, last], 1-based inclusive
    parts: list[str]


@dataclass
class ArrangementSpec:
    feel: str
    knobs: dict
    key: str
    mood_label: str
    bpm: int
    bars: int
    duration_s: float
    tail_s: float
    preview: bool
    progression: list[str]
    sections: list[Section]
    parts: list[str]
    piano_rhythm: str            # quarter / eighth
    arp: dict                    # {"on", "rate", "register"}
    drums: str                   # off / light / full
    pad_brightness_cc74: int
    duck_for_voice: bool
    voice_mode: str              # 不抢 / 平衡 / 偏配乐
    bed_lufs: float              # master loudness of the bed
    lead_melody: bool
    rit_bpm: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)   # plain-language explanation

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rit_bpm"] = {str(k): v for k, v in self.rit_bpm.items()}
        return d


# chord loops by mood (all chords exist in write_score.CHORDS / PAD_VOICINGS)
LOOPS = {
    "dark":    (["Dm", "Bb", "Gm", "A"], ["Dm", "Gm", "Bb", "A"]),
    "neutral": (["Dm", "Bb", "F", "C"], ["Dm", "Bb", "F", "C", "Gm", "Bb", "F", "A"]),
    "bright":  (["F", "C", "Dm", "Bb"], ["F", "C", "Bb", "C"]),
}
CADENCE = {
    "dark":    ["Bb", "Gm", "Asus", "Dm"],
    "neutral": ["Bb", "Gm", "Asus", "Dm"],
    "bright":  ["Dm", "Bb", "C", "F"],
}


def build_spec(text: str, knobs: dict | None = None, preview: bool = True,
               duration_s: float | None = None) -> ArrangementSpec:
    k = {**DEFAULT_KNOBS, **(knobs or {})}
    mood, speed, dens, bright = (clamp(float(k[x]), 0, 100) / 100 for x in ("mood", "speed", "density", "brightness"))
    voice = int(clamp(int(k["voice"]), 0, 2))
    notes: list[str] = []

    # tempo: 72..132 BPM (50 → 100 BPM, the original bed)
    bpm = int(round(72 + 60 * speed))
    if abs(speed - 0.5) < 0.02:
        bpm = 100

    if mood < 0.34:
        mood_key, key, mood_label = "dark", "D minor", "暗：小调、属和弦张力"
    elif mood < 0.67:
        mood_key, key, mood_label = "neutral", "D minor", "中性：原版科技解说循环"
    else:
        mood_key, key, mood_label = "bright", "F major", "亮：关系大调（同一组音，更明亮）"

    # length
    if preview:
        target = PREVIEW_SECONDS
        tail = TAIL_PREVIEW
    else:
        target = float(duration_s) if duration_s else (script_seconds(text) or EXPORT_SECONDS)
        tail = TAIL_FULL
    sec_per_bar = 240.0 / bpm
    bars = int(round((target - tail) / sec_per_bar))
    bars = int(clamp(bars, 6, 64))
    if not preview and abs(bpm - 100) < 1 and target == EXPORT_SECONDS:
        bars = 22                                     # exactly the original 22-bar form

    n_res = 2 if bars <= 10 else 4
    n_intro = 2 if bars <= 10 else max(2, min(6, round(bars * 0.27)))
    intro = (1, n_intro)
    develop = (n_intro + 1, bars - n_res)
    resolve = (bars - n_res + 1, bars)

    loop_a, loop_b = LOOPS[mood_key]
    prog = [loop_a[i % len(loop_a)] for i in range(n_intro)]
    prog += [loop_b[i % len(loop_b)] for i in range(develop[1] - develop[0] + 1)]
    cad = CADENCE[mood_key]
    if prog[-1] == cad[-n_res]:                       # avoid repeating the same chord into the cadence
        prog[-1] = "Bb" if cad[-n_res] != "Bb" else "Gm"
    prog += cad[-n_res:]

    rit_steps = [0.96, 0.9, 0.84, 0.76] if n_res == 4 else [0.9, 0.78]
    rit = {resolve[0] + i: int(round(bpm * f)) for i, f in enumerate(rit_steps)}

    # density → which parts play in the development section
    dev_parts = ["pad", "piano"]
    if dens >= 0.15:
        dev_parts.append("cello")
    lead = dens >= 0.3 and voice > 0
    if dens >= 0.3:
        dev_parts.append("viola")
    if lead:
        dev_parts.append("violin")
    if dens >= 0.35:
        dev_parts.append("bass")
    arp_on = dens >= 0.5 or bright >= 0.65
    if arp_on:
        dev_parts.append("arp")
    drums = "off" if dens < 0.55 else ("light" if dens < 0.8 else "full")
    if drums != "off":
        dev_parts.append("drums")
    arp = {"on": arp_on, "rate": "16th" if dens >= 0.75 else "8th",
           "register": "高" if bright >= 0.67 else ("中" if bright >= 0.34 else "低")}
    piano_rhythm = "eighth" if dens >= 0.25 else "quarter"
    res_parts = [p for p in ("pad", "piano", "violin", "viola", "cello", "bass") if p in dev_parts or p in ("pad", "piano")]

    sections = [
        Section("intro", "引入", list(intro), ["pad", "piano"]),
        Section("develop", "展开", list(develop), dev_parts),
        Section("resolve", "收束", list(resolve), res_parts),
    ]
    order = ["pad", "piano", "violin", "viola", "cello", "bass", "arp", "drums"]
    parts = [p for p in order if any(p in s.parts for s in sections)]

    bed_lufs = {0: -21.0, 1: -18.0, 2: -15.0}[voice]
    duck = voice < 2
    cc74 = int(round(20 + 100 * bright))

    names = {"pad": "铺底长音", "piano": "钢琴", "violin": "小提琴旋律", "viola": "中提琴", "cello": "大提琴",
             "bass": "贝斯", "arp": "电子琶音", "drums": "轻鼓"}
    notes.append(f"速度 {bpm} BPM，{bars} 小节 ≈ {round(bars * sec_per_bar + tail)} 秒")
    notes.append("用到的声部：" + "、".join(names[p] for p in parts))
    if not lead:
        notes.append("没有主旋律（不抢口播 / 很疏），只留和声与律动")
    notes.append({0: "整体压低到 −21 LUFS，并在人声频段（1.5–4 kHz）多让 3 dB",
                  1: "整体 −18 LUFS，人声频段让位（适合放在口播下面）",
                  2: "整体 −15 LUFS，音乐更突出（片头 / 无口播段落）"}[voice])
    if not preview and script_seconds(text):
        notes.append(f"根据口播稿字数估算时长约 {script_seconds(text)} 秒")

    return ArrangementSpec(
        feel=text or "", knobs={"mood": int(mood * 100), "speed": int(speed * 100), "density": int(dens * 100),
                                "brightness": int(bright * 100), "voice": voice},
        key=key, mood_label=mood_label, bpm=bpm, bars=bars,
        duration_s=round(bars * sec_per_bar + tail, 1), tail_s=tail, preview=preview,
        progression=prog, sections=sections, parts=parts, piano_rhythm=piano_rhythm, arp=arp, drums=drums,
        pad_brightness_cc74=cc74, duck_for_voice=duck, voice_mode=VOICE_LABELS[voice], bed_lufs=bed_lufs,
        lead_melody=lead, rit_bpm=rit, notes=notes,
    )
