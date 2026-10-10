"""FastAPI app: 网页界面 → src.feel（文字感觉 + 旋钮 → 编曲规格 → MIDI → 渲染 + 混音）。

  GET  /                 web/index.html (static frontend)
  POST /api/parse        {"feel"} → suggested knobs + reasons
  POST /api/generate     {"feel","knobs","full":false,"fallback":false} → audio URLs + 编曲规格
  POST /api/from-image   multipart: file=<图片> [, vision=1/0, preview=0/1, fallback=0/1]
                         → 多角度读图（vision=1，看图模型）+ 建议旋钮 + 感觉描述 + 画面特征 + 缩略图
                           （没配 API Key / 失败时自动退回颜色规则，原因在 vision_error；preview=1 时顺便渲试听）
  POST /api/markov-variation  GenReq + {"temperature":0.9,"seed":null} → 钢琴声部按马尔科夫链重新采样音高，
                         其余声部复用缓存，重新混出变奏试听（+ markov 图 JSON）
  POST /api/markov-demo  {"temperature","seed"} → 《致艾丽丝》动机的马尔科夫图 + 原动机 / 变奏两段音频
  GET  /media/<file>     rendered WAV / MP3 / MIDI from out/web/

Run:  python -m src.web.app [--port 8765] [--fallback]   (or: musician serve / make web)
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.envfile import load_env

load_env()  # noqa: E402 — local .env before reading CTM_* / API keys

from src import config as C  # noqa: E402
from src.feel import render as R
from src.feel.spec import DEFAULT_KNOBS, VOICE_LABELS, build_spec, parse_feel

MAX_IMAGE_BYTES = 20 * 1024 * 1024

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
    image_reading: dict | None = None  # 图片模式：/api/from-image 返回的多角度读图，原样带回写进编曲规格


class VarReq(GenReq):
    temperature: float = Field(0.9, ge=0.0, le=3.0)   # p ∝ P ** (1/T)
    seed: int | None = None                           # None → 随机（每次点击都不一样）
    harmony: float = Field(3.0, ge=1.0, le=10.0)      # 同小节和弦内音的额外权重


class DemoReq(BaseModel):
    temperature: float = Field(0.9, ge=0.0, le=3.0)
    seed: int | None = None


MARKOV_PART = "piano"


def _markov_graph(res: dict) -> dict | None:
    """Graph JSON of the piano part's pitch transitions, read back from its rendered MIDI."""
    from src.markov import MarkovChain, sequence_from_midi
    mid = res.get("part_midi", {}).get(MARKOV_PART)
    if not mid:
        return None
    seq = sequence_from_midi(mid, channel=C.PARTS[MARKOV_PART]["channel"])
    return {"part": MARKOV_PART, "part_label": "钢琴", "n_notes": len(seq), **MarkovChain.fit(seq).graph()}


def _url(path: str | None) -> str | None:
    return f"/media/{Path(path).name}" if path else None


@app.get("/api/health")
def health():
    from src.feel.vision import available_provider
    from src.render import sfizz_render, surge_render
    return {"ok": True, "force_fallback": FORCE_FALLBACK, "surge": surge_render.available_backend(),
            "vision": available_provider(),
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
    return _render(req.feel, knobs, reasons, full=req.full, fallback=req.fallback, image_reading=req.image_reading)


def _render(feel: str, knobs: dict, reasons: list[str], full: bool = False, fallback: bool = False,
            image_reading: dict | None = None, score_hook=None, variant: str = "") -> dict:
    spec = build_spec(feel, knobs, preview=not full, image_reading=image_reading)
    try:
        res = R.generate(spec, fallback=fallback or FORCE_FALLBACK, score_hook=score_hook, variant=variant)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"生成失败：{e}") from e
    engines = sorted({p["engine"] for p in res["parts"].values()})
    return {
        "id": res["id"], "kind": res["kind"], "knobs": knobs, "reasons": reasons,
        "audio": _url(res["mp3"]) or _url(res["wav"]), "wav": _url(res["wav"]), "mp3": _url(res["mp3"]),
        "midi": _url(res["mid"]), "duration_s": res["duration_s"], "lufs": res["lufs"],
        "rendered": res["rendered"], "reused": res["reused"], "seconds": res["seconds"],
        "engines": engines, "spec": res["spec"], "wav_path": res["wav"], "markov": _markov_graph(res),
    }


@app.post("/api/markov-variation")
def markov_variation(req: VarReq):
    """钢琴声部：统计原来的音高转移 → 温度采样新音高（节奏/力度不变）→ 只重渲钢琴，重新混音。"""
    import random

    from src.markov.vary import vary_score_part
    knobs = req.knobs.model_dump() if req.knobs else parse_feel(req.feel)[0]
    seed = random.randrange(1_000_000) if req.seed is None else req.seed
    info = {}

    def hook(score):
        r = vary_score_part(score, MARKOV_PART, req.temperature, seed, req.harmony)
        info.update(changed=r["changed"], n_notes=r["n_notes"], source=r["chain"].graph())
        return None

    out = _render(req.feel, knobs, [], full=req.full, fallback=req.fallback, image_reading=req.image_reading,
                  score_hook=hook, variant=f"markov:{MARKOV_PART}:{req.temperature:.3f}:{seed}:{req.harmony:.2f}")
    if not info:   # job came fully from cache → hook still ran (compose always runs), so this is just a guard
        raise HTTPException(500, "马尔科夫变奏失败")
    out["variation"] = {"part": MARKOV_PART, "temperature": req.temperature, "seed": seed, "harmony": req.harmony,
                        "changed": info["changed"], "n_notes": info["n_notes"]}
    out["markov_source"] = {"part": MARKOV_PART, "part_label": "钢琴", **info["source"]}   # 采样用的原链
    return out


@app.post("/api/markov-demo")
def markov_demo(req: DemoReq):
    from src.markov.demo import demo
    return demo(req.temperature, req.seed, R.OUT_WEB)


@app.post("/api/from-image")
async def from_image(file: UploadFile = File(...), preview: bool = Form(False), fallback: bool = Form(False),
                     vision: bool = Form(True)):
    """图片 → 多角度读图（看图模型，vision=1）+ 建议旋钮 + 感觉描述；颜色规则做 ±10 修正 / 兜底
    （见 src/feel/vision.py、image_spec.py）；preview=1 时再渲一段 ~20 秒试听。"""
    from PIL import Image, UnidentifiedImageError
    from starlette.concurrency import run_in_threadpool

    from src.feel.image_spec import suggest_from_image, thumbnail_data_url
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(400, "没有收到图片")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片太大（上限 20 MB）")
    try:
        sug = await run_in_threadpool(suggest_from_image, data, vision)
        thumb = await run_in_threadpool(thumbnail_data_url, data)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as e:
        raise HTTPException(400, "无法识别的图片（支持 JPG / PNG / WebP / GIF / BMP 等）") from e
    out = {**sug.to_dict(), "filename": file.filename, "thumbnail": thumb, "preview": None}
    if preview:
        out["preview"] = await run_in_threadpool(_render, sug.feel, sug.knobs, sug.reasons, False, fallback,
                                                 sug.image_reading)
    return out


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
