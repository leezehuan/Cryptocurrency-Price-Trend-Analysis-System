"""Gate MCP 只读数据源客户端。

仅接入公开只读端点（/mcp、/mcp/info、/mcp/news），不接入交易、钱包或 OAuth 端点。
使用 Streamable HTTP 传输的 MCP JSON-RPC 2.0 协议调用远端工具。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .database import utc_now

logger = logging.getLogger(__name__)

# MCP 端点路径映射
MCP_ENDPOINTS = {
    "mcp": "/mcp",
    "mcp/info": "/mcp/info",
    "mcp/news": "/mcp/news",
}

class GateMCPClient:
    """轻量 MCP Streamable HTTP 客户端，只做工具调用，不做认证。"""

    def __init__(self, base_url: str = "https://api.gatemcp.ai", timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_id = 0
        self._session_ids: dict[str, str] = {}
        self._initialized: set[str] = set()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _endpoint_url(self, endpoint: str) -> str:
        path = MCP_ENDPOINTS.get(endpoint, f"/{endpoint}")
        return f"{self.base_url}{path}"

    def _post_jsonrpc(self, endpoint: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._endpoint_url(endpoint)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        session_id = self._session_ids.get(endpoint)
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            body["params"] = params

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=body, headers=headers)
            new_session_id = response.headers.get("mcp-session-id")
            if new_session_id:
                self._session_ids[endpoint] = new_session_id
            response.raise_for_status()
            # 处理 SSE 流式响应（取最后一个完整 JSON 行）
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return self._parse_sse_response(response.text)
            return response.json()

    @staticmethod
    def _parse_sse_response(text: str) -> dict[str, Any]:
        """从 SSE 流中提取最后一个 JSON-RPC 响应。"""
        last_data = ""
        for line in text.splitlines():
            if line.startswith("data:"):
                last_data = line[len("data:"):].strip()
        if last_data:
            return json.loads(last_data)
        return {"error": {"message": "empty SSE response"}}

    def _ensure_initialized(self, endpoint: str) -> None:
        if endpoint in self._initialized:
            return
        try:
            result = self._post_jsonrpc(endpoint, "initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "btc-agent", "version": "0.2.0"},
            })
            if "error" not in result:
                # 发送 initialized 通知（无需等待响应）
                try:
                    self._post_jsonrpc(endpoint, "notifications/initialized")
                except Exception:
                    pass
            self._initialized.add(endpoint)
        except Exception as exc:
            logger.warning("Gate MCP initialize failed for %s: %s", endpoint, exc)
            self._initialized.add(endpoint)

    def call_tool(self, endpoint: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用 MCP 工具并返回结果。"""
        self._ensure_initialized(endpoint)
        result = self._post_jsonrpc(endpoint, "tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        if "error" in result:
            raise RuntimeError(f"MCP tool error: {result['error']}")
        return result.get("result", {})

    def list_tools(self, endpoint: str = "mcp") -> list[dict[str, Any]]:
        self._ensure_initialized(endpoint)
        result = self._post_jsonrpc(endpoint, "tools/list")
        return result.get("result", {}).get("tools", [])

def record_mcp_call(
    conn: sqlite3.Connection,
    endpoint: str,
    tool_name: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    status: str,
    error_message: str | None,
    latency_ms: int,
) -> int:
    """将一次 MCP 调用记录写入 gate_mcp_raw_records。"""
    raw_text = json.dumps(response_payload, ensure_ascii=False, default=str)
    # 超长响应只保留前半段，避免数据库/日志爆炸，同时尽量保留 JSON 结构完整性。
    if len(raw_text) > 128000:
        truncated = raw_text[:128000]
        # 尝试截断到最近的 JSON 对象边界，保证可解析
        last_brace = truncated.rfind("}")
        if last_brace > 0:
            truncated = truncated[: last_brace + 1] + ']}]}'
        raw_text = truncated
    cursor = conn.execute(
        """
        INSERT INTO gate_mcp_raw_records (
            endpoint, tool_name, request_payload, response_payload,
            status, error_message, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            endpoint,
            tool_name,
            json.dumps(request_payload, ensure_ascii=False),
            raw_text,
            status,
            error_message,
            latency_ms,
            utc_now(),
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0

def safe_call_tool(
    client: GateMCPClient,
    conn: sqlite3.Connection,
    endpoint: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """带错误处理和原始记录的工具调用。"""
    args = arguments or {}
    start = time.monotonic()
    try:
        result = client.call_tool(endpoint, tool_name, args)
        latency = int((time.monotonic() - start) * 1000)
        record_mcp_call(conn, endpoint, tool_name, args, result, "success", None, latency)
        return result
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        record_mcp_call(conn, endpoint, tool_name, args, {"error": str(exc)}, "error", str(exc), latency)
        raise

def extract_text_content(result: dict[str, Any]) -> str:
    """从 MCP tool result 中提取文本内容。"""
    content = result.get("content", [])
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content)

def parse_json_from_content(result: dict[str, Any]) -> Any:
    """尝试从 MCP 工具返回的文本中解析 JSON。"""
    structured = result.get("structuredContent")
    if structured not in (None, {}, []):
        return structured
    content = result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            for key in ("json", "data", "structuredContent"):
                value = item.get(key)
                if value not in (None, "", {}, []):
                    return value
    text = extract_text_content(result).strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    for start_token, end_token in (("[", "]"), ("{", "}")):
        start = text.find(start_token)
        end = text.rfind(end_token)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, TypeError):
                continue
    return text

def _square_tool_names(client: GateMCPClient, endpoint: str) -> set[str]:
    try:
        return {str(tool.get("name")) for tool in client.list_tools(endpoint) if isinstance(tool, dict) and tool.get("name")}
    except Exception:
        return set()


def _square_tool_schema(client: GateMCPClient, endpoint: str, tool_name: str) -> dict[str, Any]:
    try:
        for tool in client.list_tools(endpoint):
            if isinstance(tool, dict) and tool.get("name") == tool_name:
                schema = tool.get("inputSchema") or tool.get("input_schema") or {}
                return schema if isinstance(schema, dict) else {}
    except Exception:
        return {}
    return {}


def _btc_contract_tool_candidates(client: GateMCPClient) -> list[tuple[str, str, dict[str, Any]]]:
    """发现可用于获取 BTC 合约行情的 MCP 工具候选列表。"""
    hardcoded = ("get_futures_tickers", "get_perpetual_tickers", "get_contract_tickers",
                 "get_futures_contracts", "get_tickers", "futures_ticker")
    mcp_names = _square_tool_names(client, "mcp")
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for name in hardcoded:
        if mcp_names and name not in mcp_names:
            continue
        candidates.append(("mcp", name, {"settle": "usdt", "contract": "BTC_USDT"}))
    # 动态发现：名字包含 futures/ticker/contract/perpetual 的工具
    if mcp_names:
        for name in mcp_names:
            if any(k in name.lower() for k in ("futures", "ticker", "contract", "perpetual")):
                args = {"settle": "usdt", "contract": "BTC_USDT"}
                if ("mcp", name, args) not in candidates:
                    candidates.append(("mcp", name, args))
    return candidates

def sync_gate_info(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP /mcp/info 获取 BTC 技术分析信息。"""
    if client is None:
        client = _default_client(conn)

    try:
        result = safe_call_tool(client, conn, "mcp/info", "get_crypto_technical_analysis", {
            "symbol": "BTC",
        })
    except Exception as exc:
        logger.warning("Gate Info sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    return {"synced": True, "source": "gate_mcp_info"}

def _default_client(conn: sqlite3.Connection) -> GateMCPClient:
    """从数据库 settings 读取配置创建客户端。"""
    from .services import get_setting_value

    base_url = str(get_setting_value(conn, "gate_mcp.base_url", "https://api.gatemcp.ai"))
    timeout = int(get_setting_value(conn, "gate_mcp.timeout_seconds", 30))
    return GateMCPClient(base_url=base_url, timeout=timeout)
