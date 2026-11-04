"""A terminal chat session with automatic local persistence."""
from contextlib import ExitStack
import json
import sys

from .client import ask, assistant_turn
from .config import UserError
from .sessions import clean_title, generate_title, load_session, new_session, save_session, session_lock

HELP = "/help 帮助 · /save 保存 · /clear 新会话（保留旧会话）· /multiline 多行输入（单独一行 . 发送）· /exit 或 /quit 退出"


def persist(session):
    try:
        save_session(session)
    except OSError as exc:
        raise UserError("会话保存失败，请检查本地磁盘空间及会话目录权限；本轮可能未保存。") from exc


def chat(profile, initial_prompt="", json_output=False, profile_name="default", session=None, **options):
    options["system"] = profile.get("system", "") if options.get("system") is None else options["system"]
    current = session or new_session(profile_name, profile, options)
    with ExitStack() as locks:
        locks.enter_context(session_lock(current["id"]))
        if session is not None:
            current = load_session(current["id"])
            current["options"] = options
        return _chat(profile, current, locks, initial_prompt, json_output, **options)


def _chat(profile, session, locks, initial_prompt, json_output, **options):
    interactive = sys.stdin.isatty()
    if interactive:
        # readline improves terminal editing when available; never writes history.
        try:
            import readline
            readline.set_auto_history(False)
        except ImportError:
            pass
        print(f"ask-ai chat · {profile['model']} · 思考 {options.get('reasoning_effort', 'none')}\n{HELP}", file=sys.stderr)
        if session["history"]:
            print(f"已恢复：{clean_title(session['title'])} [{session['id'][:12]}] · {len(session['history']) // 2} 轮", file=sys.stderr)
            print(f"上次回答：{session['history'][-1]['content']}", file=sys.stderr)
    history = session["history"]
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
                if history:
                    persist(session)
                session = new_session(session["profile"], profile, options)
                locks.close()
                locks.enter_context(session_lock(session["id"]))
                history = session["history"]
                print("已清空当前上下文并开始新会话，旧会话保留，可用 resume 恢复。", file=sys.stderr)
                continue
            if prompt == "/save":
                if history:
                    persist(session)
                    print(f"已保存：{clean_title(session['title'])} [{session['id'][:12]}]", file=sys.stderr)
                else:
                    print("暂无对话内容可保存。", file=sys.stderr)
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
            if len(history) == 2 and not session["title_attempted"]:
                session["title"] = clean_title(prompt) or "新会话"
            if json_output:
                print(json.dumps(data, ensure_ascii=False), flush=True)
            else:
                print(("AI> " if interactive else "") + text, flush=True)
                if interactive:
                    print()
            # Save the response before naming, so interruption cannot lose a turn.
            persist(session)
            if not session["title_attempted"]:
                session["title_attempted"] = True
                persist(session)
                try:
                    session["title"] = generate_title(profile, history, options)
                except (UserError, OSError, ValueError):
                    print("会话自动命名失败，已使用首条问题作为名称；对话已保存。", file=sys.stderr)
                persist(session)
                if interactive:
                    print(f"会话：{clean_title(session['title'])} [{session['id'][:12]}]（自动保存）", file=sys.stderr)
        except EOFError:
            if interactive:
                print(file=sys.stderr)
            return int(had_error and not interactive)
        except KeyboardInterrupt:
            print("\n已退出会话。", file=sys.stderr)
            return 130
