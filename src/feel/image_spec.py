"""图片 → 旋钮（情绪 / 速度 / 密度 / 亮度 / 是否抢口播）→ 交给 spec.build_spec。

两条路：
  ① 视觉解读（默认，主路径）：src/feel/vision.py 让看图大模型从六个角度读图（情绪氛围 / 画面内容 /
     给人的感觉 / 可能的故事·场景 / 节奏暗示 / 色调与光影情绪）并给出 energy / brightness / density /
     调性 / BPM / 是否让位口播 → fuse_vision() 融合成旋钮；各角度文字里的情绪词再轻推一下（≤ ±10）；
     下面的 Pillow 颜色特征只做 ±10 的轻微修正。整份解读写进 ArrangementSpec.image_reading。
  ② 颜色规则（--no-vision / 没配 API Key / 视觉模型失败时自动兜底）：

只用 Pillow 量几个直观的画面特征，再按固定规则映射到 0..100 的旋钮
（和 spec.DEFAULT_KNOBS / build_spec 完全兼容），所以结果可解释、可复现、可以继续手动微调：

  主色相 (dominant hue)   暖色(红橙黄) → 情绪更亮、亮度更高；冷色(青蓝) → 情绪更暗、亮度略低；
                          紫色 → 神秘偏暗；绿色 → 中性略亮。按画面饱和度加权（灰图几乎不影响）。
  亮度 (luminance)        画面越亮 → 亮度旋钮越高、情绪略亮、速度略快；越暗 → 越低沉越慢。
  边缘密度 (edge density) 细节/线条越多 → 密度越高（更多声部、琶音、鼓）。
  色彩方差 (color var.)   颜色越丰富、对比越强 → 「能量」越高 → 速度更快、密度略高；
                          能量很高的画面 → 偏配乐（不 duck）；很安静的画面 → 不抢口播（多让位）。

  python -m src.feel.image_spec some.jpg              # 视觉解读 + 颜色修正 → 旋钮（无 key 时自动退回颜色规则）
  python -m src.feel.image_spec --no-vision some.jpg  # 只用颜色规则（离线）
"""
from __future__ import annotations

import colorsys
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .spec import DEFAULT_KNOBS, KEYWORDS, VOICE_LABELS, clamp

COLOR_BIAS_MAX = 10         # Pillow colour features may move a vision knob by at most ±10
COLOR_BIAS_GAIN = 0.35      # … proportional to (colour-rule knob − vision knob)
ANGLE_NUDGE_MAX = 10        # emotion words inside the angle texts may move a knob by at most ±10
ANGLE_NUDGE_GAIN = 0.3      # spec.KEYWORDS deltas (±25) × 0.3 ≈ ±7 per hit
FEEL_MAX_CHARS = 100        # keep the feel line < 120 chars (spec.script_seconds treats long text as a 口播稿)

ANALYZE_SIZE = 256          # longest side used for analysis (fast, stable across resolutions)
THUMB_SIZE = 320            # longest side of the preview thumbnail returned to the UI
EDGE_THRESHOLD = 40         # FIND_EDGES response (0..255) counted as an edge pixel
EDGE_FULL = 0.30            # edge fraction treated as "maximum detail"
COLOR_STD_FULL = 90.0       # mean RGB std (0..~128) treated as "maximum colour variance"
SAT_MIN = 0.18              # pixels below this saturation (or very dark) don't vote for the hue

# hue families (degrees, 0..360) → (name, colour word, mood delta, brightness delta)
HUE_FAMILIES = [
    ((345, 360), "red",    "红",   +14, +8),
    ((0, 20),    "red",    "红",   +14, +8),
    ((20, 50),   "orange", "橙",   +22, +14),
    ((50, 70),   "yellow", "黄",   +24, +18),
    ((70, 160),  "green",  "绿",   +8,  +4),
    ((160, 200), "cyan",   "青",   -8,  0),
    ((200, 260), "blue",   "蓝",   -18, -8),
    ((260, 300), "purple", "紫",   -22, -10),
    ((300, 345), "magenta", "品红", +4,  +4),
]


