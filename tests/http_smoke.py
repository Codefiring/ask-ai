"""Optional end-to-end check: local HTTP server plus installed CLI subprocesses."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


requests = []


def check_terminal(command, env, cwd):
    """Exercise readline and prompts against a real pseudo-terminal on POSIX."""
    import pty
    import select

    master, slave = pty.openpty()
    process = None
    try:
        process = subprocess.Popen([command, "chat", "-p", "vllm"], stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env)

        def expect(marker):
            output = b""
            deadline = time.monotonic() + 10
            while marker.encode() not in output:
                remaining = deadline - time.monotonic()
                assert remaining > 0, output.decode(errors="replace")
                ready, _, _ = select.select([master], [], [], remaining)
                assert ready, output.decode(errors="replace")
                output += os.read(master, 65536)

        expect("你> ")
        os.write(master, b"first\n")
        expect("你> ")
        os.write(master, b"followup\n")
        expect("你> ")
        os.write(master, b"/exit\n")
        assert process.wait(timeout=10) == 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
        os.close(slave)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append((self.path, body, self.headers))
        if self.path.endswith("/messages"):
            response = {"content": [{"type": "text", "text": "你好，Claude"}]}
        elif self.path.endswith(":generateContent"):
            response = {"candidates": [{"content": {"parts": [{"text": "你好，Gemini"}]}}]}
        else:
            response = {"choices": [{"message": {"content": "你好，vLLM"}}]}
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, help="Test a standalone executable instead of the installed Python entry point")
    args = parser.parse_args()
    command = str(args.binary.resolve() if args.binary else Path(__file__).resolve().parents[1] / ".venv/bin/ask-ai")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, ASK_AI_CONFIG=str(Path(temporary) / "config.json"), NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
            env.pop("ASK_AI_PROFILE", None)
            if args.binary:
                # The executable must work without any interpreter on PATH.
                env["PATH"] = temporary
                for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
                    env.pop(key, None)

            def run(*args, stdin=""):
                result = subprocess.run([command, *args], input=stdin, text=True, capture_output=True, env=env, timeout=10, cwd=temporary)
                assert result.returncode == 0, result.stderr
                return result.stdout

            for provider, answer in (("vllm", "vLLM"), ("anthropic", "Claude"), ("gemini", "Gemini")):
                run("config", "add", provider, "--provider", provider, "--model", "test-model", "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--api-key", "test-key", "--default")
                assert run("what is pingpang") == f"你好，{answer}\n"
            assert requests[0][0] == "/v1/chat/completions"
            assert requests[0][2]["Authorization"] == "Bearer test-key"
            assert requests[1][0] == "/v1/messages"
            assert requests[1][2]["x-api-key"] == "test-key"
            assert requests[2][0] == "/v1/models/test-model:generateContent"
            assert requests[2][2]["x-goog-api-key"] == "test-key"
            assert requests[0][1]["chat_template_kwargs"]["enable_thinking"] is False
            assert requests[1][1]["thinking"] == {"type": "disabled"}
            assert requests[2][1]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
            run("-p", "vllm", "--think", "reason about this")
            assert requests[-1][1]["chat_template_kwargs"]["enable_thinking"] is True
            assert requests[-1][1]["reasoning_effort"] == "medium"
            run("-p", "vllm", "-r", "high", "reason about this")
            assert requests[-1][1]["reasoning_effort"] == "high"
            run("-p", "vllm", "-r", "auto", "hello")
            assert "reasoning_effort" not in requests[-1][1]
            assert "chat_template_kwargs" not in requests[-1][1]
            run("-p", "vllm", "summarize", stdin="some context")
            assert requests[-1][1]["messages"][-1]["content"] == "summarize\n\nsome context"
            for provider in ("vllm", "anthropic", "gemini"):
                start = len(requests)
                run("chat", "-p", provider, stdin="first\nfollowup\n/clear\nfresh\n/exit\n")
                conversation = requests[start:]
                field = "contents" if provider == "gemini" else "messages"
                assert [len(item[1][field]) for item in conversation] == [1, 3, 1]
                assert conversation[1][1][field][1]["role"] == ("model" if provider == "gemini" else "assistant")
            if os.name == "posix":
                start = len(requests)
                check_terminal(command, env, temporary)
                assert [len(item[1]["messages"]) for item in requests[start:]] == [1, 3]
            print("HTTP smoke passed: CLI, profiles, 3 protocols, reasoning, pipelines, multi-turn chat, clear and terminal")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
