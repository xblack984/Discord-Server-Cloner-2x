from __future__ import annotations

import base64
import importlib.util
import json
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from xbox_agent import vision_tasks as vt
from xbox_agent.config import Settings
from xbox_agent.tools.claude_vision import ClaudeVision
from xbox_agent.tools.deepseek_vision import DeepSeekVision
from xbox_agent.tools.images import encode_image as _encode_image
from xbox_agent.tools.vision import build_vision

FORBIDDEN = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "minItems", "maxItems"}


def walk(schema):
    yield schema
    for sub in schema.get("properties", {}).values():
        yield from walk(sub)
    if isinstance(schema.get("items"), dict):
        yield from walk(schema["items"])
    for sub in schema.get("anyOf", []):
        yield from walk(sub)


class SchemaSanity(unittest.TestCase):
    def test_every_object_is_closed_and_fully_required(self):
        for name, schema in vt.SCHEMAS.items():
            for node in walk(schema):
                if node.get("type") == "object":
                    self.assertIs(node["additionalProperties"], False, name)
                    self.assertEqual(node["required"], list(node["properties"]), name)
                self.assertFalse(FORBIDDEN & set(node), name)

    def test_vocabularies_match_the_validators(self):
        self.assertEqual(vt.SCHEMAS["screen"]["properties"]["screen"]["enum"], list(vt.SCREENS))
        self.assertIn("password_prompt", vt.SCREENS)

    def test_the_classification_prompt_says_the_guide_counts_even_with_a_game_behind_it(self):
        seen = []

        class Recorder:
            def ask(self, png, task, required_keys, schema=None):
                seen.append(task)
                return {"screen": "guide_menu", "confidence": 0.9}

        self.assertEqual(vt.classify_screen(Recorder(), b"x"), "guide_menu")
        self.assertIn("side panel", seen[0])
        self.assertIn("game is still visible", seen[0])

    def test_tasks_send_their_schema_and_required_keys(self):
        seen = []

        class Recorder:
            def ask(self, png, task, required_keys, schema=None):
                seen.append((list(required_keys), schema))
                return {"screen": "home", "confidence": 0.9}

        vt.classify_screen(Recorder(), b"x")
        self.assertEqual(seen, [(["screen", "confidence"], vt.SCHEMAS["screen"])])


def response(text: str, stop: str = "end_turn", blocks=None):
    content = blocks if blocks is not None else [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop, content=content, stop_details=SimpleNamespace(category="cyber"))


class FakeMessages:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create(self, **kw):
        self.calls.append(kw)
        return self.reply


def fake_client(reply):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(reply)), messages=FakeMessages(reply))


GOOD = json.dumps({"screen": "home", "confidence": 0.9})


