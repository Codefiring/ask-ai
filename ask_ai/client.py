"""HTTP adapters for OpenAI compatible, Anthropic and Gemini APIs."""
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import PRESETS, UserError, api_key, validate_profile


class NoRedirect(HTTPRedirectHandler):
    # Do not forward authentication to a different origin via redirects.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_request(profile, prompt, system=None, max_tokens=None, temperature=None):
    validate_profile(profile)
    protocol = PRESETS[profile["provider"]][0]
    root = profile["base_url"].rstrip("/")
    key = api_key(profile)
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ask-ai/0.1.0"}
    model = profile["model"]
    system = profile.get("system", "") if system is None else system
    if protocol == "anthropic":
        url = root + "/messages"
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
        body = {"model": model, "max_tokens": max_tokens or 4096, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
    elif protocol == "gemini":
        url = root + "/models/" + quote(model.removeprefix("models/"), safe="") + ":generateContent"
        if key:
            headers["x-goog-api-key"] = key
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        generation = {}
        if max_tokens is not None:
            generation["maxOutputTokens"] = max_tokens
        if temperature is not None:
            generation["temperature"] = temperature
        if generation:
            body["generationConfig"] = generation
    else:
        url = root + "/chat/completions"
        if key:
            headers["Authorization"] = "Bearer " + key
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        body = {"model": model, "messages": messages, "stream": False}
        if max_tokens is not None:
            body["max_completion_tokens" if profile["provider"] == "openai" else "max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
    return Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers), protocol


def extract_text(data, protocol):
    try:
        if protocol == "anthropic":
            text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
        elif protocol == "gemini":
            text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"] if not part.get("thought"))
        else:
            message = data["choices"][0]["message"]
            text = message.get("content") or message.get("refusal")
            if isinstance(text, list):
                text = "".join(block.get("text", "") for block in text if block.get("type") == "text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty response")
        return text
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise UserError("API 未返回可用文本，请检查模型、响应格式或服务端内容限制。") from exc


def ask(profile, prompt, timeout=120, **options):
    request, protocol = make_request(profile, prompt, **options)
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = json.load(response)
    except HTTPError as exc:
        # Server error bodies may echo credentials or prompts; never print them.
        hints = {401: "认证失败，请检查 API Key", 403: "访问被拒绝，请检查权限", 404: "接口或模型不存在，请检查 API 根地址和模型名", 429: "请求限流或额度不足", 400: "请求参数不被模型支持，请检查模型及可选参数"}
        raise UserError(f"HTTP {exc.code}：{hints.get(exc.code, '服务请求失败，请检查节点状态和地址')}。") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise UserError("请求超时，可通过 --timeout 增加等待时间。") from exc
    except URLError as exc:
        raise UserError("无法连接 API，请检查节点地址、网络、代理或 TLS 证书。") from exc
    except (ValueError, UnicodeError) as exc:
        raise UserError("API 返回的内容不是有效 JSON，请检查 API 根地址。") from exc
    return extract_text(data, protocol), data
