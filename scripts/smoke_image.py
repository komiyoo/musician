"""冒烟测试：生成一张小 PNG → src.feel.image_spec → build_spec；若给了 --url 再打 POST /api/from-image。

  python scripts/smoke_image.py                      # 只测映射（不需要 web 依赖）
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


def post_image(url: str, data: bytes, preview: bool) -> dict:
    b = uuid.uuid4().hex
    parts = [
        f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="smoke.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        + data + b"\r\n",
        f'--{b}\r\nContent-Disposition: form-data; name="preview"\r\n\r\n{int(preview)}\r\n'.encode(),
        f'--{b}\r\nContent-Disposition: form-data; name="fallback"\r\n\r\n1\r\n'.encode(),
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
    a = ap.parse_args()
    png = make_png()
    s = suggest_from_image(png)
    assert set(s.knobs) == set(DEFAULT_KNOBS), s.knobs
    assert all(0 <= s.knobs[k] <= 100 for k in ("mood", "speed", "density", "brightness")) and s.knobs["voice"] in (0, 1, 2)
    spec = build_spec(s.feel, s.knobs, preview=True)
    print("[smoke] knobs", s.knobs, "duck", s.duck)
    print("[smoke] feel ", s.feel)
    print("[smoke] spec ", spec.key, spec.bpm, "BPM", spec.bars, "bars", spec.parts)
    if a.url:
        d = post_image(a.url, png, a.preview)
        assert d["knobs"] == s.knobs, (d["knobs"], s.knobs)
        assert d["thumbnail"].startswith("data:image/jpeg;base64,")
        print("[smoke] api  ", d["knobs"], d["voice_label"], "preview:", (d["preview"] or {}).get("audio"))
        if a.preview:
            assert d["preview"] and d["preview"]["audio"], "no preview audio"
    print("[smoke] ok")


if __name__ == "__main__":
    main()
