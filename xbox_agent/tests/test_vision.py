from __future__ import annotations

import base64
import importlib.util
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from xbox_agent import vision_tasks as vt
from xbox_agent.config import Settings
from xbox_agent.tools.claude_vision import ClaudeVision, _encode_image
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

    def test_openai_and_typos_are_rejected(self):
        for bad in ("openai", "gemni"):
            with self.assertRaisesRegex(ValueError, "unknown vision provider"):
                build_vision(Settings(vision_provider=bad))

    def test_gemini_without_key_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            build_vision(Settings(vision_provider="gemini"))


if __name__ == "__main__":
    unittest.main()
