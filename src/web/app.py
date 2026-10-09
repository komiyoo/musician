"""FastAPI app: 网页界面 → src.feel（文字感觉 + 旋钮 → 编曲规格 → MIDI → 渲染 + 混音）。

  GET  /                 web/index.html (static frontend)
  POST /api/parse        {"feel"} → suggested knobs + reasons
  POST /api/generate     {"feel","knobs","full":false,"fallback":false} → audio URLs + 编曲规格
  GET  /media/<file>     rendered WAV / MP3 / MIDI from out/web/

Run:  python -m src.web.app [--port 8765] [--fallback]   (or: musician serve / make web)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import config as C
from src.feel import render as R
from src.feel.spec import DEFAULT_KNOBS, VOICE_LABELS, build_spec, parse_feel

WEB_DIR = C.ROOT / "web"
FORCE_FALLBACK = os.environ.get("CTM_WEB_FALLBACK", "") not in ("", "0")

app = FastAPI(title="musician · 文字生成配乐")


class Knobs(BaseModel):
    mood: int = Field(DEFAULT_KNOBS["mood"], ge=0, le=100)
    speed: int = Field(DEFAULT_KNOBS["speed"], ge=0, le=100)
    density: int = Field(DEFAULT_KNOBS["density"], ge=0, le=100)
    brightness: int = Field(DEFAULT_KNOBS["brightness"], ge=0, le=100)
    voice: int = Field(DEFAULT_KNOBS["voice"], ge=0, le=2)


class ParseReq(BaseModel):
    feel: str = ""


class GenReq(BaseModel):
    feel: str = ""
    knobs: Knobs | None = None       # None → derive from the text
    full: bool = False               # False = ~20 s 试听, True = 完整轨
    fallback: bool = False           # True = 草稿音色（numpy 兜底合成器，最快）


def _url(path: str | None) -> str | None:
    return f"/media/{Path(path).name}" if path else None


@app.get("/api/health")
def health():
    from src.render import sfizz_render, surge_render
    return {"ok": True, "force_fallback": FORCE_FALLBACK, "surge": surge_render.available_backend(),
            "sfizz": sfizz_render.sfizz_available(), "voice_labels": VOICE_LABELS, "defaults": DEFAULT_KNOBS}


@app.post("/api/parse")
def parse(req: ParseReq):
    knobs, reasons = parse_feel(req.feel)
    return {"knobs": knobs, "reasons": reasons}


@app.post("/api/generate")
def generate(req: GenReq):
    reasons: list[str] = []
    if req.knobs is None:
        knobs, reasons = parse_feel(req.feel)
    else:
        knobs = req.knobs.model_dump()
    spec = build_spec(req.feel, knobs, preview=not req.full)
    try:
        res = R.generate(spec, fallback=req.fallback or FORCE_FALLBACK)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"生成失败：{e}") from e
    engines = sorted({p["engine"] for p in res["parts"].values()})
    return {
        "id": res["id"], "kind": res["kind"], "knobs": knobs, "reasons": reasons,
        "audio": _url(res["mp3"]) or _url(res["wav"]), "wav": _url(res["wav"]), "mp3": _url(res["mp3"]),
        "midi": _url(res["mid"]), "duration_s": res["duration_s"], "lufs": res["lufs"],
        "rendered": res["rendered"], "reused": res["reused"], "seconds": res["seconds"],
        "engines": engines, "spec": res["spec"], "wav_path": res["wav"],
    }


@app.get("/media/{name}")
def media(name: str):
    p = (R.OUT_WEB / name).resolve()
    if p.parent != R.OUT_WEB.resolve() or not p.is_file():
        raise HTTPException(404)
    types = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".mid": "audio/midi"}
    return FileResponse(p, media_type=types.get(p.suffix, "application/octet-stream"),
                        filename=name if p.suffix != ".mp3" else None)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main(argv=None):
    global FORCE_FALLBACK
    ap = argparse.ArgumentParser(prog="musician serve", description="启动网页界面")
    ap.add_argument("--host", default=os.environ.get("CTM_WEB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CTM_WEB_PORT", "8765")))
    ap.add_argument("--fallback", action="store_true", help="所有请求都用兜底合成器（不装 Surge/sfizz 也能用，最快）")
    a = ap.parse_args(argv)
    if a.fallback:
        FORCE_FALLBACK = True
        os.environ["CTM_WEB_FALLBACK"] = "1"
    import uvicorn
    print(f"[web] 打开浏览器访问 http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}/")
    import sys; sys.stdout.flush()
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
