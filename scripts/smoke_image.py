"""冒烟测试：生成一张小 PNG（或 --image 指定）→ src.feel.image_spec → build_spec；若给了 --url 再打 POST /api/from-image。

  python scripts/smoke_image.py                      # 颜色规则（离线）；环境里有 API Key 时再加测一次视觉解读
  python scripts/smoke_image.py --image 封面.jpg      # 用真实图片（推荐测视觉解读：测试 PNG 太抽象）
  python scripts/smoke_image.py --no-vision          # 只测颜色规则
  python scripts/smoke_image.py --url http://127.0.0.1:8765 [--preview]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from src.feel.image_spec import suggest_from_image  # noqa: E402
from src.feel.spec import DEFAULT_KNOBS, build_spec  # noqa: E402
from src.feel.vision import available_provider  # noqa: E402


def make_png() -> bytes:
    """64×48: warm sunset gradient top, dark blue bottom with a few bright lines (some edges, some variance)."""
    im = Image.new("RGB", (64, 48))
    d = ImageDraw.Draw(im)
    for y in range(48):
        t = y / 47
        d.line([(0, y), (63, y)], fill=(int(250 - 220 * t), int(150 - 120 * t), int(60 + 60 * t)))
    for x in range(4, 64, 12):
        d.line([(x, 30), (x + 6, 46)], fill=(255, 230, 120))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def post_image(url: str, data: bytes, preview: bool, vision: bool = False) -> dict:
    b = uuid.uuid4().hex
    parts = [
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="smoke.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        + data + b"\r\n",
        f'--{b}\r\nContent-Disposition: form-data; name="preview"\r\n\r\n{int(preview)}\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="fallback"\r\n\r\n1\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="vision"\r\n\r\n{int(vision)}\r\n'.encode(),
        f"--{b}--\r\n".encode(),
    ]
    req = urllib.request.Request(url.rstrip("/") + "/api/from-image", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--image", help="用真实图片代替生成的测试 PNG")
    ap.add_argument("--no-vision", action="store_true", help="跳过视觉解读（即使有 API Key）")
    a = ap.parse_args()
    png = Path(a.image).read_bytes() if a.image else make_png()
    s = suggest_from_image(png, use_vision=False)
    assert set(s.knobs) == set(DEFAULT_KNOBS), s.knobs
    assert all(0 <= s.knobs[k] <= 100 for k in ("mood", "speed", "density", "brightness")) and s.knobs["voice"] in (0, 1, 2)
    spec = build_spec(s.feel, s.knobs, preview=True)
    print("[smoke] knobs", s.knobs, "duck", s.duck)
    print("[smoke] feel ", s.feel)
    print("[smoke] spec ", spec.key, spec.bpm, "BPM", spec.bars, "bars", spec.parts)
    if a.url:
        d = post_image(a.url, png, a.preview, vision=False)
        assert d["knobs"] == s.knobs, (d["knobs"], s.knobs)
        assert d["thumbnail"].startswith("data:image/jpeg;base64,")
        print("[smoke] api  ", d["knobs"], d["voice_label"], "preview:", (d["preview"] or {}).get("audio"))
        if a.preview:
            assert d["preview"] and d["preview"]["audio"], "no preview audio"
    if not a.no_vision:
        prov = available_provider()
        if prov is None:
            print("[smoke] vision: 跳过（未设置 OPENAI_API_KEY / ANTHROPIC_API_KEY）；颜色兜底见上")
        else:
            v = suggest_from_image(png, use_vision=True)
            assert v.source == "vision", f"视觉解读失败：{v.vision_error}"
            print(f"[smoke] vision {v.vision['provider']}/{v.vision['model']}"
                  f"{' (cache)' if v.vision['cached'] else ''} → knobs {v.knobs} (颜色规则 {v.color_knobs})")
            for ang in v.vision["angles"]:
                print(f"[smoke]   {ang['title']}：{ang['text']}  {' '.join(ang['keywords'])}")
            vs = build_spec(v.feel, v.knobs, preview=True, image_reading=v.image_reading)
            print("[smoke] vision spec", vs.key, vs.bpm, "BPM", vs.parts)
            if a.url:
                d = post_image(a.url, png, a.preview, vision=True)
                assert d["source"] == "vision" and len(d["vision"]["angles"]) >= 3, d.get("vision_error")
                print("[smoke] api vision ok", d["knobs"])
    print("[smoke] ok")


if __name__ == "__main__":
    main()
