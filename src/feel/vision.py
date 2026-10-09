"""视觉解读：把整张图交给支持看图的大模型，从多个角度「读图」，再给出音乐旋钮建议（结构化 JSON）。

六个角度（每个都是一句中文 + 关键词）：
  情绪氛围 mood · 画面内容 content · 给人的感觉 feeling · 可能的故事/场景 story ·
  节奏暗示 rhythm · 色调与光影情绪 light
外加数值建议：energy / brightness / density（0-100）、suggested_key（Dm/F）、suggested_bpm、
suggested_duck（0/1/2 = 不抢/平衡/偏配乐）、narration_hint（口播语气建议）。

图片 → 音乐的主路径（image_spec.suggest_from_image 默认先走这里），Pillow 颜色特征只做 ±10 的轻微修正；
没配 key / 超时 / 解析失败时自动退回纯颜色规则（--no-vision 或网页取消勾选「使用视觉解读」= 直接走老路）。

不引入新依赖：只用标准库 urllib 调 HTTP 接口。

环境变量（任选其一即可；都设置时按 CTM_VISION_PROVIDER，否则 OpenAI 兼容优先）：
  OPENAI_API_KEY        OpenAI 或任何 OpenAI 兼容接口（OpenRouter / 通义千问 / 智谱 / 本地 vLLM …）
  OPENAI_BASE_URL       可选，默认 https://api.openai.com/v1（兼容接口填它们的 /v1 地址）
  ANTHROPIC_API_KEY     Anthropic Claude
  ANTHROPIC_BASE_URL    可选，默认 https://api.anthropic.com
  CTM_VISION_MODEL      可选，指定模型（逗号分隔可给多个，按顺序尝试）；不填默认 Qwen3.8-Flash-Next，
                        之后依次退到 qwen38-flash-next → deepseek-chat → gpt-4o-mini …（模型 404 时换下一个）

也可以把这些变量写进仓库根目录的 .env（cp .env.example .env；.env 已被 gitignore，切勿提交），
启动时自动读取（装了 python-dotenv 就用它，否则内置简易解析；已在 shell 里 export 的变量优先）。
  CTM_VISION_PROVIDER   可选，openai / anthropic，强制用哪家
  CTM_VISION_TIMEOUT    可选，单次请求超时秒数，默认 45

  python -m src.feel.vision 封面.jpg      # 只打印视觉解读 JSON（调试用）
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.envfile import load_env

load_env()

PROMPT_VERSION = "v2"           # bump when the prompt / schema changes → invalidates the cache
SEND_SIZE = 1024                # longest side of the JPEG sent to the model
DEFAULT_TIMEOUT = 45.0
DEFAULT_VISION_MODEL = "Qwen3.8-Flash-Next"   # used when CTM_VISION_MODEL is unset (OpenAI-compatible endpoint)
# tried in order; unknown ids 404 → next one. Alias spellings are resolved against GET /models when possible.
OPENAI_MODELS = [DEFAULT_VISION_MODEL, "qwen38-flash-next", "deepseek-chat",
                 "gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"]
ANTHROPIC_MODELS = ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"]
CACHE_DIR = Path(os.environ.get("CTM_VISION_CACHE", Path(__file__).resolve().parents[2] / "build" / "vision_cache"))

# (key, 中文标题) — order = display order in the UI and in spec notes
ANGLES = [
    ("mood", "情绪氛围"),
    ("content", "画面内容"),
    ("feeling", "给人的感觉"),
    ("story", "可能的故事 / 场景"),
    ("rhythm", "节奏暗示"),
    ("light", "色调与光影情绪"),
]
ANGLE_TITLES = dict(ANGLES)

PROMPT = """你是给视频配乐的音乐总监。请认真「读」这张图片：看清画面里有什么（人物、物体、文字、场景）、
在发生什么、构图与视线引导、光线与色彩、动静与节奏感，以及它可能想讲的故事；从多个角度理解它整体传达的
情绪和感受，然后为它设计一段「铺底配乐」。