@dataclass
class ImageFeatures:
    width: int
    height: int
    dominant_hue_deg: float | None    # None = 画面基本无彩色
    hue_family: str                   # red / orange / ... / neutral
    hue_word: str                     # 中文色名
    hue_share: float                  # 0..1 主色相在有彩色像素中的占比
    colorfulness: float               # 0..1 有彩色像素占比
    saturation: float                 # 0..1 平均饱和度
    luminance: float                  # 0..1 平均亮度 (Rec.601 L)
    contrast: float                   # 0..1 亮度标准差 / 0.5
    edge_density: float               # 0..1 边缘像素占比
    color_variance: float             # 0..1 归一化 RGB 标准差
    energy: float                     # 0..1 综合能量（色彩方差 + 饱和度 + 对比）
    palette: list[str] = field(default_factory=list)   # 主色 hex（给 UI 显示）


@dataclass
class ImageSuggestion:
    knobs: dict
    duck: bool
    feel: str
    reasons: list[str]
    features: ImageFeatures
    source: str = "color"                 # "vision" (看图模型解读 + 颜色修正) / "color" (纯颜色规则)
    vision: dict | None = None            # VisionResult.to_dict() when source == "vision"
    vision_error: str | None = None       # why we fell back (no key / timeout / bad JSON …); None if not tried
    color_knobs: dict | None = None       # what the colour rules alone would have said
    image_reading: dict | None = None     # → build_spec(image_reading=…) / ArrangementSpec.image_reading

    def to_dict(self) -> dict:
        d = asdict(self)
        d["voice_label"] = VOICE_LABELS[self.knobs["voice"]]
        return d


def _open(src) -> "Image.Image":
    from PIL import Image, ImageOps
    if isinstance(src, (bytes, bytearray)):
        im = Image.open(io.BytesIO(src))
    elif isinstance(src, (str, Path)):
        im = Image.open(src)
    else:
        im = src
    im.load()
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001 — broken EXIF shouldn't kill the upload
        pass
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))   # transparency → white
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def _hue_family(deg: float) -> tuple:
    for (lo, hi), *rest in HUE_FAMILIES:
        if lo <= deg < hi:
            return tuple(rest)
    return HUE_FAMILIES[0][1:]


def extract_features(src) -> ImageFeatures:
    """src: path / bytes / PIL.Image."""
    import numpy as np
    from PIL import Image, ImageFilter

    im = _open(src)
    w, h = im.size
    small = im.copy()
    small.thumbnail((ANALYZE_SIZE, ANALYZE_SIZE), Image.Resampling.LANCZOS)

    rgb = np.asarray(small, dtype=np.float32)
    hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
    hue, sat, val = hsv[..., 0] * 360.0, hsv[..., 1], hsv[..., 2]
    lum = np.asarray(small.convert("L"), dtype=np.float32) / 255.0

    # dominant hue: 36 × 10° bins, each pixel votes with saturation × value
    chroma = (sat >= SAT_MIN) & (val >= 0.15)
    colorfulness = float(chroma.mean())
    weights = (sat * val)[chroma]
    if colorfulness >= 0.04 and weights.sum() > 0:
        hist, _ = np.histogram(hue[chroma], bins=36, range=(0, 360), weights=weights)
        smooth = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))     # circular smoothing
        b = int(np.argmax(smooth))
        dom = b * 10 + 5.0
        family, word, *_ = _hue_family(dom)
        fam_mask = np.array([_hue_family(x)[0] == family for x in range(0, 360, 10)])
        hue_share = float(hist[fam_mask].sum() / hist.sum())
    else:
        dom, family, word, hue_share = None, "neutral", "灰", 0.0

    # edges on the luminance channel (light blur first so JPEG noise / grain doesn't count)
    edges = np.asarray(small.convert("L").filter(ImageFilter.GaussianBlur(0.8)).filter(ImageFilter.FIND_EDGES),
                       dtype=np.float32)[1:-1, 1:-1]
    edge_density = float((edges > EDGE_THRESHOLD).mean()) if edges.size else 0.0

    color_std = float(rgb.reshape(-1, 3).std(axis=0).mean())
    color_variance = float(clamp(color_std / COLOR_STD_FULL, 0, 1))
    contrast = float(clamp(lum.std() / 0.5, 0, 1))
    saturation = float(sat.mean())
    energy = float(clamp(0.5 * color_variance + 0.3 * clamp(saturation / 0.6, 0, 1) + 0.2 * contrast, 0, 1))

    # small palette for the UI (adaptive quantise of the thumbnail)
    q = small.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:15]
    counts = sorted(q.getcolors() or [], reverse=True)
    palette = ["#%02x%02x%02x" % tuple(pal[i * 3:i * 3 + 3]) for _, i in counts[:5]]

    return ImageFeatures(
        width=w, height=h, dominant_hue_deg=dom, hue_family=family, hue_word=word,
        hue_share=round(hue_share, 3), colorfulness=round(colorfulness, 3), saturation=round(saturation, 3),
        luminance=round(float(lum.mean()), 3), contrast=round(contrast, 3),
        edge_density=round(edge_density, 3), color_variance=round(color_variance, 3),
        energy=round(energy, 3), palette=palette,
    )


