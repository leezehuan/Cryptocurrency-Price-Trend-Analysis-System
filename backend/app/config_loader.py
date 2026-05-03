from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
MODEL_API_CONFIG = CONFIG_DIR / "model_api.json"
MODEL_API_LOCAL_CONFIG = CONFIG_DIR / "model_api.local.json"
PROMPTS_CONFIG = CONFIG_DIR / "prompts.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def load_model_api_config(mask_secrets: bool = True) -> dict[str, Any]:
    config = deep_merge(read_json(MODEL_API_CONFIG), read_json(MODEL_API_LOCAL_CONFIG))
    providers = config.get("providers", {})
    for provider in providers.values():
        api_key_env = provider.get("api_key_env")
        env_key = os.getenv(api_key_env, "") if api_key_env else ""
        if env_key:
            provider["api_key"] = env_key
        if mask_secrets:
            provider["api_key"] = mask_secret(provider.get("api_key"))
    return config


def load_prompts_config() -> dict[str, Any]:
    return read_json(PROMPTS_CONFIG)


def load_runtime_config() -> dict[str, Any]:
    return {
        "model_api": load_model_api_config(mask_secrets=True),
        "prompts": load_prompts_config(),
    }