只输出一个 JSON 对象，不要任何解释或 Markdown。每个角度都写一句具体的中文（20~50 字，要贴合这张图，
不要空泛套话）和 2~4 个中文关键词：
{
  "angles": {
    "mood":    {"text": "情绪氛围：画面的情绪基调", "keywords": ["…"]},
    "content": {"text": "画面内容：画面里有什么、在发生什么", "keywords": ["…"]},
    "feeling": {"text": "给人的感觉：观众看到后的内心感受", "keywords": ["…"]},
    "story":   {"text": "可能的故事/场景：这一刻之前/之后可能发生什么，适合出现在什么样的视频里", "keywords": ["…"]},
    "rhythm":  {"text": "节奏暗示：画面是静止还是流动、舒缓还是紧凑，对应音乐的速度与律动", "keywords": ["…"]},
    "light":   {"text": "色调与光影情绪：冷暖、明暗、对比、光线给人的情绪", "keywords": ["…"]}
  },
  "keywords": ["综合 3-6 个最能代表这张图的中文关键词"],
  "energy": 0-100 的整数（画面的能量/动感/紧张度，越高音乐越快越激烈）,
  "brightness": 0-100 的整数（情绪的明亮程度，越高越明朗乐观；越低越暗、沉重、悬疑）,
  "density": 0-100 的整数（画面信息量/热闹程度，越高配器越丰富、声部越多）,
  "suggested_key": "Dm" 或 "F"（Dm = D 小调，偏暗/沉思/悬疑；F = F 大调，偏明亮/温暖/积极，二选一）,
  "suggested_bpm": 72-132 的整数,
  "suggested_duck": 0、1 或 2（0 = 音乐退后、为口播让位；1 = 平衡；2 = 偏配乐、音乐可以突出，例如片头/无口播）,
  "narration_hint": "一句中文：如果给这张图配口播，适合什么样的解说语气/内容"
}"""


class VisionError(RuntimeError):
    """User-facing Chinese message: why the vision step didn't run / failed."""


@dataclass
class VisionResult:
    angles: list[dict]            # [{"key","title","text","keywords"}] in ANGLES order (only non-empty ones)
    keywords: list[str]
    energy: int
    brightness: int
    density: int
    suggested_key: str            # "Dm" / "F"
    suggested_bpm: int
    suggested_duck: int           # 0/1/2 (= spec voice knob)
    narration_hint: str
    provider: str = ""
    model: str = ""
    cached: bool = False
    seconds: float = 0.0
    image_sha256: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    def angle(self, key: str) -> str:
        return next((a["text"] for a in self.angles if a["key"] == key), "")

    # the three headline readings the brief asked for (kept as flat fields for API consumers)
    @property
    def mood_zh(self) -> str:
        return self.angle("mood")

    @property
    def content_zh(self) -> str:
        return self.angle("content")

    @property
    def feeling_zh(self) -> str:
        return self.angle("feeling")

    def all_keywords(self) -> list[str]:
        seen, out = set(), []
        for k in self.keywords + [w for a in self.angles for w in a["keywords"]]:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d.update(mood_zh=self.mood_zh, content_zh=self.content_zh, feeling_zh=self.feeling_zh)
        return d


# ---------------------------------------------------------------- JSON parsing (model output → VisionResult)

def _clip_int(v, lo: int, hi: int, default: int) -> int:
    try:
        if isinstance(v, str):
            m = re.search(r"-?\d+(\.\d+)?", v)
            v = float(m.group()) if m else default
        if v is None:
            v = default
        return int(round(max(lo, min(hi, float(v)))))
    except (TypeError, ValueError):
        return default


def _text(v, limit: int = 90) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    return s[:limit]


def _kw(v, n: int = 6) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[，,、;；/\s]+", v)
    if not isinstance(v, (list, tuple)):
        return []
    return [_text(k, 12) for k in v if _text(k, 12)][:n]