def _level(x: float, words: tuple[str, str, str]) -> str:
    return words[0] if x < 0.34 else (words[1] if x < 0.67 else words[2])


def features_to_knobs(f: ImageFeatures) -> tuple[dict, bool, str, list[str]]:
    """Fixed, documented rules → (knobs, duck, feel text, reasons). Knobs are 0..100, voice 0/1/2."""
    reasons: list[str] = []
    lum, edge = f.luminance, clamp(f.edge_density / EDGE_FULL, 0, 1)
    energy = f.energy

    # 1) dominant hue → mood / brightness, weighted by how colourful the picture is
    hue_mood = hue_bright = 0.0
    if f.dominant_hue_deg is not None:
        _, _, dm, db = _hue_family(f.dominant_hue_deg)
        strength = clamp(f.colorfulness * 1.4, 0, 1) * clamp(0.4 + f.hue_share, 0, 1)
        hue_mood, hue_bright = dm * strength, db * strength
        tone = "暖" if f.hue_family in ("red", "orange", "yellow") else (
            "冷" if f.hue_family in ("cyan", "blue", "purple") else "中性")
        reasons.append(f"主色调 {f.hue_word}（{tone}色，约 {f.dominant_hue_deg:.0f}°）→ 情绪 {hue_mood:+.0f}、亮度 {hue_bright:+.0f}")
    else:
        reasons.append("画面基本无彩色（黑白/灰）→ 色相不影响情绪，只看明暗")

    # 2) luminance → brightness (main), mood + speed (bias)
    lum_c = lum - 0.5
    mood = 40 + hue_mood + 40 * lum_c
    brightness = 15 + 75 * lum + hue_bright
    reasons.append(f"平均亮度 {lum:.0%} → 亮度旋钮 {'偏高' if lum > 0.55 else ('偏低' if lum < 0.4 else '居中')}，"
                   f"情绪 {40 * lum_c:+.0f}")

    # 3) colour variance / energy → speed (+ a little density)
    speed = 50 + 50 * (energy - 0.45) + 20 * lum_c
    reasons.append(f"色彩方差 {f.color_variance:.0%}、饱和度 {f.saturation:.0%} → 能量 {energy:.0%} → "
                   f"速度 {50 * (energy - 0.45) + 20 * lum_c:+.0f}")

    # 4) edge density → density
    density = 15 + 75 * edge + 15 * (energy - 0.45)
    reasons.append(f"边缘密度 {f.edge_density:.0%}（细节{_level(edge, ('少', '中等', '多'))}）→ 密度 {int(clamp(density, 0, 100))}")

    # 5) energy (+ detail) → voice / duck
    busy = 0.6 * energy + 0.4 * edge
    voice = 2 if busy >= 0.62 else (0 if busy < 0.22 else 1)
    duck = voice < 2                              # same rule as spec.build_spec
    reasons.append(f"画面{'很热闹' if voice == 2 else ('很安静' if voice == 0 else '适中')} → "
                   f"{VOICE_LABELS[voice]}（{'不 duck，音乐更突出' if not duck else '给人声让位 duck'}）")

    knobs = {"mood": int(round(clamp(mood, 0, 100))), "speed": int(round(clamp(speed, 0, 100))),
             "density": int(round(clamp(density, 0, 100))), "brightness": int(round(clamp(brightness, 0, 100))),
             "voice": voice}

    # plain-language feel text (deliberately not reusing spec.KEYWORDS so it doesn't re-move the knobs)
    tone = {"red": "红色调", "orange": "暖橙色调", "yellow": "金黄色调", "green": "绿色调", "cyan": "青色调",
            "blue": "冷蓝色调", "purple": "紫色调", "magenta": "品红色调", "neutral": "黑白灰调"}[f.hue_family]
    feel = (f"图片配乐：{tone}、画面{_level(lum, ('偏暗', '明暗适中', '偏亮'))}、"
            f"细节{_level(edge, ('少', '适中', '多'))}、色彩{_level(f.color_variance, ('单一', '适中', '多'))}"
            f" → 情绪{_level(knobs['mood'] / 100, ('偏暗', '中性', '偏亮'))}、"
            f"{_level(knobs['speed'] / 100, ('慢', '中速', '快'))}板、"
            f"层次{_level(knobs['density'] / 100, ('疏', '适中', '密'))}，{VOICE_LABELS[voice]}")
    return knobs, duck, feel, reasons