class ClaudeVisionTests(unittest.TestCase):
    def ask(self, client, **kw):
        return ClaudeVision(client=client, **kw).ask(b"\x89PNGdata", "task", ["screen"], vt.SCHEMAS["screen"])

    def test_request_shape_with_server_side_fallbacks(self):
        client = fake_client(response(GOOD))
        self.assertEqual(self.ask(client)["screen"], "home")
        call = client.beta.messages.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(call["extra_body"], {"fallbacks": "default"})
        self.assertEqual(call["output_config"]["effort"], "low")
        self.assertEqual(call["output_config"]["format"], {"type": "json_schema", "schema": vt.SCHEMAS["screen"]})
        self.assertNotIn("temperature", call)
        image, text = call["messages"][0]["content"]
        self.assertEqual(image["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(image["source"]["data"]), b"\x89PNGdata")
        self.assertEqual(text, {"type": "text", "text": "task"})
        self.assertEqual(client.messages.calls, [])

    def test_fallbacks_can_be_turned_off(self):
        client = fake_client(response(GOOD))
        self.ask(client, fallbacks=False)
        self.assertEqual(len(client.messages.calls), 1)
        self.assertNotIn("betas", client.messages.calls[0])
        self.assertNotIn("extra_body", client.messages.calls[0])
        self.assertEqual(client.beta.messages.calls, [])

    def test_model_and_effort_are_configurable(self):
        client = fake_client(response(GOOD))
        self.ask(client, model="claude-haiku-5-5", effort="medium")
        call = client.beta.messages.calls[0]
        self.assertEqual((call["model"], call["output_config"]["effort"]), ("claude-haiku-5-5", "medium"))

    def test_refusal_is_checked_before_content_and_raises(self):
        with self.assertRaisesRegex(ValueError, "declined.*cyber"):
            self.ask(fake_client(response("", stop="refusal", blocks=[])))

    def test_truncated_answer_raises(self):
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            self.ask(fake_client(response('{"screen": "ho', stop="max_tokens")))

    def test_non_json_and_missing_keys_raise(self):
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            self.ask(fake_client(response("The screen shows the home page")))
        with self.assertRaisesRegex(ValueError, "missing keys"):
            self.ask(fake_client(response('{"confidence": 0.5}')))

    def test_small_png_is_sent_as_is(self):
        self.assertEqual(_encode_image(b"small")[0], "image/png")

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow not installed")
    def test_oversized_png_is_reencoded_as_jpeg(self):
        import io
        import os

        from PIL import Image

        buf = io.BytesIO()
        Image.frombytes("RGB", (1400, 1000), os.urandom(1400 * 1000 * 3)).save(buf, format="PNG")
        self.assertGreater(len(buf.getvalue()), 3_500_000)
        media_type, data = _encode_image(buf.getvalue())
        self.assertEqual(media_type, "image/jpeg")
        self.assertLess(len(base64.b64decode(data)), len(buf.getvalue()))


class FakeHttp:
    """Stands in for httpx.Client: replays queued (status, payload) answers and records every request."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        status, payload = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return SimpleNamespace(status_code=status, text=str(payload), json=lambda: payload)


def completion(content, finish="stop"):
    return 200, {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]}


class DeepSeekVisionTests(unittest.TestCase):
    def ask(self, http, **kw):
        return DeepSeekVision("sk-test", client=http, **kw).ask(b"\x89PNGdata", "task", ["screen"], vt.SCHEMAS["screen"])

    def test_request_shape(self):
        http = FakeHttp(completion(GOOD))
        self.assertEqual(self.ask(http)["screen"], "home")
        call = http.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["headers"], {"Authorization": "Bearer sk-test"})
        body = call["json"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["response_format"], {"type": "json_object"})  # json_schema is refused with images
        self.assertEqual(body["thinking"], {"type": "disabled"})
        image, text = body["messages"][0]["content"]
        self.assertEqual(image["type"], "image_url")
        self.assertEqual(image["image_url"]["url"], "data:image/png;base64," + base64.b64encode(b"\x89PNGdata").decode())
        self.assertIn("JSON Schema", text["text"])
        self.assertIn(json.dumps(vt.SCHEMAS["screen"], separators=(",", ":")), text["text"])  # schema travels in the prompt
        self.assertNotIn("temperature", body)

    def test_options_are_configurable(self):
        http = FakeHttp(completion(GOOD))
        self.ask(http, model="deepseek-v4-flash-vision-exp", base_url="https://gw.example/v1/", thinking="default")
        call = http.calls[0]
        self.assertEqual(call["url"], "https://gw.example/v1/chat/completions")
        self.assertEqual(call["json"]["model"], "deepseek-v4-flash-vision-exp")
        self.assertNotIn("thinking", call["json"])

    def test_a_deployment_that_rejects_the_thinking_field_is_retried_once_and_remembered(self):
        http = FakeHttp((400, {"error": "unknown field thinking"}), completion(GOOD))
        vision = DeepSeekVision("k", client=http)
        vision.ask(b"x", "t", ["screen"])
        self.assertEqual(["thinking" in c["json"] for c in http.calls], [True, False])
        vision.ask(b"x", "t", ["screen"])
        self.assertNotIn("thinking", http.calls[-1]["json"])  # not re-sent on later calls

    def test_http_errors_carry_status_and_body(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            self.ask(FakeHttp((401, {"error": {"message": "Authentication Fails"}})))
        with self.assertRaisesRegex(RuntimeError, "HTTP 402"):  # insufficient balance
            self.ask(FakeHttp((402, "Insufficient Balance")))

    def test_bad_answers_raise_instead_of_guessing(self):
        cases = {
            "cut off": completion('{"screen": "ho', finish="length"),
            "declined": completion("", finish="content_filter"),
            "empty answer": completion(""),
            "non-JSON": completion("It looks like the home screen"),
            "missing keys": completion('{"confidence": 0.5}'),
        }
        for pattern, answer in cases.items():
            with self.subTest(pattern), self.assertRaisesRegex(ValueError, pattern):
                self.ask(FakeHttp(answer))

    def test_code_fenced_json_is_accepted(self):
        self.assertEqual(self.ask(FakeHttp(completion("```json\n" + GOOD + "\n```")))["screen"], "home")

    def test_missing_key_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            DeepSeekVision("")

    def test_no_http_library_is_needed_just_to_construct_it(self):
        DeepSeekVision("k")  # httpx is only imported on the first request


HAS_WEB = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "uvicorn"))


@unittest.skipUnless(HAS_WEB, "fastapi/httpx/uvicorn not installed")
class DeepSeekOverRealHttp(unittest.TestCase):
    """The real httpx client against a local server that imitates the chat-completions endpoint."""

    def test_roundtrip(self):
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        seen = []

        async def chat(request):
            seen.append((request.headers.get("authorization"), await request.json()))
            return JSONResponse({"choices": [{"finish_reason": "stop", "message": {"content": GOOD}}]})

        app = Starlette(routes=[Route("/chat/completions", chat, methods=["POST"])])

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self.addCleanup(lambda: (setattr(server, "should_exit", True), thread.join(5)))
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        vision = DeepSeekVision("sk-real", base_url=f"http://127.0.0.1:{port}")
        self.addCleanup(lambda: vision._client and vision._client.close())
        out = vt.classify_screen(vision, b"\x89PNG-bytes")
        self.assertEqual(out, "home")
        auth, body = seen[0]
        self.assertEqual(auth, "Bearer sk-real")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertTrue(body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,"))


class ProviderSelection(unittest.TestCase):
    def fake_anthropic(self):
        mod = types.ModuleType("anthropic")
        mod.Anthropic = lambda: fake_client(response(GOOD))
        return mock.patch.dict(sys.modules, {"anthropic": mod})

    def fake_google(self):
        google, genai = types.ModuleType("google"), types.ModuleType("google.genai")
        genai.Client = lambda api_key: object()
        google.genai = genai
        return mock.patch.dict(sys.modules, {"google": google, "google.genai": genai})

    def test_auto_without_gemini_key_uses_claude_with_settings(self):
        cfg = Settings(claude_model="claude-sonnet-5-5", claude_effort="medium", claude_fallbacks=False)
        with self.fake_anthropic():
            v = build_vision(cfg)
        self.assertIsInstance(v, ClaudeVision)
        self.assertEqual((v._model, v._effort, v._fallbacks), ("claude-sonnet-5-5", "medium", False))

    def test_auto_with_gemini_key_keeps_gemini(self):
        from xbox_agent.tools.gemini_vision import GeminiVision

        with self.fake_google():
            self.assertIsInstance(build_vision(Settings(gemini_api_key="k")), GeminiVision)

    def test_explicit_provider_wins_over_a_gemini_key(self):
        with self.fake_anthropic():
            self.assertIsInstance(build_vision(Settings(vision_provider="claude", gemini_api_key="k")), ClaudeVision)

    def test_auto_prefers_gemini_then_deepseek_then_claude(self):
        self.assertIsInstance(build_vision(Settings(deepseek_api_key="d")), DeepSeekVision)
        with self.fake_google():
            from xbox_agent.tools.gemini_vision import GeminiVision

            self.assertIsInstance(build_vision(Settings(gemini_api_key="g", deepseek_api_key="d")), GeminiVision)
        with self.fake_anthropic():
            self.assertIsInstance(build_vision(Settings()), ClaudeVision)

    def test_explicit_deepseek_uses_its_settings(self):
        v = build_vision(Settings(vision_provider="deepseek", deepseek_api_key="d", deepseek_model="m",
                                  deepseek_base_url="https://x/", deepseek_thinking="default"))
        self.assertEqual((v._model, v._url, v._send_thinking_off), ("m", "https://x/chat/completions", False))
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            build_vision(Settings(vision_provider="deepseek"))

    def test_openai_and_typos_are_rejected(self):
        for bad in ("openai", "gemni"):
            with self.assertRaisesRegex(ValueError, "unknown vision provider"):
                build_vision(Settings(vision_provider=bad))

    def test_gemini_without_key_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            build_vision(Settings(vision_provider="gemini"))


if __name__ == "__main__":
    unittest.main()
