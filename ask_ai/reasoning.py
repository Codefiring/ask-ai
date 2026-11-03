"""Translate a CLI reasoning preference into each provider's wire parameters."""
import re

from .config import UserError

EFFORTS = ("none", "low", "medium", "high", "auto")
BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}


def apply_reasoning(body, profile, effort="none", max_tokens=None):
    if effort not in EFFORTS:
        raise UserError("未知思考强度，请选择 none、low、medium、high 或 auto。")
    if effort == "auto":
        return  # Explicit compatibility escape hatch: let the server choose.
    enabled = effort != "none"
    provider = profile["provider"]
    model = profile["model"].lower().removeprefix("models/")
    if provider == "anthropic":
        if not enabled:
            body["thinking"] = {"type": "disabled"}
        elif re.search(r"claude-(?:opus|sonnet)-(?:4-[6-9]|[5-9])|claude-(?:fable|mythos)", model):
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": effort}
        else:
            budget = BUDGETS[effort]
            if max_tokens is not None and max_tokens <= budget:
                raise UserError(f"此 Claude 模式的 --max-tokens 必须大于思考预算 {budget}，或降低 --reasoning-effort。")
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max_tokens if max_tokens is not None else budget + 4096
        return
    if provider == "gemini":
        if model.startswith(("gemini-1.", "gemini-2.0")):
            if enabled:
                raise UserError("该 Gemini 模型不支持思考，请选择支持思考的模型。")
            return
        if model.startswith("gemini-3"):
            if not enabled:
                raise UserError("Gemini 3 无法完全关闭思考；请显式使用 --think 或 --reasoning-effort low，或选择可关闭思考的模型。")
            if model.startswith("gemini-3-pro") and effort == "medium":
                # Gemini 3 Pro exposes low/high only; choose low for the speed preset.
                level = "low"
            else:
                level = effort
            thinking = {"thinkingLevel": level}
        else:
            if not enabled and model.startswith("gemini-2.5-pro"):
                raise UserError("Gemini 2.5 Pro 无法关闭思考；请使用 --think 或选择 Flash 模型。")
            thinking = {"thinkingBudget": BUDGETS[effort] if enabled else 0}
        body.setdefault("generationConfig", {})["thinkingConfig"] = thinking
        return
    if provider == "deepseek":
        body["thinking"] = {"type": "enabled" if enabled else "disabled"}
    elif provider == "qwen":
        body["enable_thinking"] = enabled
        if enabled:
            body["thinking_budget"] = BUDGETS[effort]
    elif provider == "openrouter":
        body["reasoning"] = {"enabled": enabled}
        if enabled:
            body["reasoning"]["effort"] = effort
    elif provider == "vllm":
        body["chat_template_kwargs"] = {"enable_thinking": enabled}
        if enabled:
            body["reasoning_effort"] = effort
    elif provider == "openai" and model.startswith(("gpt-4", "gpt-3.5", "chatgpt-4")):
        if enabled:
            raise UserError("该 OpenAI 模型不支持思考强度，请选择推理模型。")
        # Non-reasoning models reject reasoning_effort, even when it is 'none'.
    elif provider == "groq" and model.startswith(("llama", "meta-llama/", "gemma", "mixtral")):
        if enabled:
            raise UserError("该 Groq 模型不支持思考强度，请选择推理模型。")
    else:
        body["reasoning_effort"] = effort
