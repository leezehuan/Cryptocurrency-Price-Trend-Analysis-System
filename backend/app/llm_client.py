from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .config_loader import load_model_api_config, load_prompts_config, load_skill_prompt


class LLMClientError(RuntimeError):
    pass


class SafePromptValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def prompt_value(value: Any) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    return str(value)


def render_template(template: str, values: dict[str, Any]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value))
    return result


def prompt_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def extract_json_payload(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise LLMClientError("model response does not contain a JSON object") from None
        payload = json.loads(clean[start : end + 1])
    if not isinstance(payload, dict):
        raise LLMClientError("model response JSON must be an object")
    return payload


def standard_node_output(
    node_name: str,
    data: dict[str, Any] | None = None,
    success: bool = True,
    model_confidence: float = 0.0,
    needs_human_review: bool = False,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "node_name": node_name,
        "model_confidence": model_confidence,
        "needs_human_review": needs_human_review,
        "data": data or {},
        "warnings": warnings or [],
        "errors": errors or [],
        "evidence": evidence or [],
    }


def normalize_node_output(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return standard_node_output(
        node_name=node_name,
        data=data,
        success=bool(payload.get("success", True)),
        model_confidence=float(payload.get("model_confidence") or payload.get("confidence") or 0.0),
        needs_human_review=bool(payload.get("needs_human_review", payload.get("needs_user_confirmation", False))),
        warnings=list(payload.get("warnings") or []),
        errors=list(payload.get("errors") or []),
        evidence=list(payload.get("evidence") or []),
    )


def get_node_prompt(graph_name: str, node_name: str) -> dict[str, Any]:
    prompts = load_prompts_config()
    if graph_name == "skills":
        node_prompt = load_skill_prompt(node_name)
        if not node_prompt:
            raise LLMClientError(f"missing skill prompt config: {node_name}")
        return {
            "global_system_prompt": prompts.get("global_system_prompt", ""),
            **node_prompt,
        }
    graph = prompts.get("graphs", {}).get(graph_name, {})
    node_prompt = graph.get(node_name, {})
    if not node_prompt:
        raise LLMClientError(f"missing prompt config: {graph_name}.{node_name}")
    return {
        "global_system_prompt": prompts.get("global_system_prompt", ""),
        **node_prompt,
    }


def active_provider_config() -> dict[str, Any]:
    config = load_model_api_config(mask_secrets=False)
    active = config.get("active_provider")
    provider = config.get("providers", {}).get(active or "", {})
    if not provider:
        raise LLMClientError("active model provider is not configured")
    return provider


def provider_config(provider_name: str) -> dict[str, Any]:
    config = load_model_api_config(mask_secrets=False)
    provider = config.get("providers", {}).get(provider_name or "", {})
    if not provider:
        raise LLMClientError(f"model provider '{provider_name}' is not configured")
    return provider


def call_llm_node(graph_name: str, node_name: str, values: dict[str, Any], provider_name: str = "") -> dict[str, Any]:
    prompt = get_node_prompt(graph_name, node_name)
    if provider_name:
        provider = provider_config(provider_name)
    else:
        provider = active_provider_config()
    base_url = str(provider.get("base_url") or "").rstrip("/")
    api_key = str(provider.get("api_key") or "")
    model = str(provider.get("chat_model") or "")
    if not base_url or not model:
        return standard_node_output(node_name, success=False, errors=["model base_url or chat_model is missing"])
    system_parts = [prompt_text(prompt.get("global_system_prompt")), prompt_text(prompt.get("system"))]
    user_content = render_template(prompt_text(prompt.get("user_template")), values)
    user_content = f"{user_content}\n\n标准输出格式：{json.dumps(load_prompts_config().get('standard_output_schema', {}), ensure_ascii=False)}\n节点输出 data 结构：{json.dumps(prompt.get('output_schema', {}), ensure_ascii=False)}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "\n".join(part for part in system_parts if part)},
            {"role": "user", "content": user_content},
        ],
        "temperature": float(provider.get("temperature", 0.2)),
        "max_tokens": int(provider.get("max_tokens", 1200)),
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(provider.get("timeout_seconds", 30))) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        return normalize_node_output(node_name, extract_json_payload(content))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        return standard_node_output(node_name, success=False, errors=[f"HTTP {exc.code}: {exc.reason} | {body[:500]}"])
    except (KeyError, IndexError, json.JSONDecodeError, urllib.error.URLError, TimeoutError, LLMClientError) as exc:
        return standard_node_output(node_name, success=False, errors=[str(exc)])


def test_model_connection() -> dict[str, Any]:
    return call_llm_node(
        "daily_report",
        "market_summary",
        {
            "market_data": {"symbol": "BTCUSDT", "close": 0},
            "technical_indicators": {},
            "funding_rates": {},
        },
    )
