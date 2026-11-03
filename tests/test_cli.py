import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from ask_ai.cli import main
from ask_ai.client import ask, extract_text, make_request
from ask_ai.config import UserError, api_key, config_path, load_config, save_config


class AskAITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"ASK_AI_CONFIG": str(Path(self.temp.name) / "config.json")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.profile = {"provider": "vllm", "base_url": "http://localhost:8000/v1", "model": "local-model"}

    def cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def add(self, name="local"):
        result = self.cli("config", "add", name, "--provider", "vllm", "--model", "local-model")
        self.assertEqual(result[0], 0, result)

    def test_profile_lifecycle_and_permissions(self):
        self.add()
        self.add("second")
        self.assertEqual(load_config()["default"], "local")
        self.assertEqual(stat.S_IMODE(config_path().stat().st_mode), 0o600)
        self.cli("config", "use", "second")
        self.assertEqual(load_config()["default"], "second")
        self.cli("config", "remove", "second")
        self.assertEqual(load_config()["default"], "local")
        self.cli("config", "remove", "local")
        self.assertIsNone(load_config()["default"])

    def test_secret_masked_and_provider_switch_clears_secret(self):
        self.cli("config", "add", "x", "--provider", "openai", "--model", "model", "--api-key", "secret")
        self.assertNotIn("secret", self.cli("config", "show", "x")[1])
        self.cli("config", "add", "x", "--provider", "vllm", "--model", "local")
        self.assertNotIn("api_key", load_config()["profiles"]["x"])

    def test_environment_key_precedence(self):
        profile = dict(self.profile, api_key="stored", api_key_env="MY_KEY")
        with self.assertRaises(UserError):
            api_key(profile)
        with patch.dict(os.environ, {"MY_KEY": "from-env"}):
            self.assertEqual(api_key(profile), "from-env")

    def test_missing_profile_is_actionable(self):
        code, out, err = self.cli("hello")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("ask-ai config", err)

    def test_bad_config_not_overwritten(self):
        config_path().write_text("not json")
        self.assertEqual(self.cli("config", "add", "x", "--model", "x")[0], 1)
        self.assertEqual(config_path().read_text(), "not json")

    def test_noninteractive_config_does_not_block(self):
        self.assertEqual(self.cli("config")[0], 1)

    def test_malformed_default_is_reported_without_traceback(self):
        config_path().write_text(json.dumps({"version": 1, "default": [], "profiles": {}}))
        code, out, err = self.cli("hello")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)

    def test_invalid_root_not_saved(self):
        for url in ("file:///tmp/test", "https://user:pass@host/v1", "https://host/v1?key=secret"):
            self.assertEqual(self.cli("config", "--model", "m", "--base-url", url)[0], 1)
        self.assertFalse(config_path().exists())

    def test_openai_and_vllm_payload(self):
        for provider, token_field in (("vllm", "max_tokens"), ("openai", "max_completion_tokens")):
            req, protocol = make_request(dict(self.profile, provider=provider, api_key="secret"), "你好", system="简洁", max_tokens=30)
            body = json.loads(req.data)
            self.assertEqual(req.full_url, "http://localhost:8000/v1/chat/completions")
            self.assertEqual(req.get_header("Authorization"), "Bearer secret")
            self.assertEqual(body[token_field], 30)
            self.assertNotIn("temperature", body)
            self.assertEqual(body["messages"][1]["content"], "你好")
            self.assertEqual(protocol, "openai")

    def test_anthropic_payload(self):
        req, protocol = make_request(dict(self.profile, provider="anthropic", api_key="secret"), "hello", system="brief")
        body = json.loads(req.data)
        self.assertEqual(req.get_header("X-api-key"), "secret")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(body["system"], "brief")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(protocol, "anthropic")

    def test_gemini_payload(self):
        req, protocol = make_request(dict(self.profile, provider="gemini", model="models/test", api_key="secret"), "hello", system="brief", max_tokens=42)
        body = json.loads(req.data)
        self.assertTrue(req.full_url.endswith("/models/test:generateContent"))
        self.assertNotIn("secret", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "secret")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 42)
        self.assertEqual(protocol, "gemini")

    def test_response_adapters(self):
        self.assertEqual(extract_text({"choices": [{"message": {"content": "hi"}}]}, "openai"), "hi")
        self.assertEqual(extract_text({"content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "hi"}]}, "anthropic"), "hi")
        self.assertEqual(extract_text({"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "hi"}]}}]}, "gemini"), "hi")
        for protocol in ("openai", "anthropic", "gemini"):
            with self.assertRaises(UserError):
                extract_text({}, protocol)

    def test_pipeline_and_overrides(self):
        self.add()
        with patch("ask_ai.cli.ask", return_value=("答案", {"ok": True})) as call:
            code, out, err = self.cli("总结", "-m", "override", "--system", "中文", stdin="文件内容")
            self.assertEqual((code, out, err), (0, "答案\n", ""))
            self.assertEqual(call.call_args.args[1], "总结\n\n文件内容")
            self.assertEqual(call.call_args.args[0]["model"], "override")
        self.assertEqual(load_config()["profiles"]["local"]["model"], "local-model")

    def test_stdin_only_and_json(self):
        self.add()
        with patch("ask_ai.cli.ask", return_value=("hi", {"text": "你好"})) as call:
            code, out, _ = self.cli("--json", stdin="hello")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {"text": "你好"})
            self.assertEqual(call.call_args.args[1], "hello")

    def test_http_errors_hide_secrets(self):
        with patch("ask_ai.client.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError("http://host", 401, "secret", {}, io.BytesIO(b'secret'))
            with self.assertRaisesRegex(UserError, "401") as caught:
                ask(self.profile, "hello")
            self.assertNotIn("secret", str(caught.exception))

    def test_transport_errors(self):
        for failure in (TimeoutError(), URLError("offline")):
            with patch("ask_ai.client.build_opener") as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(UserError):
                    ask(self.profile, "hello")

    def test_http_json_and_invalid_response(self):
        with patch("ask_ai.client.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b'{"choices":[{"message":{"content":"hello"}}]}')
            self.assertEqual(ask(self.profile, "hi")[0], "hello")
            opener.return_value.open.return_value = io.BytesIO(b'<html>wrong root</html>')
            with self.assertRaisesRegex(UserError, "JSON"):
                ask(self.profile, "hi")

    def test_profile_environment_selection(self):
        self.add()
        self.add("second")
        with patch.dict(os.environ, {"ASK_AI_PROFILE": "second"}), patch("ask_ai.cli.ask", return_value=("ok", {})):
            self.assertEqual(self.cli("hello")[0], 0)
            self.assertEqual(self.cli("-p", "missing", "hello")[0], 1)

    def test_atomic_save_failure_preserves_file(self):
        self.add()
        original = config_path().read_bytes()
        with patch("ask_ai.config.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                save_config({"version": 1, "profiles": {}, "default": None})
        self.assertEqual(config_path().read_bytes(), original)
        self.assertEqual(list(config_path().parent.glob(".ask-ai-*")), [])


if __name__ == "__main__":
    unittest.main()