def _angle_nudges(angles: list[dict]) -> tuple[dict, list[str]]:
    """Emotion words found in each angle's text/keywords (spec.KEYWORDS) → small knob nudges.
    The 节奏暗示 angle counts double for speed/density. voice is left to suggested_duck."""
    tot = {"mood": 0.0, "speed": 0.0, "density": 0.0, "brightness": 0.0}
    why: list[str] = []
    for a in angles:
        blob = (a["text"] + " " + " ".join(a["keywords"])).lower()
        for words, delta, label in KEYWORDS:
            hits = [w for w in words if w.lower() in blob]
            if not hits:
                continue
            moved = []
            for k, d in delta.items():
                if k not in tot:
                    continue
                w = ANGLE_NUDGE_GAIN * (2 if a["key"] == "rhythm" and k in ("speed", "density") else 1)
                tot[k] += d * w
                moved.append(k)
            if moved:
                why.append(f"{a['title']}「{hits[0]}」→ {label}")
    tot = {k: clamp(v, -ANGLE_NUDGE_MAX, ANGLE_NUDGE_MAX) for k, v in tot.items()}
    return tot, why


def fuse_vision(vr, color_knobs: dict, f: ImageFeatures) -> tuple[dict, bool, str, list[str], dict]:
    """Vision interpretation (primary) + angle word nudges + colour features (mild ±10 bias) → knobs.

    mood       ← 情绪明亮度 brightness, held on the side of the suggested key (Dm < 67 ≤ F, see build_spec)
    speed      ← suggested_bpm (70%) + energy (30%)
    density    ← density (75%) + energy (25%)
    brightness ← brightness (60%) + key colour (F +10 / Dm −5) + energy (20%)
    voice      ← suggested_duck (0 不抢 / 1 平衡 / 2 偏配乐)
    """
    reasons: list[str] = [f"看图模型（{vr.provider}/{vr.model}{'，缓存' if vr.cached else ''}）从 {len(vr.angles)} 个角度读图，"
                          "以下旋钮以它为主，颜色只做 ±10 修正："]
    bpm_speed = (vr.suggested_bpm - 72) / 60 * 100
    base = {
        "mood": float(vr.brightness),
        "speed": 0.7 * bpm_speed + 0.3 * vr.energy,
        "density": 0.75 * vr.density + 0.25 * vr.energy,
        "brightness": 0.6 * vr.brightness + 0.2 * vr.energy + 20 + (10 if vr.suggested_key == "F" else -5),
    }
    key_word = "F 大调（明亮温暖）" if vr.suggested_key == "F" else "D 小调（沉静/悬疑）"
    reasons.append(f"情绪明亮度 {vr.brightness}、建议 {key_word} → 情绪 {base['mood']:.0f}")
    reasons.append(f"能量 {vr.energy}、建议 {vr.suggested_bpm} BPM → 速度 {base['speed']:.0f}")
    reasons.append(f"画面信息量 {vr.density} → 密度 {base['density']:.0f}")

    nudge, why = _angle_nudges(vr.angles)
    for k, v in nudge.items():
        base[k] += v
    if why:
        reasons.append("各角度的情绪词微调：" + "；".join(why[:5]) + " → "
                       + "、".join(f"{n} {nudge[k]:+.0f}" for k, n in
                                  (("mood", "情绪"), ("speed", "速度"), ("density", "密度"), ("brightness", "亮度"))
                                  if abs(nudge[k]) >= 0.5))

    bias = {k: clamp(COLOR_BIAS_GAIN * (color_knobs[k] - base[k]), -COLOR_BIAS_MAX, COLOR_BIAS_MAX) for k in base}
    for k in base:
        base[k] += bias[k]
    reasons.append(f"颜色修正（{f.hue_word}色调、亮度 {f.luminance:.0%}、细节 {f.edge_density:.0%}）："
                   + "、".join(f"{n} {bias[k]:+.0f}" for k, n in
                              (("mood", "情绪"), ("speed", "速度"), ("density", "密度"), ("brightness", "亮度"))))

    mood = base["mood"]
    mood = max(mood, 68) if vr.suggested_key == "F" else min(mood, 64)        # keep build_spec on the suggested key
    voice = int(vr.suggested_duck)
    knobs = {"mood": int(round(clamp(mood, 0, 100))), "speed": int(round(clamp(base["speed"], 0, 100))),
             "density": int(round(clamp(base["density"], 0, 100))),
             "brightness": int(round(clamp(base["brightness"], 0, 100))), "voice": voice}
    duck = voice < 2
    reasons.append(f"模型建议 → {VOICE_LABELS[voice]}（{'给人声让位 duck' if duck else '不 duck，音乐更突出'}）")

    head = vr.content_zh or vr.mood_zh
    tail = "、".join(vr.all_keywords()[:4])
    feel = f"图片配乐：{head}"
    if vr.mood_zh and vr.mood_zh != head:
        feel += f"；{vr.mood_zh}"
    if len(feel) > FEEL_MAX_CHARS - len(tail) - 4:
        feel = feel[:FEEL_MAX_CHARS - len(tail) - 5] + "…"
    if tail:
        feel += f"（{tail}）"
    reading = {"source": "vision", "provider": vr.provider, "model": vr.model, "angles": vr.angles,
               "keywords": vr.all_keywords()[:8], "suggested_key": vr.suggested_key,
               "suggested_bpm": vr.suggested_bpm, "suggested_duck": vr.suggested_duck,
               "energy": vr.energy, "brightness": vr.brightness, "density": vr.density,
               "narration_hint": vr.narration_hint, "color_bias": {k: round(v, 1) for k, v in bias.items()},
               "angle_nudge": {k: round(v, 1) for k, v in nudge.items()}}
    return knobs, duck, feel, reasons, reading


