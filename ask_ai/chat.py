"""An in-memory terminal chat session."""
import json
import sys

from .client import ask, assistant_turn
from .config import UserError

HELP = "/help 帮助 · /clear 清空上下文 · /multiline 多行输入（单独一行 . 发送）· /exit 或 /quit 退出"


def chat(profile, initial_prompt="", json_output=False, **options):
    interactive = sys.stdin.isatty()
    if interactive:
        # readline improves terminal editing when available; never writes history.
        try:
            import readline
            readline.set_auto_history(False)
        except ImportError:
            pass
        print(f"ask-ai chat · {profile['model']} · 思考 {options.get('reasoning_effort', 'none')}\n{HELP}", file=sys.stderr)
    history = []
    pending = initial_prompt
    had_error = False
    while True:
        try:
            prompt = pending or input("你> " if interactive else "")
            pending = ""
            prompt = prompt.strip()
            if not prompt:
                continue
            if prompt in ("/exit", "/quit"):
                return int(had_error and not interactive)
            if prompt == "/help":
                print(HELP, file=sys.stderr)
                continue
            if prompt == "/clear":
                history.clear()
                print("已清空当前会话上下文。", file=sys.stderr)
                continue
            if prompt == "/multiline":
                lines = []
                while True:
                    line = input("... " if interactive else "")
                    if line == ".":
                        break
                    lines.append(line)
                prompt = "\n".join(lines).strip()
                if not prompt:
                    continue
            elif prompt.startswith("//"):
                prompt = prompt[1:]  # Send a literal slash command to the model.
            elif prompt.startswith("/"):
                print("未知会话命令。使用 /help 查看，或用 // 开头发送字面量 /。", file=sys.stderr)
                continue
            try:
                text, data = ask(profile, prompt, history=history, **options)
            except UserError as exc:
                had_error = True
                print(f"ask-ai: {exc}\n本轮未加入上下文，可重新输入；/clear 开始新会话。", file=sys.stderr)
                continue
            # Commit the pair only after the request and response parsing succeed.
            reply = assistant_turn(profile, text, data)
            history.extend([{"role": "user", "content": prompt}, reply])
            if json_output:
                print(json.dumps(data, ensure_ascii=False), flush=True)
            else:
                print(("AI> " if interactive else "") + text, flush=True)
                if interactive:
                    print()
        except EOFError:
            if interactive:
                print(file=sys.stderr)
            return int(had_error and not interactive)
        except KeyboardInterrupt:
            print("\n已退出会话。", file=sys.stderr)
            return 130
