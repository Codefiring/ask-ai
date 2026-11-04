import contextlib
from copy import deepcopy
import io
import json
import os
import stat
import unittest
from unittest.mock import patch

import test_cli
from ask_ai.config import UserError
from ask_ai.sessions import choose_session, generate_title, list_sessions, load_session, new_session, save_session, session_lock, session_path

RESPONSE = {"choices": [{"message": {"content": "answer"}}]}


class SessionTests(unittest.TestCase):
    setUp = test_cli.AskAITests.setUp
    cli = test_cli.AskAITests.cli
    add = test_cli.AskAITests.add

    def create_chat(self, lines="hello\n/exit\n", flags=()):
        self.add()
        with patch("ask_ai.chat.ask", return_value=("answer", RESPONSE)), patch("ask_ai.chat.generate_title", return_value="Python 学习计划") as naming:
            result = self.cli("chat", *flags, stdin=lines)
        self.assertEqual(result[0], 0, result)
        return list_sessions(), naming.call_count

    def test_auto_save_and_title_only_once(self):
        sessions, count = self.create_chat("hello\nfollowup\n/save\n/exit\n")
        self.assertEqual(count, 1)
        self.assertEqual(len(sessions), 1)
        saved = sessions[0]
        self.assertEqual(saved["title"], "Python 学习计划")
        self.assertEqual(len(saved["history"]), 4)
        self.assertTrue(saved["title_attempted"])
        self.assertEqual(saved["profile"], "local")
        self.assertEqual(stat.S_IMODE(session_path(saved["id"]).stat().st_mode), 0o600)

    def test_resume_restores_context_options_and_does_not_rename(self):
        sessions, _ = self.create_chat(flags=("--system", "中文", "--think", "-m", "original-model"))
        saved = sessions[0]
        seen = []
        def request(profile, prompt, **options):
            seen.append((deepcopy(profile), deepcopy(options)))
            return "answer", RESPONSE
        with patch("ask_ai.chat.ask", side_effect=request), patch("ask_ai.chat.generate_title") as naming:
            result = self.cli("resume", saved["id"][:12], stdin="next question\n/exit\n")
        self.assertEqual(result[0], 0, result)
        self.assertEqual(seen[0][0]["model"], "original-model")
        self.assertEqual(len(seen[0][1]["history"]), 2)
        self.assertEqual(seen[0][1]["system"], "中文")
        self.assertEqual(seen[0][1]["reasoning_effort"], "medium")
        naming.assert_not_called()
        self.assertEqual(len(load_session(saved["id"])["history"]), 4)

    def test_title_failure_preserves_fallback_and_conversation(self):
        self.add()
        with patch("ask_ai.chat.ask", return_value=("answer", RESPONSE)), patch("ask_ai.chat.generate_title", side_effect=UserError("offline")):
            result = self.cli("chat", stdin="首条问题\n")
        self.assertEqual(result[0], 0)
        saved = list_sessions()[0]
        self.assertEqual(saved["title"], "首条问题")
        self.assertEqual(len(saved["history"]), 2)
        self.assertIn("自动命名失败", result[2])

    def test_interruption_during_title_keeps_first_turn(self):
        self.add()
        with patch("ask_ai.chat.ask", return_value=("answer", RESPONSE)), patch("ask_ai.chat.generate_title", side_effect=KeyboardInterrupt):
            self.assertEqual(self.cli("chat", stdin="hello\n")[0], 130)
        self.assertEqual(len(list_sessions()[0]["history"]), 2)

    def test_clear_preserves_old_session(self):
        sessions, count = self.create_chat("first\n/clear\nsecond\n/exit\n")
        self.assertEqual(count, 2)
        self.assertEqual(len(sessions), 2)
        self.assertEqual([s["history"][0]["content"] for s in sessions], ["second", "first"])

    def test_failed_first_request_does_not_create_session(self):
        self.add()
        with patch("ask_ai.chat.ask", side_effect=UserError("offline")), patch("ask_ai.chat.generate_title") as naming:
            self.assertEqual(self.cli("chat", stdin="hello\n")[0], 1)
        self.assertEqual(list_sessions(), [])
        naming.assert_not_called()

    def test_resume_picker_and_cancel(self):
        sessions, _ = self.create_chat()
        terminal = io.StringIO()
        terminal.isatty = lambda: True
        with patch("sys.stdin", terminal), patch("builtins.input", side_effect=["invalid", "1"]), contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(choose_session()["id"], sessions[0]["id"])
        self.assertIn("Python 学习计划", output.getvalue())
        with patch("sys.stdin", terminal), patch("builtins.input", return_value="q"), contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(choose_session())

    def test_list_without_interactive_input_and_resume_requires_id(self):
        self.create_chat()
        result = self.cli("resume", "--list")
        self.assertEqual(result[0], 0)
        self.assertIn("Python 学习计划", result[1])
        self.assertEqual(self.cli("resume")[0], 1)
        self.assertEqual(self.cli("resume", "../../outside")[0], 1)

    def test_api_keys_are_not_stored_and_changed_endpoint_is_rejected(self):
        self.add()
        self.cli("config", "add", "local", "--api-key", "private-test-key")
        sessions, _ = self.create_chat()
        saved = sessions[0]
        self.assertNotIn("private-test-key", session_path(saved["id"]).read_text())
        self.cli("config", "add", "local", "--base-url", "http://different-host/v1")
        with patch("ask_ai.chat.ask") as request:
            self.assertEqual(self.cli("resume", saved["id"], stdin="hello\n")[0], 1)
            request.assert_not_called()

    def test_atomic_failure_preserves_previous_session(self):
        sessions, _ = self.create_chat()
        saved = sessions[0]
        original = session_path(saved["id"]).read_bytes()
        with patch("ask_ai.sessions.os.replace", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                save_session(saved)
        self.assertEqual(session_path(saved["id"]).read_bytes(), original)

    @unittest.skipIf(os.name == "nt", "POSIX lock check")
    def test_concurrent_resume_rejected_and_lock_released(self):
        sessions, _ = self.create_chat()
        session_id = sessions[0]["id"]
        with session_lock(session_id):
            with self.assertRaisesRegex(UserError, "另一个终端"):
                with session_lock(session_id):
                    pass
        with session_lock(session_id):
            pass

    def test_title_request_separate_from_history_and_sanitized(self):
        history = [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]
        options = {"system": "original", "reasoning_effort": "none", "timeout": 120}
        with patch("ask_ai.sessions.ask", return_value=('"Python\n学习"', {})) as request:
            self.assertEqual(generate_title(self.profile, history, options), "Python 学习")
            self.assertNotIn("history", request.call_args.kwargs)
            self.assertEqual(request.call_args.kwargs["timeout"], 30)
        self.assertEqual(options["system"], "original")
        self.assertEqual(len(history), 2)

    def test_corrupt_file_is_skipped_but_other_sessions_work(self):
        sessions, _ = self.create_chat()
        broken = new_session("local", self.profile, {})
        session_path(broken["id"]).write_text("broken")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual([s["id"] for s in list_sessions()], [sessions[0]["id"]])
