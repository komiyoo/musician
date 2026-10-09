"""图片 → 旋钮（情绪 / 速度 / 密度 / 亮度 / 是否抢口播）→ 交给 spec.build_spec。

不用机器学习，只用 Pillow 量几个直观的画面特征，再按固定规则映射到 0..100 的旋钮
（和 spec.DEFAULT_KNOBS / build_spec 完全兼容），所以结果可解释、可复现、可以继续手动微调：

  主色相 (dominant hue)   暖色(红橙黄) → 情绪更亮、亮度更高；冷色(青蓝) → 情绪更暗、亮度略低；
                          紫色 → 神秘偏暗；绿色 → 中性略亮。按画面饱和度加权（灰图几乎不影响）。
  亮度 (luminance)        画面越亮 → 亮度旋钮越高、情绪略亮、速度略快；越暗 → 越低沉越慢。
  边缘密度 (edge density) 细节/线条越多 → 密度越高（更多声部、琶音、鼓）。
  色彩方差 (color var.)   颜色越丰富、对比越强 → 「能量」越高 → 速度更快、密度略高；
                          能量很高的画面 → 偏配乐（不 duck）；很安静的画面 → 不抢口播（多让位）。

  python -m src.feel.image_spec some.jpg      # 打印特征 + 旋钮 + 感觉描述（调试用）
"""
from __future__ import annotations

import colorsys
import io
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .spec import DEFAULT_KNOBS, VOICE_LABELS, clamp

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


def suggest_from_image(src) -> ImageSuggestion:
    f = extract_features(src)
    knobs, duck, feel, reasons = features_to_knobs(f)
    assert set(knobs) == set(DEFAULT_KNOBS)
    return ImageSuggestion(knobs=knobs, duck=duck, feel=feel, reasons=reasons, features=f)


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
    if not argv:
        print("usage: python -m src.feel.image_spec IMAGE [IMAGE...]", file=sys.stderr)
        return 2
    for p in argv:
        s = suggest_from_image(p)
        print(json.dumps({"image": p, **s.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
