"""Phase B — Agent ReAct Tools。

每个 Tool 封装为可在 LangGraph Agent 节点中调用的函数，
接收 conn + 可选参数，返回 dict 结果。
Tool 不直接调用 LLM，只做数据查询/同步。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: GateMarketResearchTool — BTC 合约补充查询
# ---------------------------------------------------------------------------

def gate_market_research(conn: sqlite3.Connection | None, **kwargs: Any) -> dict[str, Any]:
    """查询最新 BTC 合约指标和近期 K 线摘要。"""
    tool_prompt = {
        "description": "查询最新 BTC 合约指标和 K 线摘要",
        "use_when": "需要确认 BTC 合约价格、资金费率、持仓量或多周期 K 线状态时使用。",
        "input_guidance": "无需额外参数；可传 symbol、intervals、limit 作为后续扩展。",
        "output_summary": "返回 contract_metrics 与 kline_summary。",
    }
    if kwargs.get("metadata_only"):
        return {"name": "gate_market_research", "prompt": tool_prompt}
    from .gate_mcp import latest_btc_contract_metrics

    metrics = latest_btc_contract_metrics(conn)
    if not metrics:
        return {"tool": "gate_market_research", "found": False, "reason": "no contract metrics available"}

    # 附加最近 K 线统计
    rows = conn.execute(
        """
        SELECT interval, COUNT(*) AS cnt, MAX(close) AS max_close, MIN(close) AS min_close,
               AVG(volume) AS avg_volume
        FROM market_data
        WHERE symbol = 'BTCUSDT' AND market_type = 'perpetual'
        GROUP BY interval
        ORDER BY interval
        """,
    ).fetchall()
    kline_summary = [{key: row[key] for key in row.keys()} for row in rows] if rows else []

    return {
        "tool": "gate_market_research",
        "found": True,
        "contract_metrics": metrics,
        "kline_summary": kline_summary,
    }


# ---------------------------------------------------------------------------
# Tool 2: GateInfoResearchTool — 技术面补充查询
# ---------------------------------------------------------------------------

def gate_info_research(conn: sqlite3.Connection | None, **kwargs: Any) -> dict[str, Any]:
    """查询最近的 Gate Info 技术分析原始记录。"""
    tool_prompt = {
        "description": "查询 Gate Info 技术分析原始记录",
        "use_when": "需要补充技术分析、链上/币种信息或第三方技术面上下文时使用。",
        "input_guidance": "无需额外参数；默认读取最近成功的 Gate Info 记录。",
        "output_summary": "返回最近 Gate Info raw records。",
    }
    if kwargs.get("metadata_only"):
        return {"name": "gate_info_research", "prompt": tool_prompt}
    rows = conn.execute(
        """
        SELECT response_payload, created_at
        FROM gate_mcp_raw_records
        WHERE endpoint = 'mcp/info' AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 3
        """,
    ).fetchall()
    if not rows:
        return {"tool": "gate_info_research", "found": False, "reason": "no gate info records"}

    records = []
    for row in rows:
        try:
            payload = json.loads(row["response_payload"]) if isinstance(row["response_payload"], str) else row["response_payload"]
        except (json.JSONDecodeError, TypeError):
            payload = row["response_payload"]
        records.append({"payload": payload, "created_at": row["created_at"]})

    return {"tool": "gate_info_research", "found": True, "records": records}


# ---------------------------------------------------------------------------
# Tool 3: GateNewsResearchTool — 新闻/公告补充查询
# ---------------------------------------------------------------------------

def gate_news_research(conn: sqlite3.Connection | None, **kwargs: Any) -> dict[str, Any]:
    """查询最近的 Gate 新闻原始记录。"""
    tool_prompt = {
        "description": "查询最近的 BTC、宏观和交易所相关新闻",
        "use_when": "需要补充新闻、宏观事件、公告或市场叙事证据时使用。",
        "input_guidance": "无需额外参数；默认读取最近成功的 Gate News 记录。",
        "output_summary": "返回最近新闻 raw records。",
    }
    if kwargs.get("metadata_only"):
        return {"name": "gate_news_research", "prompt": tool_prompt}
    rows = conn.execute(
        """
        SELECT response_payload, created_at
        FROM gate_mcp_raw_records
        WHERE endpoint = 'mcp/news' AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 5
        """,
    ).fetchall()
    if not rows:
        return {"tool": "gate_news_research", "found": False, "reason": "no news records"}

    records = []
    for row in rows:
        try:
            payload = json.loads(row["response_payload"]) if isinstance(row["response_payload"], str) else row["response_payload"]
        except (json.JSONDecodeError, TypeError):
            payload = row["response_payload"]
        records.append({"payload": payload, "created_at": row["created_at"]})

    return {"tool": "gate_news_research", "found": True, "record_count": len(records), "records": records}


# ---------------------------------------------------------------------------
# Tool 4: GateSquareResearchTool — 广场热门补充查询
# ---------------------------------------------------------------------------

def gate_square_research(conn: sqlite3.Connection | None, **kwargs: Any) -> dict[str, Any]:
    """查询最近的 Gate Square 热门和关注用户帖子。"""
    tool_prompt = {
        "description": "查询 Gate Square 热门和关注用户帖子",
        "use_when": "需要补充社交情绪、热门叙事或指定用户观点时使用。",
        "input_guidance": "可传 limit 作为后续扩展；默认读取最近热门和关注用户内容。",
        "output_summary": "返回 hot_posts 与 followed_user_posts。",
    }
    if kwargs.get("metadata_only"):
        return {"name": "gate_square_research", "prompt": tool_prompt}
    from .gate_mcp import recent_square_posts, recent_followed_user_posts

    hot_posts = recent_square_posts(conn, limit=10)
    followed_posts = recent_followed_user_posts(conn, limit=10)

    return {
        "tool": "gate_square_research",
        "found": bool(hot_posts or followed_posts),
        "hot_posts": hot_posts,
        "followed_user_posts": followed_posts,
    }


# ---------------------------------------------------------------------------
# Tool 5: MarketMemorySearchTool — 历史记忆检索
# ---------------------------------------------------------------------------

def market_memory_search(conn: sqlite3.Connection | None, **kwargs: Any) -> dict[str, Any]:
    """检索活跃的市场记忆。"""
    tool_prompt = {
        "description": "检索活跃的市场记忆",
        "use_when": "需要参考近期/长期情绪、趋势、事件或风险记忆时使用。",
        "input_guidance": "可传 symbol 和 limit；默认 BTCUSDT、20 条。",
        "output_summary": "返回 memories 与 memory_type 分布。",
    }
    if kwargs.get("metadata_only"):
        return {"name": "market_memory_search", "prompt": tool_prompt}
    from .gate_mcp import active_market_memories

    symbol = kwargs.get("symbol", "BTCUSDT")
    limit = int(kwargs.get("limit", 20))
    memories = active_market_memories(conn, symbol=symbol, limit=limit)

    if not memories:
        return {"tool": "market_memory_search", "found": False, "reason": "no active memories"}

    # 按 memory_type 分组摘要
    type_counts: dict[str, int] = {}
    for mem in memories:
        mt = mem.get("memory_type", "unknown")
        type_counts[mt] = type_counts.get(mt, 0) + 1

    return {
        "tool": "market_memory_search",
        "found": True,
        "count": len(memories),
        "type_distribution": type_counts,
        "memories": memories,
    }


# ---------------------------------------------------------------------------
# Tool 注册表 — Agent 节点通过 name 查找并调用
# ---------------------------------------------------------------------------

AGENT_TOOLS: dict[str, dict[str, Any]] = {
    "gate_market_research": {
        "fn": gate_market_research,
    },
    "gate_info_research": {
        "fn": gate_info_research,
    },
    "gate_news_research": {
        "fn": gate_news_research,
    },
    "gate_square_research": {
        "fn": gate_square_research,
    },
    "market_memory_search": {
        "fn": market_memory_search,
    },
}


def run_agent_tool(conn: sqlite3.Connection, tool_name: str, **kwargs: Any) -> dict[str, Any]:
    """按名称调用 Agent Tool，返回结果或错误。"""
    tool = AGENT_TOOLS.get(tool_name)
    if not tool:
        return {"tool": tool_name, "error": f"unknown tool: {tool_name}"}
    try:
        return tool["fn"](conn, **kwargs)
    except Exception as exc:
        logger.warning("Agent tool %s failed: %s", tool_name, exc)
        return {"tool": tool_name, "error": str(exc)}


def list_agent_tools() -> list[dict[str, Any]]:
    """返回所有可用 Tool 的名称和描述。"""
    tools: list[dict[str, Any]] = []
    for name, info in AGENT_TOOLS.items():
        metadata = info["fn"](None, metadata_only=True)
        prompt = metadata.get("prompt", {})
        tools.append({"name": name, "description": prompt.get("description", ""), "prompt": prompt})
    return tools