def _norm_key(v) -> str:
    s = str(v or "").strip().lower().replace(" ", "")
    if s.startswith("f") or "major" in s or "大调" in s:
        return "F"
    return "Dm"


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates ```json fences / chatter around it)."""
    if not text or not text.strip():
        raise VisionError("视觉模型返回了空内容")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:                       # scan balanced braces (string-aware)
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = t.find("{", start + 1)
    raise VisionError("视觉模型没有返回可解析的 JSON")


# flat aliases accepted for robustness (older prompt / models that flatten the structure)
_FLAT = {"mood": ("mood_zh", "情绪氛围"), "content": ("content_zh", "画面内容"), "feeling": ("feeling_zh", "给人的感觉"),
         "story": ("story_zh", "可能的故事"), "rhythm": ("rhythm_zh", "节奏暗示"), "light": ("light_zh", "色调与光影情绪")}


def parse_result(obj_or_text) -> VisionResult:
    obj = extract_json(obj_or_text) if isinstance(obj_or_text, str) else dict(obj_or_text)
    raw_angles = obj.get("angles") or {}
    if isinstance(raw_angles, list):          # [{"key": "mood", "text": ...}, ...]
        raw_angles = {a.get("key"): a for a in raw_angles if isinstance(a, dict)}
    angles = []
    for key, title in ANGLES:
        a = raw_angles.get(key) if isinstance(raw_angles, dict) else None
        if a is None:
            a = next((obj[f] for f in _FLAT[key] if obj.get(f)), None)
        if isinstance(a, str):
            a = {"text": a}
        if not isinstance(a, dict):
            continue
        txt = _text(a.get("text") or a.get("zh") or a.get("desc"), 120)
        if txt:
            angles.append({"key": key, "title": title, "text": txt, "keywords": _kw(a.get("keywords"), 4)})
    if not any(a["key"] in ("mood", "content", "feeling") for a in angles):
        raise VisionError("视觉模型的 JSON 缺少「情绪氛围 / 画面内容 / 给人的感觉」")
    energy = _clip_int(obj.get("energy"), 0, 100, 50)
    return VisionResult(
        angles=angles, keywords=_kw(obj.get("keywords")),
        energy=energy, brightness=_clip_int(obj.get("brightness"), 0, 100, 50),
        density=_clip_int(obj.get("density"), 0, 100, 50), suggested_key=_norm_key(obj.get("suggested_key")),
        suggested_bpm=_clip_int(obj.get("suggested_bpm"), 72, 132, int(round(72 + 0.6 * energy))),
        suggested_duck=_clip_int(obj.get("suggested_duck"), 0, 2, 1),
        narration_hint=_text(obj.get("narration_hint"), 120), raw=obj,
    )


# ---------------------------------------------------------------- providers

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def available_provider() -> str | None:
    forced = _env("CTM_VISION_PROVIDER").lower()
    has = {"openai": bool(_env("OPENAI_API_KEY")), "anthropic": bool(_env("ANTHROPIC_API_KEY"))}
    if forced in has:
        return forced if has[forced] else None
    return "openai" if has["openai"] else ("anthropic" if has["anthropic"] else None)


NO_KEY_MSG = ("未配置视觉模型 API Key：请设置环境变量 OPENAI_API_KEY（OpenAI 或兼容接口，可配 OPENAI_BASE_URL）"
              "或 ANTHROPIC_API_KEY 后重启服务。本次已改用颜色规则（色相 / 明暗 / 细节）生成。")


def _norm_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_SERVER_MODELS: dict[str, list[str] | None] = {}


def _server_models(timeout: float = 10.0) -> list[str] | None:
    """GET {OPENAI_BASE_URL}/models (cached per base URL); None when unavailable."""
    base = (_env("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if base in _SERVER_MODELS:
        return _SERVER_MODELS[base]
    ids = None
    try:
        req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + _env("OPENAI_API_KEY")})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ids = [m["id"] for m in json.load(r).get("data", []) if isinstance(m, dict) and m.get("id")]
    except Exception:  # noqa: BLE001 — listing is best effort
        ids = None
    _SERVER_MODELS[base] = ids
    return ids


def _resolve_ids(models: list[str], server: list[str] | None) -> list[str]:
    """Map candidates onto exact server ids (case / punctuation-insensitive, e.g. Qwen3.8-Flash-Next ↔
    qwen38-flash-next); ids the server lists go first, the rest keep their order. Deduplicated."""
    if not server:
        return list(dict.fromkeys(models))
    by_norm: dict[str, str] = {}
    for sid in server:
        by_norm.setdefault(_norm_id(sid), sid)
    hit = [by_norm[_norm_id(m)] for m in models if _norm_id(m) in by_norm]
    miss = [m for m in models if _norm_id(m) not in by_norm]
    return list(dict.fromkeys(hit + miss))


def _list_models() -> list[str] | None:
    return MODEL_LISTER()


MODEL_LISTER = _server_models   # tests monkeypatch this


def _models(provider: str) -> list[str]:
    m = [x.strip() for x in _env("CTM_VISION_MODEL").split(",") if x.strip()]
    if provider != "openai":
        return m or ANTHROPIC_MODELS
    m = m + [x for x in OPENAI_MODELS if x not in m]    # explicit choice first, default chain as fallback
    return _resolve_ids(m, _list_models())


def _jpeg_b64(data: bytes) -> str:
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(data))
    im.load()
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001
        pass
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    im.thumbnail((SEND_SIZE, SEND_SIZE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


class HTTPFail(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status, self.body = status, body


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise HTTPFail(e.code, e.read().decode("utf-8", "replace")) from e


HTTP_POST = _post_json          # tests monkeypatch this


def _call_openai(model: str, b64: str, timeout: float) -> str:
    base = (_env("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
    ]}]
    payload = {"model": model, "messages": msgs, "temperature": 0.4, "max_tokens": 1200,
               "response_format": {"type": "json_object"}}
    hdr = {"Authorization": "Bearer " + _env("OPENAI_API_KEY")}
    try:
        d = HTTP_POST(base + "/chat/completions", payload, hdr, timeout)
    except HTTPFail as e:          # some compatible servers / newer models reject these params → retry once
        if e.status != 400 or ("response_format" not in e.body and "max_tokens" not in e.body
                               and "temperature" not in e.body):
            raise
        if "response_format" in e.body:
            payload.pop("response_format", None)
        if "max_tokens" in e.body:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if "temperature" in e.body:
            payload.pop("temperature", None)
        d = HTTP_POST(base + "/chat/completions", payload, hdr, timeout)
    c = d["choices"][0]["message"]["content"]
    if isinstance(c, list):
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c


def _call_anthropic(model: str, b64: str, timeout: float) -> str:
    base = (_env("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    payload = {"model": model, "max_tokens": 1200, "temperature": 0.4, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        {"type": "text", "text": PROMPT},
    ]}]}
    hdr = {"x-api-key": _env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}
    d = HTTP_POST(base + "/v1/messages", payload, hdr, timeout)
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


def _model_missing(e: HTTPFail) -> bool:
    b = e.body.lower()
    return e.status == 404 or (e.status == 400 and "model" in b and any(w in b for w in ("not", "invalid", "exist", "support")))


# ---------------------------------------------------------------- cache (by image sha256 + prompt version)

_MEM: dict[str, VisionResult] = {}
_LOCK = threading.Lock()


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_path(h: str) -> Path:
    return CACHE_DIR / f"{h}.{PROMPT_VERSION}.json"


def _cache_get(h: str) -> VisionResult | None:
    with _LOCK:
        if h in _MEM:
            return _MEM[h]
    p = _cache_path(h)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text("utf-8"))
        r = parse_result(d["raw"])
        r.provider, r.model, r.image_sha256 = d.get("provider", ""), d.get("model", ""), h
    except (OSError, ValueError, KeyError, VisionError):
        return None
    with _LOCK:
        _MEM[h] = r
    return r


def _cache_put(h: str, r: VisionResult) -> None:
    with _LOCK:
        _MEM[h] = r
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(h).write_text(json.dumps({"provider": r.provider, "model": r.model, "raw": r.raw,
                                              "created": time.time()}, ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        pass


def clear_memory_cache() -> None:
    with _LOCK:
        _MEM.clear()


# ---------------------------------------------------------------- main entry

def interpret_image(src, timeout: float | None = None, use_cache: bool = True) -> VisionResult:
    """src: bytes / path. Raises VisionError (Chinese message) when no key / all models fail / timeout."""
    data = Path(src).read_bytes() if isinstance(src, (str, Path)) else bytes(src)
    h = image_hash(data)
    if use_cache:
        hit = _cache_get(h)
        if hit is not None:
            r = parse_result(hit.raw)
            r.provider, r.model, r.image_sha256, r.cached = hit.provider, hit.model, h, True
            return r
    provider = available_provider()
    if provider is None:
        raise VisionError(NO_KEY_MSG)
    timeout = float(timeout or _env("CTM_VISION_TIMEOUT") or DEFAULT_TIMEOUT)
    try:
        b64 = _jpeg_b64(data)
    except Exception as e:  # noqa: BLE001
        raise VisionError(f"图片无法转换后发送给视觉模型：{e}") from e
    call = _call_openai if provider == "openai" else _call_anthropic
    errors: list[str] = []
    t0 = time.time()
    for model in _models(provider):
        try:
            r = parse_result(call(model, b64, timeout))
        except HTTPFail as e:
            if e.status in (401, 403):
                raise VisionError(f"视觉模型 API Key 无效或无权限（{provider} HTTP {e.status}），请检查环境变量。"
                                  "本次已改用颜色规则。") from e
            errors.append(f"{model}: HTTP {e.status}")
            if _model_missing(e) or e.status in (429, 500, 502, 503, 529):
                continue
            break
        except (TimeoutError, OSError) as e:             # urllib timeouts / connection errors
            msg = "请求超时" if isinstance(e, TimeoutError) or "timed out" in str(e).lower() else f"网络错误 {e}"
            raise VisionError(f"视觉模型{msg}（{provider}/{model}，超时上限 {timeout:.0f} 秒）。本次已改用颜色规则。") from e
        except VisionError as e:
            errors.append(f"{model}: {e}")
            continue
        except (KeyError, IndexError, TypeError, ValueError) as e:
            errors.append(f"{model}: 返回格式异常 {e}")
            continue
        r.provider, r.model, r.image_sha256 = provider, model, h
        r.seconds = round(time.time() - t0, 2)
        if use_cache:
            _cache_put(h, r)
        return r
    raise VisionError("视觉模型都没有成功（" + "；".join(errors[:4]) + "）。本次已改用颜色规则。")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m src.feel.vision IMAGE", file=sys.stderr)
        return 2
    for p in argv:
        try:
            print(json.dumps({"image": p, **interpret_image(p).to_dict()}, ensure_ascii=False, indent=2))
        except VisionError as e:
            print(f"[vision] {p}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
