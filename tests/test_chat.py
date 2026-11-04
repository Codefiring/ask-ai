import contextlib
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

import test_cli
from ask_ai.chat import chat
from ask_ai.client import assistant_turn, make_request
from ask_ai.config import UserError


RESPONSE = {"choices": [{"message": {"content": "answer"}}]}


class ChatTests(unittest.TestCase):
    setUp = test_cli.AskAITests.setUp
    cli = test_cli.AskAITests.cli
    add = test_cli.AskAITests.add

    def run_chat(self, lines, replies=None, flags=()):
        self.add()
        calls = []
        replies = iter(replies or [("answer", RESPONSE)] * 10)

        def request(profile, prompt, **kwargs):
            calls.append((deepcopy(profile), prompt, deepcopy(kwargs)))
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("ask_ai.chat.ask", side_effect=request):
            result = self.cli("chat", *flags, stdin=lines)
        return result, calls

    def test_followup_receives_both_sides_of_prior_turn(self):
        result, calls = self.run_chat("我叫小明\n我叫什么？\n/exit\n")
        self.assertEqual(result, (0, "answer\nanswer\n", ""))
        self.assertEqual(calls[0][2]["history"], [])
        history = calls[1][2]["history"]
        self.assertEqual([(t["role"], t["content"]) for t in history], [("user", "我叫小明"), ("assistant", "answer")])
        self.assertEqual(calls[1][2]["reasoning_effort"], "none")

    def test_clear_keeps_session_options(self):
        result, calls = self.run_chat("first\n/clear\nsecond\n/quit\n", flags=("--system", "中文", "--think"))
        self.assertEqual(result[0], 0)
        self.assertEqual(calls[1][2]["history"], [])
        self.assertEqual(calls[1][2]["system"], "中文")
        self.assertEqual(calls[1][2]["reasoning_effort"], "medium")

    def test_failed_turn_does_not_pollute_history(self):
        result, calls = self.run_chat("first\nfailed\nretry\n", replies=[("answer", RESPONSE), UserError("timeout"), ("answer", RESPONSE)])
        self.assertEqual(result[0], 1)  # Scripts can detect that a turn failed.
        self.assertIn("本轮未加入上下文", result[2])
        self.assertEqual(calls[1][2]["history"], calls[2][2]["history"])
        self.assertEqual(len(calls[2][2]["history"]), 2)

    def test_initial_prompt_then_stdin(self):
        result, calls = self.run_chat("followup\n", flags=("initial question",))
        self.assertEqual([call[1] for call in calls], ["initial question", "followup"])
        self.assertEqual(result[0], 0)

    def test_multiline_and_literal_commands(self):
        _, calls = self.run_chat("\n/help\n/unknown\n/multiline\nline1\n\nline2\n.\n//exit\n/exit\n")
        self.assertEqual([call[1] for call in calls], ["line1\n\nline2", "/exit"])

    def test_eof_during_multiline_discards_partial_input(self):
        result, calls = self.run_chat("/multiline\nunfinished\n")
        self.assertEqual(result[0], 0)
        self.assertEqual(calls, [])

    def test_json_outputs_one_response_per_line(self):
        result, _ = self.run_chat("hello\nagain\n", flags=("--json",))
        self.assertEqual([json.loads(line) for line in result[1].splitlines()], [RESPONSE, RESPONSE])

    def test_ctrl_c_exits_cleanly(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(chat(self.profile), 130)

    def test_terminal_prompts_and_session_isolation(self):
        self.add()
        terminal = io.StringIO()
        terminal.isatty = lambda: True
        output = io.StringIO()
        with patch("sys.stdin", terminal), patch("builtins.input", side_effect=["hello", "/exit"]) as read, patch("ask_ai.chat.ask", return_value=("answer", RESPONSE)), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(chat(self.profile), 0)
            read.assert_called_with("你> ")
        self.assertIn("AI> answer", output.getvalue())
        _, calls = self.run_chat("fresh\n")
        self.assertEqual(calls[0][2]["history"], [])

    def test_literal_chat_still_works_in_single_question_mode(self):
        self.add()
        with patch("ask_ai.cli.ask", return_value=("answer", RESPONSE)) as request:
            self.assertEqual(self.cli("--", "chat")[0], 0)
            self.assertEqual(request.call_args.args[1], "chat")


class HistoryProtocolTests(unittest.TestCase):
    def test_native_history_and_system_survive_next_request(self):
        cases = [
            ("vllm", {"choices": [{"message": {"content": "answer", "reasoning_content": "reasoning"}}]}),
            ("anthropic", {"content": [{"type": "thinking", "thinking": "reasoning", "signature": "signed"}, {"type": "redacted_thinking", "data": "opaque"}, {"type": "text", "text": "answer"}]}),
            ("gemini", {"candidates": [{"content": {"parts": [{"text": "reasoning", "thought": True}, {"text": "answer", "thoughtSignature": "signed"}]}}]}),
        ]
        for provider, response in cases:
            with self.subTest(provider=provider):
                profile = {"provider": provider, "model": "test-model", "base_url": "http://localhost/v1", "system": "be brief"}
                history = [{"role": "user", "content": "first"}, assistant_turn(profile, "answer", response)]
                original = deepcopy(history)
                request, _ = make_request(profile, "followup", history=history)
                body = json.loads(request.data)
                self.assertEqual(history, original)
                if provider == "gemini":
                    self.assertEqual([t["role"] for t in body["contents"]], ["user", "model", "user"])
                    self.assertEqual(body["contents"][1]["parts"], response["candidates"][0]["content"]["parts"])
                    self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "be brief")
                elif provider == "anthropic":
                    self.assertEqual(body["messages"][1]["content"], response["content"])
                    self.assertEqual(body["system"], "be brief")
                else:
                    self.assertEqual([t["role"] for t in body["messages"]], ["system", "user", "assistant", "user"])
                    self.assertEqual(body["messages"][2]["reasoning_content"], "reasoning")
                self.assertNotIn("_native", request.data.decode())

    def test_plain_gemini_history_maps_assistant_role(self):
        profile = {"provider": "gemini", "model": "test-model", "base_url": "http://localhost/v1"}
        request, _ = make_request(profile, "next", history=[{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}])
        self.assertEqual(json.loads(request.data)["contents"][1], {"role": "model", "parts": [{"text": "answer"}]})
