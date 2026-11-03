"""Persistent profiles and provider presets."""
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


class UserError(Exception):
    """An actionable error safe to display without a traceback."""


# provider: (wire protocol, API root, optional conventional key environment variable)
PRESETS = {
    "openai": ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
    "deepseek": ("openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "qwen": ("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "groq": ("openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "vllm": ("openai", "http://localhost:8000/v1", ""),
    "ollama": ("openai", "http://localhost:11434/v1", ""),
    "custom": ("openai", "", ""),
}


def config_path():
    if os.environ.get("ASK_AI_CONFIG"):
        return Path(os.environ["ASK_AI_CONFIG"]).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ask-ai" / "config.json"


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise UserError("配置档案格式错误。")
    if profile.get("provider") not in PRESETS:
        raise UserError("未知 provider，请运行 ask-ai providers。")
    for field in ("model", "base_url"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise UserError(f"配置缺少 {field}。")
    url = urlsplit(profile["base_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise UserError("base_url 必须是 http(s) API 根地址，不能含账号、密码、查询参数或片段。")
    for field in ("api_key", "api_key_env", "system"):
        if field in profile and not isinstance(profile[field], str):
            raise UserError(f"{field} 必须是字符串。")


def load_config():
    path = config_path()
    if not path.exists():
        return {"version": 1, "default": None, "profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UserError(f"无法读取配置 {path}，请检查文件格式和权限。") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("profiles"), dict):
        raise UserError(f"配置格式或版本不受支持：{path}")
    for profile in data["profiles"].values():
        validate_profile(profile)
    if data.get("default") is not None and (not isinstance(data["default"], str) or data["default"] not in data["profiles"]):
        raise UserError("默认配置不存在，请修正配置文件。")
    return data


def save_config(data):
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkstemp uses 0600; atomic replacement never exposes a partly written secret.
    fd, temporary = tempfile.mkstemp(prefix=".ask-ai-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_profile(data, name=None):
    name = name or os.environ.get("ASK_AI_PROFILE") or data.get("default")
    if not name or name not in data["profiles"]:
        raise UserError("未找到配置，请先运行 ask-ai config，或用 -p 指定已有配置。")
    return data["profiles"][name].copy()


def api_key(profile):
    env = profile.get("api_key_env")
    if env:
        value = os.environ.get(env)
        if not value:
            raise UserError(f"请设置环境变量 {env}。")
        return value
    return profile.get("api_key") or os.environ.get(PRESETS[profile["provider"]][2], "")
