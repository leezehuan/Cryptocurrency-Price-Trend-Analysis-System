"""将 backend/app/gate_mcp.py 拆分为 gate_mcp / gate_sync / content_sync 三个模块。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "backend" / "app"
SOURCE_FILE = BASE / "gate_mcp.py"

# 分类：每个函数/类归属的目标文件
TARGET_MAP = {
    # MCP 协议层 -> gate_mcp.py
    "GateMCPClient": "gate_mcp",
    "record_mcp_call": "gate_mcp",
    "safe_call_tool": "gate_mcp",
    "extract_text_content": "gate_mcp",
    "parse_json_from_content": "gate_mcp",
    "_btc_contract_tool_candidates": "gate_mcp",
    "_default_client": "gate_mcp",
    "sync_gate_info": "gate_mcp",

    # Gate REST 同步 + 市场数据 -> gate_sync.py
    "_float": "gate_sync",
    "_int": "gate_sync",
    "_write_btc_contract_metrics": "gate_sync",
    "sync_btc_contract_metrics": "gate_sync",
    "_btc_metrics_and_chained": "gate_sync",
    "sync_btc_contract_klines": "gate_sync",
    "sync_btc_funding_rate_history": "gate_sync",
    "sync_btc_contract_context": "gate_sync",
    "latest_btc_contract_metrics": "gate_sync",
    "latest_sentiment_snapshot": "gate_sync",
    "active_market_memories": "gate_sync",
    "recent_square_posts": "gate_sync",
    "recent_followed_user_posts": "gate_sync",
    "list_analyst_source_accounts": "gate_sync",
    "gate_mcp_source_status": "gate_sync",
    "build_market_sentiment_snapshot": "gate_sync",
    "_sentiment_to_memory_pipeline": "gate_sync",
    "compact_market_memories": "gate_sync",

    # Square / News 内容同步 -> content_sync.py
    "_first_value": "content_sync",
    "_nested_value": "content_sync",
    "_coerce_time": "content_sync",
    "_contains_chinese": "content_sync",
    "_square_hot_rank": "content_sync",
    "_square_preview": "content_sync",
    "_translate_posts_to_chinese": "content_sync",
    "_extract_square_items": "content_sync",
    "_square_text_post": "content_sync",
    "_normalize_square_post": "content_sync",
    "_normalize_square_posts": "content_sync",
    "_square_tool_names": "content_sync",
    "_square_tool_schema": "content_sync",
    "_square_search_arguments": "content_sync",
    "_square_hot_tool_candidates": "content_sync",
    "sync_gate_news": "content_sync",
    "sync_gate_square_hot": "content_sync",
    "sync_gate_square_user_opinions": "content_sync",
}


def extract_lines(source: str, start: int, end: int) -> str:
    lines = source.splitlines()
    return "\n".join(lines[start:end])


def build_file(source: str, tree: ast.Module, target: str, header: str, module_imports: str) -> str:
    parts: list[str] = [header.rstrip(), "", module_imports.strip()]

    # 收集属于 target 的顶层定义，并按源码顺序追加
    items = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            name = node.name
            if TARGET_MAP.get(name) == target:
                items.append(extract_lines(source, node.lineno - 1, node.end_lineno))

    if items:
        parts.append("")
        parts.append("\n\n".join(items))

    return "\n".join(parts) + "\n"


def main() -> int:
    if not SOURCE_FILE.exists():
        print(f"Missing source file: {SOURCE_FILE}")
        return 1

    source = SOURCE_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ---------- gate_mcp.py 头部保留（import + 常量）----------
    gate_mcp_header = '"""Gate MCP 只读数据源客户端。\n\n仅接入公开只读端点（/mcp、/mcp/info、/mcp/news），不接入交易、钱包或 OAuth 端点。\n使用 Streamable HTTP 传输的 MCP JSON-RPC 2.0 协议调用远端工具。\n"""'
    gate_mcp_imports = """from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx

from .database import utc_now

logger = logging.getLogger(__name__)

# MCP 端点路径映射
MCP_ENDPOINTS = {
    "mcp": "/mcp",
    "mcp/info": "/mcp/info",
    "mcp/news": "/mcp/news",
}"""

    # ---------- gate_sync.py 头部 ----------
    gate_sync_header = '"""Gate 数据源同步服务（REST API + 市场数据计算）。\n\n包含 BTC 合约行情/K线/资金费率同步、市场情绪快照、记忆压缩等。\n"""'
    gate_sync_imports = """from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .database import utc_now
from .gate_mcp import (
    GateMCPClient,
    _btc_contract_tool_candidates,
    parse_json_from_content,
    record_mcp_call,
    safe_call_tool,
)
from .gate_rest_client import GateRestClient

logger = logging.getLogger(__name__)"""

    # ---------- content_sync.py 头部 ----------
    content_sync_header = '"""内容源同步服务（Gate Square / Gate News）。\n\n负责热门帖子、关注用户帖子和新闻的同步与落库。\n"""'
    content_sync_imports = '''from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx

from .database import utc_now
from .gate_mcp import GateMCPClient, parse_json_from_content, safe_call_tool

logger = logging.getLogger(__name__)
SQUARE_SEARCH_QUERY = "BTC 比特币"
'''

    # 构建三个文件内容
    gate_mcp_body = build_file(source, tree, "gate_mcp", gate_mcp_header, gate_mcp_imports)
    gate_sync_body = build_file(source, tree, "gate_sync", gate_sync_header, gate_sync_imports)
    content_sync_body = build_file(source, tree, "content_sync", content_sync_header, content_sync_imports)

    # 写回
    (BASE / "gate_mcp.py").write_text(gate_mcp_body, encoding="utf-8")
    (BASE / "gate_sync.py").write_text(gate_sync_body, encoding="utf-8")
    (BASE / "content_sync.py").write_text(content_sync_body, encoding="utf-8")

    print("Split completed:")
    print(f"  gate_mcp.py  -> {len(gate_mcp_body.splitlines())} lines")
    print(f"  gate_sync.py -> {len(gate_sync_body.splitlines())} lines")
    print(f"  content_sync.py -> {len(content_sync_body.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
