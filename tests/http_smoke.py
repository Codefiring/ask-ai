"""Optional end-to-end check: local HTTP server plus installed CLI subprocesses."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


requests = []


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
            run("-p", "vllm", "summarize", stdin="some context")
            assert requests[-1][1]["messages"][-1]["content"] == "summarize\n\nsome context"
            print("HTTP smoke passed: installed CLI, persisted profiles, 3 protocols, pipeline input")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
