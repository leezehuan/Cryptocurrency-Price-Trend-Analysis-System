from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

# 以项目根目录为基准定位 config 配置目录。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
MODEL_API_CONFIG = CONFIG_DIR / "model_api.json"
MODEL_API_LOCAL_CONFIG = CONFIG_DIR / "model_api.local.json"
PROMPTS_CONFIG = CONFIG_DIR / "prompts.json"


def read_json(path: Path) -> dict[str, Any]:
    # 配置文件不存在时返回空字典，方便与默认配置合并。
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    # 递归合并配置，让本地配置只覆盖需要变更的字段。
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def mask_secret(value: str | None) -> str:
    # 对外展示配置时隐藏 API Key 等敏感信息。
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def load_model_api_config(mask_secrets: bool = True) -> dict[str, Any]:
    # 合并默认模型配置和本地私有配置，并优先读取环境变量中的 API Key。
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
    # 读取所有 Graph 节点使用的提示词配置。
    return read_json(PROMPTS_CONFIG)


def load_runtime_config() -> dict[str, Any]:
    # 返回前端设置页需要展示的完整运行时配置。
    return {
        "model_api": load_model_api_config(mask_secrets=True),
        "prompts": load_prompts_config(),
    }
