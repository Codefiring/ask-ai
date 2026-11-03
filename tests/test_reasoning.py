import json
import unittest
from unittest.mock import patch

import test_cli
from ask_ai.client import make_request
from ask_ai.config import UserError


class ReasoningTests(unittest.TestCase):
    def body(self, provider, effort="none", model="test-model", **kwargs):
        profile = {"provider": provider, "model": model, "base_url": "http://localhost:8000/v1"}
        request, _ = make_request(profile, "hello", reasoning_effort=effort, **kwargs)
        return json.loads(request.data)

    def test_defaults_disable_thinking_for_each_protocol(self):
        cases = {
            "openai": ("reasoning_effort", "none"),
            "custom": ("reasoning_effort", "none"),
            "ollama": ("reasoning_effort", "none"),
            "groq": ("reasoning_effort", "none"),
            "deepseek": ("thinking", {"type": "disabled"}),
            "anthropic": ("thinking", {"type": "disabled"}),
            "qwen": ("enable_thinking", False),
            "vllm": ("chat_template_kwargs", {"enable_thinking": False}),
            "openrouter": ("reasoning", {"enabled": False}),
        }
        for provider, (key, expected) in cases.items():
            with self.subTest(provider=provider):
                self.assertEqual(self.body(provider)[key], expected)
        self.assertEqual(self.body("gemini")["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})
        self.assertNotIn("reasoning_effort", self.body("vllm"))

    def test_enabling_and_effort_mapping(self):
        for effort in ("low", "medium", "high"):
            for provider in ("openai", "custom", "ollama", "groq", "vllm"):
                with self.subTest(effort=effort, provider=provider):
                    self.assertEqual(self.body(provider, effort)["reasoning_effort"], effort)
            self.assertEqual(self.body("deepseek", effort)["thinking"], {"type": "enabled"})
            self.assertEqual(self.body("openrouter", effort)["reasoning"], {"enabled": True, "effort": effort})
        self.assertEqual(self.body("qwen", "high")["thinking_budget"], 8192)
        self.assertEqual(self.body("vllm", "low")["chat_template_kwargs"], {"enable_thinking": True})

    def test_auto_omits_all_reasoning_fields(self):
        for provider in ("openai", "custom", "ollama", "groq", "vllm", "qwen", "deepseek", "anthropic", "gemini", "openrouter"):
            body = self.body(provider, "auto")
            for key in ("thinking", "reasoning", "reasoning_effort", "chat_template_kwargs", "enable_thinking", "thinking_budget", "generationConfig"):
                self.assertNotIn(key, body)

    def test_anthropic_budget_and_output_limit(self):
        body = self.body("anthropic", "medium", model="claude-sonnet-4-5")
        self.assertEqual(body["thinking"], {"type": "enabled", "budget_tokens": 4096})
        self.assertEqual(body["max_tokens"], 8192)
        with self.assertRaisesRegex(UserError, "max-tokens"):
            self.body("anthropic", "high", max_tokens=8192)
        self.assertEqual(self.body("anthropic", "low", max_tokens=2000)["max_tokens"], 2000)

    def test_anthropic_adaptive_models(self):
        for model in ("claude-sonnet-4-6", "claude-opus-4-7", "claude-sonnet-5"):
            body = self.body("anthropic", "high", model=model)
            self.assertEqual(body["thinking"], {"type": "adaptive"})
            self.assertEqual(body["output_config"], {"effort": "high"})

    def test_gemini_models(self):
        self.assertEqual(self.body("gemini", "low", model="gemini-2.5-flash")["generationConfig"]["thinkingConfig"], {"thinkingBudget": 1024})
        self.assertEqual(self.body("gemini", "high", model="gemini-3-flash-preview")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "high"})
        self.assertEqual(self.body("gemini", "medium", model="gemini-3-pro-preview")["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})
        for model in ("gemini-3-flash-preview", "models/gemini-2.5-pro"):
            with self.assertRaisesRegex(UserError, "无法.*关闭思考"):
                self.body("gemini", model=model)
        self.assertNotIn("generationConfig", self.body("gemini", model="gemini-2.0-flash"))

    def test_nonreasoning_models_are_not_sent_unsupported_parameters(self):
        for provider, model in (("openai", "gpt-4o"), ("openai", "gpt-4.1-mini"), ("groq", "llama-3.3-70b-versatile")):
            self.assertNotIn("reasoning_effort", self.body(provider, model=model))
            with self.assertRaisesRegex(UserError, "不支持思考"):
                self.body(provider, "high", model=model)


# Reuse the isolated CLI harness without inheriting its test cases.
class ReasoningCLITests(unittest.TestCase):
    setUp = test_cli.AskAITests.setUp
    cli = test_cli.AskAITests.cli
    add = test_cli.AskAITests.add

    def test_flags_and_default(self):
        self.add()
        for flags, expected in (([], "none"), (["--think"], "medium"), (["-r", "high"], "high"), (["--reasoning-effort", "auto"], "auto")):
            with patch("ask_ai.cli.ask", return_value=("ok", {})) as call:
                self.assertEqual(self.cli(*flags, "hello")[0], 0)
                self.assertEqual(call.call_args.kwargs["reasoning_effort"], expected)

    def test_conflicting_or_invalid_options_are_rejected(self):
        for flags in (("--think", "-r", "none"), ("-r", "invalid")):
            with self.assertRaises(SystemExit) as caught:
                self.cli(*flags, "hello")
            self.assertEqual(caught.exception.code, 2)