def suggest_from_image(src, use_vision: bool = True, timeout: float | None = None) -> ImageSuggestion:
    """use_vision=True: 看图模型解读为主（失败自动退回颜色规则，原因写在 vision_error）；False: 只用颜色规则。"""
    if isinstance(src, (str, Path)):
        src = Path(src).read_bytes()
    f = extract_features(src)
    knobs, duck, feel, reasons = features_to_knobs(f)
    assert set(knobs) == set(DEFAULT_KNOBS)
    color = ImageSuggestion(knobs=knobs, duck=duck, feel=feel, reasons=reasons, features=f, color_knobs=dict(knobs))
    if not use_vision:
        return color
    from .vision import VisionError, interpret_image
    try:
        vr = interpret_image(src, timeout=timeout) if isinstance(src, (bytes, bytearray)) else None
        if vr is None:
            raise VisionError("视觉解读需要原始图片文件")
    except VisionError as e:
        color.vision_error = str(e)
        return color
    vk, vduck, vfeel, vreasons, reading = fuse_vision(vr, knobs, f)
    return ImageSuggestion(knobs=vk, duck=vduck, feel=vfeel, reasons=vreasons, features=f, source="vision",
                           vision=vr.to_dict(), vision_error=None, color_knobs=dict(knobs), image_reading=reading)


def thumbnail_data_url(src, size: int = THUMB_SIZE) -> str:
    """Small JPEG data: URL for the UI preview (EXIF-rotated, transparency flattened)."""
    import base64
    im = _open(src)
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    use_vision = "--no-vision" not in argv
    argv = [a for a in argv if a != "--no-vision"]
    if not argv:
        print("usage: python -m src.feel.image_spec [--no-vision] IMAGE [IMAGE...]", file=sys.stderr)
        return 2
    for p in argv:
        s = suggest_from_image(p, use_vision=use_vision)
        if s.vision_error:
            print(f"[image] 视觉解读未启用：{s.vision_error}", file=sys.stderr)
        print(json.dumps({"image": p, **s.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
