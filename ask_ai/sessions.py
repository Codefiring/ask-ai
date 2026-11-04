"""Private, atomic on-disk chat sessions and a terminal session picker."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from .client import ask
from .config import UserError, config_path


def sessions_dir():
    return Path(os.environ["ASK_AI_SESSIONS_DIR"]).expanduser() if os.environ.get("ASK_AI_SESSIONS_DIR") else config_path().parent / "sessions"


def clean_title(text):
    return " ".join("".join(c for c in text if c.isprintable() or c in "\n\t").split()).strip('"\'` ')[:80]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_session(profile_name, profile, options):
    return {
        "version": 1, "id": uuid.uuid4().hex, "title": "新会话", "title_attempted": False,
        "created_at": now(), "updated_at": now(), "profile": profile_name,
        "provider": profile["provider"], "base_url": profile["base_url"], "model": profile["model"],
        "options": {key: value for key, value in options.items() if key in ("system", "timeout", "max_tokens", "temperature", "reasoning_effort")},
        "history": [],
    }


def session_path(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise UserError("无效会话 ID。")
    return sessions_dir() / f"{session_id}.json"


@contextmanager
def session_lock(session_id):
    """Prevent two interactive clients from overwriting the same conversation."""
    path = session_path(session_id).with_suffix(".lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UserError("该会话正在另一个终端使用，请先退出那个会话。") from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UserError("该会话正在另一个终端使用，请先退出那个会话。") from exc
        yield
    finally:
        os.close(fd)


def save_session(session):
    path = session_path(session["id"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    session["updated_at"] = now()
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(session, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_session(session_id):
    path = session_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("id") != session_id:
            raise ValueError()
        for key in ("title", "created_at", "updated_at", "profile", "provider", "base_url", "model"):
            if not isinstance(data.get(key), str):
                raise ValueError()
        if not isinstance(data.get("options"), dict) or not isinstance(data.get("history"), list):
            raise ValueError()
        if set(data["options"]) - {"system", "timeout", "max_tokens", "temperature", "reasoning_effort"}:
            raise ValueError()
        if len(data["history"]) % 2:
            raise ValueError()
        for index, turn in enumerate(data["history"]):
            if not isinstance(turn, dict) or turn.get("role") != ("user" if index % 2 == 0 else "assistant") or not isinstance(turn.get("content"), str):
                raise ValueError()
            if "_native" in turn and not isinstance(turn["_native"], dict):
                raise ValueError()
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise UserError(f"无法读取会话 {session_id[:12]}，请检查文件格式和权限。") from exc


def list_sessions():
    result = []
    for path in sessions_dir().glob("*.json"):
        try:
            result.append(load_session(path.stem))
        except UserError:
            print(f"ask-ai: 跳过无法读取的会话文件 {path.name}", file=sys.stderr)
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


def choose_session(identifier=None, list_only=False):
    sessions = list_sessions()
    if identifier:
        if not re.fullmatch(r"[a-f0-9]{4,32}", identifier):
            raise UserError("请输入会话 ID 或至少 4 位 ID 前缀。")
        matches = [item for item in sessions if item["id"].startswith(identifier)]
        if len(matches) != 1:
            raise UserError("会话不存在或 ID 前缀不唯一，请运行 ask-ai resume --list 查看。")
        return matches[0]
    if not sessions:
        print("还没有保存的会话，运行 ask-ai chat 开始。", file=sys.stderr)
        return None
    output = sys.stdout if list_only else sys.stderr
    print("已保存的会话（最近使用优先）：", file=output)
    for index, session in enumerate(sessions, 1):
        print(f"{index:>3}. {clean_title(session['title'])}  [{session['id'][:12]}]  {session['updated_at'][:19]} UTC  {len(session['history']) // 2} 轮", file=output)
    if list_only:
        return None
    if not sys.stdin.isatty():
        raise UserError("非交互模式请指定会话 ID：ask-ai resume <ID>。")
    while True:
        print("选择序号（回车选择最近会话，q 取消）: ", end="", file=sys.stderr, flush=True)
        choice = input().strip()
        if choice.lower() in ("q", "quit", "/exit"):
            return None
        if not choice:
            return sessions[0]
        if choice.isdecimal() and 1 <= int(choice) <= len(sessions):
            return sessions[int(choice) - 1]
        print("请输入列表中的序号。", file=sys.stderr)


def generate_title(profile, history, options):
    title_options = dict(options)
    title_options["system"] = "为这段对话生成简短的会话标题（2到8个词，中文不超过20字）。仅输出标题，不要引号、解释或回答对话内容。"
    title_options["timeout"] = min(options.get("timeout", 120), 30)
    title_options.pop("temperature", None)
    title_options.pop("max_tokens", None)
    prompt = "用户：" + history[0]["content"][:1500] + "\n助手：" + history[1]["content"][:1500]
    title, _ = ask(profile, prompt, **title_options)
    title = clean_title(title)
    if not title:
        raise UserError("模型未生成有效会话名称。")
    return title
