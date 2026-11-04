"""Command line entry point."""
import argparse
import getpass
import json
import math
import sys

from . import __version__
from .client import ask
from .chat import chat
from .reasoning import EFFORTS
from .config import PRESETS, UserError, config_path, get_profile, load_config, save_config, validate_profile


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的有限数值")
    return number


def temperature_value(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise argparse.ArgumentTypeError("temperature 必须在 0 到 2 之间（具体范围取决于模型）")
    return number


def prompt_value(label, default=""):
    value = input(f"{label}{' [' + default + ']' if default else ''}: ").strip()
    return value or default


def configure(argv):
    parser = argparse.ArgumentParser(prog="ask-ai config", description="交互式配置，或通过参数新增/更新档案")
    parser.add_argument("action", nargs="?", choices=["add", "list", "show", "use", "remove", "path"], default="add")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--provider", choices=PRESETS)
    parser.add_argument("--model")
    parser.add_argument("--base-url", help="API 根地址（通常包含 /v1），不含 /chat/completions")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--api-key-env", help="存储 API Key 环境变量的名称")
    auth.add_argument("--api-key", help="保存 API Key；推荐交互输入，避免写入 shell 历史")
    auth.add_argument("--no-key", action="store_true", help="清除已保存的密钥及环境变量引用")
    parser.add_argument("--system", help="保存默认系统提示词")
    parser.add_argument("--default", action="store_true", help="设置为默认配置")
    args = parser.parse_args(argv)
    if args.action == "path":
        print(config_path())
        return 0
    data = load_config()
    if args.action == "list":
        for name, profile in data["profiles"].items():
            mark = "*" if name == data.get("default") else " "
            print(f"{mark} {name}\t{profile['provider']}\t{profile['model']}\t{profile['base_url']}")
        if not data["profiles"]:
            print("尚无配置，请运行 ask-ai config。")
        return 0
    if args.action == "show":
        profile = get_profile(data, args.name)
        if "api_key" in profile:
            profile["api_key"] = "********" if profile["api_key"] else ""
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    if args.action in ("use", "remove"):
        if not args.name or args.name not in data["profiles"]:
            raise UserError("请指定存在的配置名称，使用 ask-ai config list 查看。")
        if args.action == "use":
            data["default"] = args.name
        else:
            del data["profiles"][args.name]
            if data.get("default") == args.name:
                data["default"] = next(iter(data["profiles"]), None)
        save_config(data)
        print("配置已更新。")
        return 0

    interactive = not any((args.provider, args.model, args.base_url, args.api_key is not None, args.api_key_env is not None, args.no_key, args.system is not None, args.default))
    if interactive and not sys.stdin.isatty():
        raise UserError("交互式配置需要终端；脚本中请指定 --provider 和 --model 等参数。")
    name = args.name or (prompt_value("配置名称", "default") if interactive else "default")
    if not name.strip():
        raise UserError("配置名称不能为空。")
    previous = data["profiles"].get(name, {})
    if interactive:
        print("服务类型：" + ", ".join(PRESETS))
    provider = args.provider or (prompt_value("服务类型", previous.get("provider", "openai")) if interactive else previous.get("provider", "openai"))
    if provider not in PRESETS:
        raise UserError("未知 provider，请运行 ask-ai providers。")
    # A change of provider must not carry old credentials to the new service.
    profile = previous.copy() if provider == previous.get("provider") else {"provider": provider}
    root = args.base_url if args.base_url is not None else profile.get("base_url", PRESETS[provider][1])
    model = args.model if args.model is not None else profile.get("model", "")
    if interactive:
        root = prompt_value("API 根地址", root)
        model = prompt_value("模型名称（使用服务端提供的准确名称）", model)
    profile.update(provider=provider, base_url=root.strip().rstrip("/"), model=model.strip())
    if args.system is not None:
        profile["system"] = args.system
    if args.no_key:
        profile.pop("api_key", None)
        profile.pop("api_key_env", None)
    elif args.api_key_env is not None:
        if not args.api_key_env.strip():
            raise UserError("环境变量名不能为空。")
        profile.pop("api_key", None)
        profile["api_key_env"] = args.api_key_env.strip()
    elif args.api_key is not None:
        profile.pop("api_key_env", None)
        profile["api_key"] = args.api_key
    elif interactive:
        key = getpass.getpass("API Key（不回显；留空保留原设置或使用环境变量，本地无认证节点可留空）: ").strip()
        if key:
            profile.pop("api_key_env", None)
            profile["api_key"] = key
    validate_profile(profile)
    data["profiles"][name] = profile
    if args.default or not data.get("default"):
        data["default"] = name
    save_config(data)
    print(f"已保存配置 {name} 至 {config_path()}。")
    return 0


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "config":
        return configure(argv[1:])
    if argv == ["providers"]:
        for name, (protocol, root, env) in PRESETS.items():
            print(f"{name:12} {protocol:10} {root or '<自定义 API 根地址>'}  {env}")
        return 0
    chat_mode = bool(argv and argv[0] == "chat")
    if chat_mode:
        argv.pop(0)
    parser = argparse.ArgumentParser(prog="ask-ai chat" if chat_mode else "ask-ai", description="终端多轮对话，/help 查看会话命令" if chat_mode else '一次配置，直接提问：ask-ai "what is pingpang"', epilog="聊天：ask-ai chat | 配置：ask-ai config | 管理：ask-ai config --help | 服务列表：ask-ai providers")
    parser.add_argument("prompt", nargs="*", help="问题；也可从标准输入读取")
    parser.add_argument("--version", action="version", version=f"ask-ai {__version__}")
    parser.add_argument("-p", "--profile", help="本次使用的配置名称")
    parser.add_argument("-m", "--model", help="临时覆盖模型名称")
    parser.add_argument("--system", help="临时覆盖系统提示词")
    parser.add_argument("--timeout", type=positive_float, default=120, help="网络读写超时秒数，默认 120")
    parser.add_argument("--max-tokens", type=positive_int, help="输出 token 上限，必须受模型支持")
    parser.add_argument("--temperature", type=temperature_value, help="采样温度，仅在指定时发送")
    reasoning = parser.add_mutually_exclusive_group()
    reasoning.add_argument("--think", action="store_const", const="medium", dest="reasoning_effort", help="开启思考（medium 强度）")
    reasoning.add_argument("-r", "--reasoning-effort", choices=EFFORTS, help="思考强度，默认 none 关闭；auto 使用服务端默认")
    parser.set_defaults(reasoning_effort="none")
    parser.add_argument("--json", action="store_true", help="输出 API 原始 JSON 响应")
    args = parser.parse_args(argv)
    prompt = " ".join(args.prompt).strip()
    if not chat_mode and not sys.stdin.isatty():
        context = sys.stdin.read().strip()
        if context:
            prompt = f"{prompt}\n\n{context}" if prompt else context
    if not prompt and not chat_mode:
        parser.print_help()
        return 0
    profile = get_profile(load_config(), args.profile)
    if args.model is not None:
        profile["model"] = args.model
    options = dict(timeout=args.timeout, system=args.system, max_tokens=args.max_tokens, temperature=args.temperature, reasoning_effort=args.reasoning_effort)
    if chat_mode:
        return chat(profile, initial_prompt=prompt, json_output=args.json, **options)
    text, data = ask(profile, prompt, **options)
    print(json.dumps(data, ensure_ascii=False) if args.json else text)
    return 0


def main(argv=None):
    try:
        return run(argv)
    except UserError as exc:
        print(f"ask-ai: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"ask-ai: 操作失败（{type(exc).__name__}），请检查配置、文件权限和网络。", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n已取消。", file=sys.stderr)
        return 130
