"""视觉解读：JSON 解析 / 融合 / 缓存 / 兜底（不联网，HTTP 被 mock）。

  uv run python -m unittest -v tests.test_vision      （或 make test）
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from src.feel import image_spec as IS
from src.feel import vision as V
from src.feel.spec import build_spec, script_seconds

GOOD = {
    "angles": {
        "mood": {"text": "黄昏海边的宁静与淡淡的怀旧", "keywords": ["宁静", "怀旧"]},
        "content": {"text": "一个人坐在码头尽头看夕阳，海面泛着金光", "keywords": ["码头", "夕阳"]},
        "feeling": {"text": "放松、温暖，又有一点孤独", "keywords": ["温暖", "孤独"]},
        "story": {"text": "一天结束后的独处时刻，像旅行 vlog 的结尾", "keywords": ["vlog", "结尾"]},
        "rhythm": {"text": "画面几乎静止，海浪缓慢起伏，适合舒缓的律动", "keywords": ["舒缓", "缓慢"]},
        "light": {"text": "暖橙逆光，对比柔和，温暖而略带感伤", "keywords": ["暖橙", "逆光"]},
    },
    "keywords": ["黄昏", "宁静", "温暖", "怀旧"],
    "energy": 22, "brightness": 72, "density": 30,
    "suggested_key": "F", "suggested_bpm": 80, "suggested_duck": 0,
    "narration_hint": "低声、娓娓道来的旅行独白",
}


def png() -> bytes:
    im = Image.new("RGB", (64, 48))
    d = ImageDraw.Draw(im)
    for y in range(48):
        t = y / 47
        d.line([(0, y), (63, y)], fill=(int(250 - 220 * t), int(150 - 120 * t), int(60 + 60 * t)))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def openai_reply(obj_or_text):
    content = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(V, "CACHE_DIR", Path(self.tmp.name)),
                        mock.patch.dict(os.environ, {}, clear=False),
                        mock.patch.object(V, "MODEL_LISTER", lambda: None)]   # no network in tests
        for p in self.patches:
            p.start()
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CTM_VISION_MODEL", "CTM_VISION_PROVIDER", "OPENAI_BASE_URL",
                  "CTM_VISION_TIMEOUT", "CTM_VISION_MAX_TOKENS", "CTM_VISION_REASONING"):
            os.environ.pop(k, None)
        V.clear_memory_cache()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
        V.clear_memory_cache()


class ParseTests(Base):
    def test_full_schema(self):
        r = V.parse_result(json.dumps(GOOD, ensure_ascii=False))
        self.assertEqual([a["key"] for a in r.angles], ["mood", "content", "feeling", "story", "rhythm", "light"])
        self.assertEqual(r.angles[4]["title"], "节奏暗示")
        self.assertEqual(r.mood_zh, GOOD["angles"]["mood"]["text"])
        self.assertEqual((r.suggested_key, r.suggested_bpm, r.suggested_duck), ("F", 80, 0))
        d = r.to_dict()
        self.assertIn("feeling_zh", d)
        self.assertNotIn("raw", d)

    def test_fenced_and_chatty(self):
        txt = "好的，以下是解读：\n```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```\n希望有帮助 {not json}"
        self.assertEqual(V.parse_result(txt).energy, 22)

    def test_flat_fields_and_clamping(self):
        r = V.parse_result({"mood_zh": "紧张", "content_zh": "城市夜景", "feeling_zh": "压迫感",
                            "keywords": "夜晚、霓虹, 紧张", "energy": "150", "brightness": -5, "density": "abc",
                            "suggested_key": "D minor", "suggested_bpm": 300, "suggested_duck": 7})
        self.assertEqual(r.keywords, ["夜晚", "霓虹", "紧张"])
        self.assertEqual((r.energy, r.brightness, r.density), (100, 0, 50))
        self.assertEqual((r.suggested_key, r.suggested_bpm, r.suggested_duck), ("Dm", 132, 2))

    def test_missing_core_angles(self):
        with self.assertRaises(V.VisionError):
            V.parse_result({"energy": 50})
        with self.assertRaises(V.VisionError):
            V.parse_result("抱歉，我无法查看图片。")
        with self.assertRaises(V.VisionError):
            V.parse_result("")


class FallbackTests(Base):
    def test_no_key_falls_back_with_chinese_message(self):
        s = IS.suggest_from_image(png())
        self.assertEqual(s.source, "color")
        self.assertIn("OPENAI_API_KEY", s.vision_error)
        self.assertIn("颜色规则", s.vision_error)
        self.assertIsNone(s.vision)
        self.assertEqual(s.knobs, IS.suggest_from_image(png(), use_vision=False).knobs)

    def test_no_vision_flag_never_calls_http(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        with mock.patch.object(V, "HTTP_POST", side_effect=AssertionError("should not call")):
            s = IS.suggest_from_image(png(), use_vision=False)
        self.assertEqual(s.source, "color")
        self.assertIsNone(s.vision_error)

    def test_timeout_falls_back(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        with mock.patch.object(V, "HTTP_POST", side_effect=TimeoutError("timed out")):
            s = IS.suggest_from_image(png(), timeout=1)
        self.assertEqual(s.source, "color")
        self.assertIn("超时", s.vision_error)

    def test_bad_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "bad"
        fake = mock.Mock(side_effect=V.HTTPFail(401, "invalid x-api-key"))
        with mock.patch.object(V, "HTTP_POST", fake):
            s = IS.suggest_from_image(png())
        self.assertIn("无效", s.vision_error)
        self.assertEqual(fake.call_count, 1)                 # 401 = bad key → stop, don't burn the chain

    def test_403_no_access_on_every_model(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        fake = mock.Mock(side_effect=V.HTTPFail(403, '{"error":{"message":"no access to model"}}'))
        with mock.patch.object(V, "HTTP_POST", fake):
            s = IS.suggest_from_image(png())
        self.assertEqual(s.source, "color")
        self.assertIn("无权限", s.vision_error)
        self.assertEqual(fake.call_count, len(V.OPENAI_MODELS))   # every candidate was tried


class VisionPathTests(Base):
    def test_openai_success_fuse_and_cache(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        calls = []

        def fake(url, payload, headers, timeout):
            calls.append((url, payload["model"]))
            img = payload["messages"][0]["content"][1]["image_url"]["url"]
            assert img.startswith("data:image/jpeg;base64,")
            return openai_reply(GOOD)

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            s = IS.suggest_from_image(png())
        self.assertEqual([m for _, m in calls], ["Qwen3.8-Flash-Next"])
        self.assertTrue(calls[0][0].endswith("/chat/completions"))
        self.assertEqual(s.source, "vision")
        self.assertEqual(len(s.vision["angles"]), 6)
        self.assertEqual(s.knobs["voice"], 0)
        self.assertGreaterEqual(s.knobs["mood"], 67)            # F → bright side of build_spec
        self.assertLess(s.knobs["speed"], 30)                  # 80 BPM + calm rhythm angle
        self.assertTrue(all(abs(v) <= IS.COLOR_BIAS_MAX for v in s.image_reading["color_bias"].values()))
        self.assertLess(len(s.feel), 120)
        self.assertIsNone(script_seconds(s.feel))
        self.assertTrue(any("节奏暗示" in r for r in s.reasons))

        spec = build_spec(s.feel, s.knobs, preview=False, image_reading=s.image_reading)
        self.assertEqual(spec.key, "F major")
        self.assertEqual(spec.image_reading["angles"][0]["key"], "mood")
        self.assertTrue(any(n.startswith("读图·节奏暗示") for n in spec.notes))
        self.assertTrue(any(n.startswith("口播建议") for n in spec.notes))
        json.dumps(spec.to_dict(), ensure_ascii=False)

        # second call: served from cache (memory, then disk) — no HTTP at all
        with mock.patch.object(V, "HTTP_POST", side_effect=AssertionError("cache miss")):
            s2 = IS.suggest_from_image(png())
            self.assertTrue(s2.vision["cached"])
            self.assertEqual(s2.knobs, s.knobs)
            V.clear_memory_cache()
            s3 = IS.suggest_from_image(png())
            self.assertTrue(s3.vision["cached"])
        self.assertEqual(len(list(Path(self.tmp.name).glob("*.json"))), 1)

    def test_anthropic_and_response_format_retry(self):
        os.environ["ANTHROPIC_API_KEY"] = "ak-test"
        os.environ["CTM_VISION_MODEL"] = "claude-x"
        dark = json.loads(json.dumps(GOOD))
        dark.update(suggested_key="Dm", brightness=20, energy=85, density=90, suggested_bpm=124, suggested_duck=2)

        def fake(url, payload, headers, timeout):
            assert url.endswith("/v1/messages") and headers["x-api-key"] == "ak-test"
            assert payload["model"] == "claude-x"
            return {"content": [{"type": "text", "text": json.dumps(dark, ensure_ascii=False)}]}

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            s = IS.suggest_from_image(png())
        self.assertEqual(s.source, "vision")
        self.assertLessEqual(s.knobs["mood"], 64)
        self.assertEqual(s.knobs["voice"], 2)
        self.assertFalse(s.duck)
        self.assertEqual(build_spec(s.feel, s.knobs, image_reading=s.image_reading).key, "D minor")

        V.clear_memory_cache()
        for f in Path(self.tmp.name).glob("*"):
            f.unlink()
        os.environ.pop("ANTHROPIC_API_KEY")
        os.environ.pop("CTM_VISION_MODEL")
        os.environ["OPENAI_API_KEY"] = "sk"
        seen = []

        def fake2(url, payload, headers, timeout):
            seen.append(dict(payload))
            if "response_format" in payload:
                raise V.HTTPFail(400, "response_format is not supported")
            return openai_reply("```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```")

        with mock.patch.object(V, "HTTP_POST", side_effect=fake2):
            self.assertEqual(IS.suggest_from_image(png()).source, "vision")
        self.assertEqual(len(seen), 2)

    def test_default_model_qwen_then_deepseek_vision(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        ms = V._models("openai")
        self.assertEqual(ms[:2], ["Qwen3.8-Flash-Next", "DeepSeek-V4-Flash-Vision-Exp"])
        self.assertNotIn("deepseek-chat", ms)                # text-only model can't read images
        os.environ["CTM_VISION_MODEL"] = "my-model"
        ms = V._models("openai")
        self.assertEqual(ms[:3], ["my-model", "Qwen3.8-Flash-Next", "DeepSeek-V4-Flash-Vision-Exp"])

    def test_model_alias_resolved_against_server_list(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        with mock.patch.object(V, "MODEL_LISTER", lambda: ["deepseek-v4-flash-vision-exp", "qwen38-flash-next"]):
            ms = V._models("openai")
        self.assertEqual(ms[:2], ["qwen38-flash-next", "deepseek-v4-flash-vision-exp"])
        self.assertEqual(len(ms), len(set(ms)))

    def test_qwen_403_no_access_falls_back_to_deepseek(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        tried = []

        def fake(url, payload, headers, timeout):
            tried.append(payload["model"])
            if payload["model"] == "Qwen3.8-Flash-Next":
                raise V.HTTPFail(403, '{"error":{"message":"no access to model Qwen3.8-Flash-Next"}}')
            return openai_reply(json.dumps(GOOD, ensure_ascii=False))

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            r = V.interpret_image(png(), use_cache=False)
        self.assertEqual(tried, ["Qwen3.8-Flash-Next", "DeepSeek-V4-Flash-Vision-Exp"])
        self.assertEqual(r.model, "DeepSeek-V4-Flash-Vision-Exp")

    def test_qwen_404_falls_back(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        tried = []

        def fake(url, payload, headers, timeout):
            tried.append(payload["model"])
            if payload["model"] != "DeepSeek-V4-Flash-Vision-Exp":
                raise V.HTTPFail(404, "model not found")
            return openai_reply(json.dumps(GOOD, ensure_ascii=False))

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            r = V.interpret_image(png(), use_cache=False)
        self.assertEqual(r.model, "DeepSeek-V4-Flash-Vision-Exp")
        self.assertEqual(tried[0], "Qwen3.8-Flash-Next")

    def test_payload_defaults_tokens_reasoning_timeout(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        seen = []

        def fake(url, payload, headers, timeout):
            seen.append((dict(payload), timeout))
            return openai_reply(GOOD)

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            V.interpret_image(png(), use_cache=False)
        payload, timeout = seen[0]
        self.assertEqual(payload["max_tokens"], 4000)
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(timeout, 90.0)

        os.environ.update(CTM_VISION_REASONING="off", CTM_VISION_MAX_TOKENS="2500", CTM_VISION_TIMEOUT="30")
        seen.clear()
        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            V.interpret_image(png(), use_cache=False)
        self.assertNotIn("reasoning_effort", seen[0][0])
        self.assertEqual((seen[0][0]["max_tokens"], seen[0][1]), (2500, 30.0))

    def test_unsupported_reasoning_param_stripped_and_retried(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        seen = []

        def fake(url, payload, headers, timeout):
            seen.append(dict(payload))
            if "reasoning_effort" in payload:
                raise V.HTTPFail(400, "Unrecognized request argument supplied: reasoning_effort")
            if "response_format" in payload:
                raise V.HTTPFail(400, "response_format is not supported")
            return openai_reply(GOOD)

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            r = V.interpret_image(png(), use_cache=False)
        self.assertEqual(r.model, "Qwen3.8-Flash-Next")
        self.assertEqual(len(seen), 3)

    def test_empty_content_from_reasoning_model_falls_back(self):
        os.environ["OPENAI_API_KEY"] = "sk"

        def fake(url, payload, headers, timeout):
            if payload["model"] == "Qwen3.8-Flash-Next":   # thinking ate the budget → content empty / None
                return {"choices": [{"message": {"content": None, "reasoning_content": "..."},
                                     "finish_reason": "length"}]}
            return openai_reply(GOOD)

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            r = V.interpret_image(png(), use_cache=False)
        self.assertEqual(r.model, "DeepSeek-V4-Flash-Vision-Exp")

    def test_cache_key_includes_model(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        os.environ["CTM_VISION_MODEL"] = "Qwen3.8-Flash-Next"
        fake = mock.Mock(return_value=openai_reply(GOOD))
        with mock.patch.object(V, "HTTP_POST", fake):
            r1 = V.interpret_image(png())
            self.assertFalse(r1.cached)
            self.assertTrue(V.interpret_image(png()).cached)            # same model → cache hit
            os.environ["CTM_VISION_MODEL"] = "DeepSeek-V4-Flash-Vision-Exp"
            r2 = V.interpret_image(png())                               # other model → fresh call
            self.assertFalse(r2.cached)
            self.assertEqual(r2.model, "DeepSeek-V4-Flash-Vision-Exp")
            V.clear_memory_cache()
            self.assertTrue(V.interpret_image(png()).cached)            # disk cache, keyed by model too
        self.assertEqual(fake.call_count, 2)
        names = sorted(p.name for p in Path(self.tmp.name).glob("*.json"))
        self.assertEqual(len(names), 2)
        self.assertTrue(any("qwen38flashnext" in n for n in names))
        self.assertTrue(any("deepseekv4flashvisionexp" in n for n in names))
        self.assertNotEqual(V.cache_key("h", "openai", "a"), V.cache_key("h", "openai", "b"))

    def test_fallback_result_not_served_for_preferred_model(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        tried = []

        def fake(url, payload, headers, timeout):
            tried.append(payload["model"])
            if payload["model"] == "Qwen3.8-Flash-Next" and len(tried) == 1:
                raise V.HTTPFail(503, "overloaded")
            return openai_reply(GOOD)

        with mock.patch.object(V, "HTTP_POST", side_effect=fake):
            self.assertEqual(V.interpret_image(png()).model, "DeepSeek-V4-Flash-Vision-Exp")
            r = V.interpret_image(png())                    # preferred Qwen retried, not DeepSeek's cached read
        self.assertEqual((r.model, r.cached), ("Qwen3.8-Flash-Next", False))

    def test_dotenv_loader_does_not_override(self):
        from src import envfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("# c\nexport CTM_T1='a b'\nCTM_T2=x # note\nCTM_T3=keep\n", "utf-8")
            os.environ["CTM_T3"] = "shell"
            try:
                self.assertEqual(envfile.load_env(f), f)
                self.assertEqual(os.environ["CTM_T1"], "a b")
                self.assertEqual(os.environ["CTM_T2"], "x")
                self.assertEqual(os.environ["CTM_T3"], "shell")
            finally:
                for k in ("CTM_T1", "CTM_T2", "CTM_T3"):
                    os.environ.pop(k, None)

    def test_garbage_from_all_models(self):
        os.environ["OPENAI_API_KEY"] = "sk"
        with mock.patch.object(V, "HTTP_POST", return_value=openai_reply("I can't see images")):
            s = IS.suggest_from_image(png())
        self.assertEqual(s.source, "color")
        self.assertIn("都没有成功", s.vision_error)


if __name__ == "__main__":
    unittest.main()
