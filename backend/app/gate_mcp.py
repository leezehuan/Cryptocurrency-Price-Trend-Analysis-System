"""Gate MCP 只读数据源客户端与同步服务。

仅接入公开只读端点（/mcp、/mcp/info、/mcp/news），不接入交易、钱包或 OAuth 端点。
使用 Streamable HTTP 传输的 MCP JSON-RPC 2.0 协议调用远端工具。
"""
from __future__ import annotations

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
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

# MCP 端点路径映射
MCP_ENDPOINTS = {
    "mcp": "/mcp",
    "mcp/info": "/mcp/info",
    "mcp/news": "/mcp/news",
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC 客户端
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 原始记录持久化
# ---------------------------------------------------------------------------

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
            json.dumps(response_payload, ensure_ascii=False, default=str)[:32000],
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


# ---------------------------------------------------------------------------
# MCP 工具结果解析辅助
# ---------------------------------------------------------------------------

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
    text = extract_text_content(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


# ---------------------------------------------------------------------------
# BTC 合约数据同步
# ---------------------------------------------------------------------------

def sync_btc_contract_metrics(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP 获取 BTC 合约行情并写入 btc_contract_metrics。"""
    if client is None:
        client = _default_client(conn)

    result = safe_call_tool(client, conn, "mcp", "get_futures_tickers", {
        "settle": "usdt",
        "contract": "BTC_USDT",
    })
    data = parse_json_from_content(result)

    # Gate futures tickers 返回数组
    ticker = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    if not ticker:
        return {"synced": False, "error": "empty ticker response"}

    now = utc_now()
    fetched_at = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()

    conn.execute(
        """
        INSERT INTO btc_contract_metrics (
            symbol, last_price, mark_price, index_price,
            funding_rate, funding_rate_indicative,
            volume_24h, open_interest, high_24h, low_24h, change_pct_24h,
            source, fetched_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, fetched_at) DO UPDATE SET
            last_price = excluded.last_price,
            mark_price = excluded.mark_price,
            funding_rate = excluded.funding_rate,
            volume_24h = excluded.volume_24h,
            created_at = excluded.created_at
        """,
        (
            "BTC_USDT",
            _float(ticker.get("last")),
            _float(ticker.get("mark_price")),
            _float(ticker.get("index_price")),
            _float(ticker.get("funding_rate")),
            _float(ticker.get("funding_rate_indicative")),
            _float(ticker.get("volume_24h") or ticker.get("volume_24h_quote")),
            _float(ticker.get("quanto_base_rate") or ticker.get("open_interest")),
            _float(ticker.get("high_24h")),
            _float(ticker.get("low_24h")),
            _float(ticker.get("change_percentage")),
            "gate_mcp",
            fetched_at,
            now,
        ),
    )
    conn.commit()

    # A2: 串联 K 线和资金费率历史同步（失败不影响 ticker 结果）
    kline_result: dict[str, Any] = {}
    funding_result: dict[str, Any] = {}
    context_result: dict[str, Any] = {}
    try:
        kline_result = sync_btc_contract_klines(conn, client)
    except Exception as exc:
        kline_result = {"synced": False, "error": str(exc)}
    try:
        funding_result = sync_btc_funding_rate_history(conn, client)
    except Exception as exc:
        funding_result = {"synced": False, "error": str(exc)}
    try:
        context_result = sync_btc_contract_context(conn, client)
    except Exception as exc:
        context_result = {"synced": False, "error": str(exc)}

    return {
        "synced": True,
        "symbol": "BTC_USDT",
        "last_price": _float(ticker.get("last")),
        "funding_rate": _float(ticker.get("funding_rate")),
        "fetched_at": fetched_at,
        "klines": kline_result,
        "funding_history": funding_result,
        "context": context_result,
    }


# ---------------------------------------------------------------------------
# Gate News 同步
# ---------------------------------------------------------------------------

def sync_gate_news(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP /mcp/news 拉取配置关键词相关新闻，存入 gate_mcp_raw_records 作为原始记录。"""
    from .services import get_setting_value

    if client is None:
        client = _default_client(conn)

    keywords = get_setting_value(conn, "news.keywords", ["BTC", "Bitcoin", "Nasdaq", "FOMC", "CPI", "Fed"])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = [keywords]
    keyword_str = " ".join(keywords[:6]) if keywords else "BTC Bitcoin"

    try:
        result = safe_call_tool(client, conn, "mcp/news", "search_news", {
            "keyword": keyword_str,
            "limit": 20,
        })
    except Exception as exc:
        logger.warning("Gate News sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    return {"synced": True, "source": "gate_mcp_news", "keywords": keyword_str}


def sync_nasdaq_data(conn: sqlite3.Connection) -> dict[str, Any]:
    from .services import get_setting_value

    symbols = get_setting_value(conn, "nasdaq.symbols", ["IXIC", "NDX", "QQQ", "NQ"])
    if isinstance(symbols, str):
        try:
            symbols = json.loads(symbols)
        except (json.JSONDecodeError, TypeError):
            symbols = [item.strip() for item in symbols.split(",") if item.strip()]
    symbol_map = {"IXIC": "^IXIC", "NDX": "^NDX", "QQQ": "QQQ", "NQ": "NQ=F"}
    requested = [str(item).upper() for item in symbols if str(item).strip()]
    yahoo_symbols = ",".join(symbol_map.get(item, item) for item in requested)
    if not yahoo_symbols:
        return {"synced": False, "reason": "no nasdaq symbols configured"}
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(YAHOO_QUOTE_URL, params={"symbols": yahoo_symbols})
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Nasdaq data sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    rows = ((payload.get("quoteResponse") or {}).get("result") or [])
    now = utc_now()
    fetched_at = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
    synced = 0
    reverse_map = {value: key for key, value in symbol_map.items()}
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_symbol = str(item.get("symbol") or "")
        symbol = reverse_map.get(raw_symbol, raw_symbol.replace("^", ""))
        price = _float(item.get("regularMarketPrice"))
        if not symbol or price is None:
            continue
        conn.execute(
            """
            INSERT INTO nasdaq_market_data (
                symbol, price, open, high, low, close, volume,
                change_pct, source, market_session, payload, fetched_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'yahoo_finance', ?, ?, ?, ?)
            ON CONFLICT(symbol, fetched_at) DO UPDATE SET
                price = excluded.price,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                change_pct = excluded.change_pct,
                market_session = excluded.market_session,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                symbol,
                price,
                _float(item.get("regularMarketOpen")),
                _float(item.get("regularMarketDayHigh")),
                _float(item.get("regularMarketDayLow")),
                price,
                _float(item.get("regularMarketVolume")),
                _float(item.get("regularMarketChangePercent")),
                item.get("marketState"),
                json.dumps(item, ensure_ascii=False, default=str),
                fetched_at,
                now,
            ),
        )
        synced += 1
    conn.commit()
    return {"synced": True, "count": synced, "symbols": requested, "fetched_at": fetched_at}


# ---------------------------------------------------------------------------
# Gate Square 热门同步
# ---------------------------------------------------------------------------

def sync_gate_square_hot(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP /mcp/info 拉取 Square 热门帖子。"""
    if client is None:
        client = _default_client(conn)

    try:
        result = safe_call_tool(client, conn, "mcp/info", "get_square_hot", {
            "limit": 20,
        })
    except Exception as exc:
        logger.warning("Gate Square sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    data = parse_json_from_content(result)
    posts = data if isinstance(data, list) else []
    now = utc_now()
    synced = 0

    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or post.get("post_id") or "")
        if not post_id:
            continue
        content = str(post.get("content") or post.get("text") or "")
        if not content:
            continue
        try:
            conn.execute(
                """
                INSERT INTO gate_square_posts (
                    post_id, author, author_id, content, publish_time,
                    likes, comments, repost_count,
                    sentiment, tags, source, fetched_at, created_at,
                    hot_score, is_followed_user, is_hot_post
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
                ON CONFLICT(post_id) DO UPDATE SET
                    likes = excluded.likes,
                    comments = excluded.comments,
                    repost_count = excluded.repost_count,
                    hot_score = excluded.hot_score,
                    fetched_at = excluded.fetched_at,
                    is_hot_post = 1
                """,
                (
                    post_id,
                    str(post.get("author") or post.get("user_name") or ""),
                    str(post.get("author_id") or post.get("user_id") or ""),
                    content[:4000],
                    post.get("publish_time") or post.get("created_at"),
                    int(post.get("likes") or post.get("like_count") or 0),
                    int(post.get("comments") or post.get("comment_count") or 0),
                    int(post.get("reposts") or post.get("repost_count") or 0),
                    None,
                    json.dumps(post.get("tags") or [], ensure_ascii=False),
                    "gate_square",
                    now,
                    now,
                    float(post.get("hot_score") or post.get("score") or 0),
                ),
            )
            synced += 1
        except Exception:
            continue
    conn.commit()
    return {"synced": True, "count": synced}


# ---------------------------------------------------------------------------
# 市场情绪快照构建
# ---------------------------------------------------------------------------

def build_market_sentiment_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """基于最新 BTC 合约指标和数据库资讯，生成市场情绪快照。"""
    now = utc_now()
    snapshot_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()

    # 读取最新合约指标
    row = conn.execute(
        "SELECT * FROM btc_contract_metrics WHERE symbol = 'BTC_USDT' ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    metrics = dict(row) if row else {} if not row else {key: row[key] for key in row.keys()}

    funding_rate = float(metrics.get("funding_rate") or 0) if metrics else None
    change_pct = float(metrics.get("change_pct_24h") or 0) if metrics else None
    square_rows = conn.execute(
        """
        SELECT post_id, author, content, hot_score, likes, comments, sentiment, publish_time
        FROM gate_square_posts
        ORDER BY is_hot_post DESC, hot_score DESC, publish_time DESC, created_at DESC
        LIMIT 20
        """
    ).fetchall()
    square_posts = [{key: row[key] for key in row.keys()} for row in square_rows]
    news_rows = conn.execute(
        """
        SELECT tool_name, response_payload, created_at
        FROM gate_mcp_raw_records
        WHERE endpoint = 'mcp/news' AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 5
        """
    ).fetchall()
    news_summary = [{key: row[key] for key in row.keys()} for row in news_rows]
    nasdaq_rows = conn.execute(
        "SELECT * FROM nasdaq_market_data ORDER BY fetched_at DESC LIMIT 8"
    ).fetchall()
    nasdaq_context = [{key: row[key] for key in row.keys()} for row in nasdaq_rows]
    memory_rows = conn.execute(
        """
        SELECT memory_type, title, content, sentiment, importance, created_at
        FROM market_memories
        WHERE is_active = 1
        ORDER BY importance DESC, created_at DESC
        LIMIT 10
        """
    ).fetchall()
    memory_context = [{key: row[key] for key in row.keys()} for row in memory_rows]

    # 简易情绪推断
    overall = "neutral"
    if funding_rate is not None and change_pct is not None:
        score = 0.0
        if funding_rate > 0.0003:
            score += 1
        elif funding_rate < -0.0003:
            score -= 1
        if change_pct > 3:
            score += 1.5
        elif change_pct < -3:
            score -= 1.5
        if score >= 2:
            overall = "extreme_greed"
        elif score >= 1:
            overall = "greed"
        elif score <= -2:
            overall = "extreme_fear"
        elif score <= -1:
            overall = "fear"

    bull_ratio = round(max(0, min(1, 0.5 + (change_pct or 0) / 20)), 3) if change_pct is not None else None
    bear_ratio = round(1 - bull_ratio, 3) if bull_ratio is not None else None
    confidence = "medium" if metrics else "low"
    dominant_topics: list[str] = []
    crowd_positioning = "unknown"
    evidence_refs = [f"btc_contract_metrics:{metrics.get('id')}"] if metrics.get("id") else []
    payload = {
        "contract_metrics": metrics,
        "square_posts": square_posts,
        "news_summary": news_summary,
        "nasdaq_context": nasdaq_context,
        "memory_context": memory_context,
        "rule_based": True,
    }
    try:
        from .llm_client import call_llm_node
        llm_result = call_llm_node("skills", "market_sentiment_analysis", {
            "square_posts": square_posts,
            "news_summary": news_summary,
            "market_indicators": {
                "btc_contract": metrics,
                "nasdaq": nasdaq_context,
                "memories": memory_context,
            },
        })
        if llm_result.get("success"):
            data = llm_result.get("data", {})
            overall = str(data.get("overall_sentiment") or overall)
            bull_ratio = _float(data.get("bull_ratio")) if data.get("bull_ratio") is not None else bull_ratio
            bear_ratio = _float(data.get("bear_ratio")) if data.get("bear_ratio") is not None else bear_ratio
            confidence = str(data.get("confidence") or confidence)
            dominant_topics = list(data.get("dominant_topics") or [])
            crowd_positioning = str(data.get("crowd_positioning") or crowd_positioning)
            payload["llm_sentiment"] = data
            payload["rule_based"] = False
    except Exception as exc:
        payload["llm_error"] = str(exc)

    conn.execute(
        """
        INSERT INTO market_sentiment_snapshots (
            symbol, bull_ratio, bear_ratio, fear_greed_index,
            funding_rate, open_interest_change_pct,
            news_sentiment_score, square_sentiment_score,
            overall_sentiment, target, source_window, confidence,
            dominant_topics, crowd_positioning, evidence_refs,
            payload, snapshot_time, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, snapshot_time) DO UPDATE SET
            bull_ratio = excluded.bull_ratio,
            bear_ratio = excluded.bear_ratio,
            overall_sentiment = excluded.overall_sentiment,
            funding_rate = excluded.funding_rate,
            confidence = excluded.confidence,
            dominant_topics = excluded.dominant_topics,
            crowd_positioning = excluded.crowd_positioning,
            evidence_refs = excluded.evidence_refs,
            payload = excluded.payload,
            created_at = excluded.created_at
        """,
        (
            "BTCUSDT",
            bull_ratio,
            bear_ratio,
            None,
            funding_rate,
            None,
            None,
            None,
            overall,
            "BTC",
            "latest_24h",
            confidence,
            json.dumps(dominant_topics, ensure_ascii=False),
            crowd_positioning,
            json.dumps(evidence_refs, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False, default=str),
            snapshot_time,
            now,
        ),
    )
    conn.commit()

    # B6: 情绪→记忆写入管线 — 高置信度情绪写入 market_memories
    try:
        memory_result = _sentiment_to_memory_pipeline(conn, overall, bull_ratio, bear_ratio, funding_rate, snapshot_time)
    except Exception as exc:
        logger.warning("Sentiment to memory pipeline failed: %s", exc)
        memory_result = {"written": False, "error": str(exc)}

    return {
        "symbol": "BTCUSDT",
        "overall_sentiment": overall,
        "bull_ratio": bull_ratio,
        "bear_ratio": bear_ratio,
        "funding_rate": funding_rate,
        "confidence": confidence,
        "dominant_topics": dominant_topics,
        "snapshot_time": snapshot_time,
        "memory_pipeline": memory_result,
    }


def _sentiment_to_memory_pipeline(
    conn: sqlite3.Connection,
    overall: str,
    bull_ratio: float | None,
    bear_ratio: float | None,
    funding_rate: float | None,
    snapshot_time: str,
) -> dict[str, Any]:
    """当情绪信号强烈时，将其压缩为市场记忆写入 market_memories。

    写入条件：
    - 情绪偏离中性（extreme_greed / extreme_fear / greed / fear）
    - bull_ratio > 0.7 或 < 0.3（多源共识明显）
    """
    now = utc_now()
    # 判断是否满足写入条件
    strong_signal = overall in ("extreme_greed", "extreme_fear", "greed", "fear")
    consensus = (bull_ratio is not None and (bull_ratio > 0.7 or bull_ratio < 0.3))
    if not strong_signal and not consensus:
        return {"written": False, "reason": "neutral sentiment, skipped"}

    # 先尝试 LLM Skill 压缩
    sentiment_label_map = {
        "extreme_greed": "极度贪婪",
        "greed": "贪婪",
        "extreme_fear": "极度恐惧",
        "fear": "恐惧",
        "neutral": "中性",
    }
    sentiment_cn = sentiment_label_map.get(overall, overall)

    # 收集最新热门帖子作为 source_items
    hot_rows = conn.execute(
        "SELECT content FROM gate_square_posts WHERE is_hot_post = 1 ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    source_items = [row["content"][:200] for row in hot_rows] if hot_rows else []

    title = f"市场情绪信号：{sentiment_cn}"
    content = f"情绪快照 {snapshot_time} — 整体情绪 {sentiment_cn}"
    if bull_ratio is not None:
        content += f"，看涨比例 {bull_ratio}"
    if funding_rate is not None:
        content += f"，资金费率 {funding_rate}"

    importance = 0.7 if "extreme" in overall else 0.5
    sentiment_dir = "bullish" if overall in ("greed", "extreme_greed") else "bearish" if overall in ("fear", "extreme_fear") else "neutral"

    # 尝试 LLM Skill（可选，失败不阻塞）
    try:
        from .llm_client import call_llm_node
        llm_result = call_llm_node("skills", "memory_summarization", {
            "source_items": source_items or [content],
            "market_state": {"overall_sentiment": overall, "bull_ratio": bull_ratio, "funding_rate": funding_rate},
        })
        if llm_result.get("success") and llm_result.get("data", {}).get("title"):
            d = llm_result["data"]
            title = str(d.get("title", title))[:60]
            content = str(d.get("content", content))[:400]
            importance = float(d.get("importance", importance))
            sentiment_dir = str(d.get("sentiment", sentiment_dir))
    except Exception:
        pass  # LLM 不可用时使用规则生成

    # 写入 market_memories
    conn.execute(
        """
        INSERT INTO market_memories (
            memory_type, symbol, title, content, importance,
            sentiment, expectation, decay_policy, evidence_refs, source_types,
            valid_from, valid_until, is_active, source, payload,
            created_at, updated_at
        ) VALUES (?, 'BTCUSDT', ?, ?, ?, ?, ?, 'default', ?, ?, ?, ?, 1, 'sentiment_pipeline', '{}', ?, ?)
        """,
        (
            "market_sentiment_memory",
            title[:120],
            content[:1000],
            importance,
            sentiment_dir,
            f"{sentiment_cn}走势预期",
            json.dumps(source_items[:5], ensure_ascii=False),
            json.dumps(["gate_square", "btc_contract_metrics"], ensure_ascii=False),
            snapshot_time,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    return {"written": True, "title": title, "sentiment": sentiment_dir, "importance": importance}


# ---------------------------------------------------------------------------
# 市场记忆压缩
# ---------------------------------------------------------------------------

def compact_market_memories(conn: sqlite3.Connection) -> dict[str, Any]:
    """将过期记忆标记为非活跃，并对活跃记忆应用时间衰减降低 importance。"""
    import math

    now_str = utc_now()
    now_dt = datetime.now(timezone.utc)

    # 1. 硬过期：valid_until 已过期的直接停用
    conn.execute(
        """
        UPDATE market_memories
        SET is_active = 0, updated_at = ?
        WHERE is_active = 1 AND valid_until IS NOT NULL AND valid_until < ?
        """,
        (now_str, now_str),
    )

    # 2. 衰减：对活跃记忆根据 age 衰减 importance
    # half_life 映射（小时）
    half_life_map = {
        "market_sentiment_memory": 6,
        "btc_trend_memory": 24,
        "btc_contract_memory": 12,
        "nasdaq_trend_memory": 24,
        "event_memory": 72,
        "risk_memory": 24,
    }
    default_half_life = 48  # 小时

    rows = conn.execute(
        "SELECT id, memory_type, importance, created_at FROM market_memories WHERE is_active = 1"
    ).fetchall()

    decayed = 0
    deactivated_by_decay = 0
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = max(0, (now_dt - created).total_seconds() / 3600)
            half_life = half_life_map.get(row["memory_type"], default_half_life)
            original_importance = float(row["importance"])
            effective = original_importance * math.exp(-age_hours * math.log(2) / half_life)
            effective = round(effective, 4)
            if effective < 0.05:
                conn.execute(
                    "UPDATE market_memories SET is_active = 0, importance = ?, updated_at = ? WHERE id = ?",
                    (effective, now_str, row["id"]),
                )
                deactivated_by_decay += 1
            elif abs(effective - original_importance) > 0.001:
                conn.execute(
                    "UPDATE market_memories SET importance = ?, updated_at = ? WHERE id = ?",
                    (effective, now_str, row["id"]),
                )
                decayed += 1
        except Exception:
            continue

    conn.commit()
    return {"deactivated_expired": 0, "deactivated_by_decay": deactivated_by_decay, "importance_decayed": decayed}


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------

def latest_btc_contract_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """返回最新一条 BTC 合约指标。"""
    row = conn.execute(
        "SELECT * FROM btc_contract_metrics WHERE symbol = 'BTC_USDT' ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {}
    return {key: row[key] for key in row.keys()}


def latest_sentiment_snapshot(conn: sqlite3.Connection, symbol: str = "BTCUSDT") -> dict[str, Any]:
    """返回最新一条市场情绪快照。"""
    row = conn.execute(
        "SELECT * FROM market_sentiment_snapshots WHERE symbol = ? ORDER BY snapshot_time DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not row:
        return {}
    return {key: row[key] for key in row.keys()}


def active_market_memories(conn: sqlite3.Connection, symbol: str = "BTCUSDT", limit: int = 20) -> list[dict[str, Any]]:
    """返回活跃的市场记忆。"""
    rows = conn.execute(
        """
        SELECT * FROM market_memories
        WHERE is_active = 1 AND symbol = ?
        ORDER BY importance DESC, updated_at DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def recent_square_posts(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """返回最近的 Gate Square 帖子。"""
    rows = conn.execute(
        "SELECT * FROM gate_square_posts ORDER BY publish_time DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


# ---------------------------------------------------------------------------
# A1: 指定用户观点采集
# ---------------------------------------------------------------------------

def sync_gate_square_user_opinions(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate Square 读取指定用户的新帖子，作为分析师观点进入解析管线。"""
    from .services import create_opinion, get_or_create_analyst, get_setting_value

    if client is None:
        client = _default_client(conn)

    followed_users = get_setting_value(conn, "square.followed_users", [])
    if isinstance(followed_users, str):
        try:
            followed_users = json.loads(followed_users)
        except (json.JSONDecodeError, TypeError):
            followed_users = []
    if not followed_users:
        return {"synced": False, "reason": "no followed users configured"}

    total_synced = 0
    total_opinions = 0
    total_reviews = 0
    now = utc_now()

    for user_cfg in followed_users:
        if not isinstance(user_cfg, dict):
            continue
        source_user_id = str(user_cfg.get("source_user_id") or "")
        display_name = str(user_cfg.get("display_name") or source_user_id)
        if not source_user_id:
            continue

        analyst = get_or_create_analyst(conn, display_name, "gate_square_user")
        conn.execute(
            """
            INSERT INTO analyst_source_accounts (
                analyst_id, source_platform, source_user_id, display_name, enabled, created_at
            ) VALUES (?, 'gate_square', ?, ?, 1, ?)
            ON CONFLICT(source_platform, source_user_id) DO UPDATE SET
                analyst_id = excluded.analyst_id,
                display_name = excluded.display_name,
                enabled = 1
            """,
            (analyst["id"], source_user_id, display_name, now),
        )
        conn.commit()

        # 拉取该用户的帖子
        try:
            result = safe_call_tool(client, conn, "mcp", "get_square_user_posts", {
                "user_id": source_user_id,
                "limit": 10,
            })
        except Exception as exc:
            logger.warning("Gate Square user sync failed for %s: %s", source_user_id, exc)
            continue

        data = parse_json_from_content(result)
        posts = data if isinstance(data, list) else []

        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("id") or post.get("post_id") or "")
            if not post_id:
                continue
            content = str(post.get("content") or post.get("text") or "")
            if not content or len(content.strip()) < 10:
                continue

            # 写入 gate_square_posts（标记为关注用户）
            try:
                conn.execute(
                    """
                    INSERT INTO gate_square_posts (
                        post_id, author, author_id, content, publish_time,
                        likes, comments, repost_count, sentiment, tags,
                        source, fetched_at, created_at,
                        is_followed_user, is_hot_post, hot_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0)
                    ON CONFLICT(post_id) DO UPDATE SET
                        likes = excluded.likes,
                        comments = excluded.comments,
                        fetched_at = excluded.fetched_at,
                        is_followed_user = 1
                    """,
                    (
                        post_id,
                        display_name,
                        source_user_id,
                        content[:4000],
                        post.get("publish_time") or post.get("created_at"),
                        int(post.get("likes") or post.get("like_count") or 0),
                        int(post.get("comments") or post.get("comment_count") or 0),
                        int(post.get("reposts") or post.get("repost_count") or 0),
                        None,
                        json.dumps(post.get("tags") or [], ensure_ascii=False),
                        "gate_square_user",
                        now,
                        now,
                    ),
                )
                total_synced += 1
            except Exception:
                continue

            source_url = f"gate_square://{source_user_id}/{post_id}"
            already = conn.execute(
                "SELECT id FROM raw_opinions WHERE source_url = ?",
                (source_url,),
            ).fetchone()
            if already:
                continue
            pending_review = conn.execute(
                "SELECT id FROM opinion_review_drafts WHERE source_url = ?",
                (source_url,),
            ).fetchone()
            if pending_review:
                continue
            payload = SimpleNamespace(
                analyst_name=display_name,
                content=content[:4000],
                source_url=source_url,
                published_at=post.get("publish_time") or post.get("created_at") or now,
            )
            try:
                result = create_opinion(conn, payload)
                total_opinions += len(result.get("prediction_ids") or [])
                if result.get("needs_user_confirmation"):
                    total_reviews += 1
            except Exception as exc:
                logger.warning("Gate Square opinion ingestion failed for %s: %s", source_url, exc)
                continue

        conn.commit()

    return {"synced": True, "posts": total_synced, "predictions_created": total_opinions, "reviews_created": total_reviews}


# ---------------------------------------------------------------------------
# A2: BTC 合约 K 线同步
# ---------------------------------------------------------------------------

def sync_btc_contract_klines(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP 拉取 BTC 合约 K 线写入 market_data 表。"""
    if client is None:
        client = _default_client(conn)

    from .database import utc_now as _utc_now
    from .services import get_setting_value

    configured_intervals = get_setting_value(conn, "market.intervals", ["1m", "5m", "15m", "1h", "4h", "1d"])
    if isinstance(configured_intervals, str):
        try:
            configured_intervals = json.loads(configured_intervals)
        except (json.JSONDecodeError, TypeError):
            configured_intervals = [item.strip() for item in configured_intervals.split(",") if item.strip()]
    intervals_map = {"1m": "60", "5m": "300", "15m": "900", "1h": "3600", "4h": "14400", "1d": "86400"}
    intervals = [str(item) for item in configured_intervals if str(item) in intervals_map]
    if not intervals:
        intervals = ["1h", "4h", "1d"]
    now = _utc_now()
    total = 0
    errors: dict[str, str] = {}

    for interval in intervals:
        gate_interval = intervals_map[interval]
        try:
            result = safe_call_tool(client, conn, "mcp", "get_futures_candlesticks", {
                "settle": "usdt",
                "contract": "BTC_USDT",
                "interval": gate_interval,
                "limit": 50,
            })
        except Exception as exc:
            logger.warning("BTC kline sync failed for %s: %s", interval, exc)
            errors[interval] = str(exc)
            continue

        data = parse_json_from_content(result)
        klines = data if isinstance(data, list) else []

        for kline in klines:
            if not isinstance(kline, dict):
                continue
            try:
                open_time = kline.get("t") or kline.get("time") or kline.get("timestamp")
                if open_time and isinstance(open_time, (int, float)):
                    open_time = datetime.fromtimestamp(float(open_time), tz=timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO market_data (
                        symbol, market_type, interval, open_time,
                        open, high, low, close, volume, funding_rate, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, market_type, interval, open_time) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        created_at = excluded.created_at
                    """,
                    (
                        "BTCUSDT", "perpetual", interval,
                        open_time,
                        _float(kline.get("o") or kline.get("open")),
                        _float(kline.get("h") or kline.get("high")),
                        _float(kline.get("l") or kline.get("low")),
                        _float(kline.get("c") or kline.get("close")),
                        _float(kline.get("v") or kline.get("volume")),
                        0.0,
                        now,
                    ),
                )
                total += 1
            except Exception:
                continue

    conn.commit()
    return {"synced": True, "klines_inserted": total, "intervals": intervals, "errors": errors}


def sync_btc_funding_rate_history(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP 拉取 BTC 资金费率历史。"""
    if client is None:
        client = _default_client(conn)

    try:
        result = safe_call_tool(client, conn, "mcp", "get_futures_funding_rate", {
            "settle": "usdt",
            "contract": "BTC_USDT",
            "limit": 20,
        })
    except Exception as exc:
        logger.warning("BTC funding rate history sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    data = parse_json_from_content(result)
    rates = data if isinstance(data, list) else []
    now = utc_now()
    synced = 0

    for item in rates:
        if not isinstance(item, dict):
            continue
        ts = item.get("t") or item.get("time") or item.get("timestamp")
        if ts and isinstance(ts, (int, float)):
            fetched_at = datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(microsecond=0).isoformat()
        else:
            fetched_at = str(ts or now)
        funding = _float(item.get("r") or item.get("rate") or item.get("funding_rate"))
        if funding is None:
            continue
        try:
            conn.execute(
                """
                INSERT INTO btc_contract_metrics (
                    symbol, last_price, funding_rate, source, fetched_at, created_at
                ) VALUES ('BTC_USDT', 0, ?, 'gate_mcp_funding', ?, ?)
                ON CONFLICT(symbol, fetched_at) DO UPDATE SET
                    funding_rate = excluded.funding_rate
                """,
                (funding, fetched_at, now),
            )
            synced += 1
        except Exception:
            continue

    conn.commit()
    return {"synced": True, "funding_rates_inserted": synced}


def sync_btc_contract_context(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    if client is None:
        client = _default_client(conn)
    try:
        tools = client.list_tools("mcp")
    except Exception as exc:
        logger.warning("BTC contract context tools/list failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    keyword_groups = {
        "depth": ("order_book", "depth"),
        "trades": ("trade", "trades"),
        "premium": ("premium",),
        "liquidation": ("liquidation", "liq"),
        "stats": ("stats", "statistics", "open_interest"),
    }
    selected: dict[str, str] = {}
    for tool in tools:
        name = str(tool.get("name") or "")
        lower = name.lower()
        if "future" not in lower and "futures" not in lower and "fx" not in lower:
            continue
        for group, keywords in keyword_groups.items():
            if group not in selected and any(keyword in lower for keyword in keywords):
                selected[group] = name
    called: dict[str, str] = {}
    errors: dict[str, str] = {}
    for group, tool_name in selected.items():
        args = {"settle": "usdt", "contract": "BTC_USDT", "limit": 20}
        try:
            safe_call_tool(client, conn, "mcp", tool_name, args)
            called[group] = tool_name
        except Exception as exc:
            errors[group] = str(exc)
    return {"synced": bool(called), "called": called, "errors": errors, "available_groups": sorted(selected)}


# ---------------------------------------------------------------------------
# A3: Gate Info 技术面同步
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# A1 + A6: 查询辅助
# ---------------------------------------------------------------------------

def recent_followed_user_posts(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """返回指定用户（关注用户）的最近帖子。"""
    rows = conn.execute(
        """
        SELECT * FROM gate_square_posts
        WHERE is_followed_user = 1
        ORDER BY publish_time DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def list_analyst_source_accounts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """返回所有分析师来源账户映射。"""
    rows = conn.execute(
        "SELECT * FROM analyst_source_accounts ORDER BY enabled DESC, created_at DESC"
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def latest_nasdaq_data(conn: sqlite3.Connection, symbol: str = "IXIC", limit: int = 1) -> list[dict[str, Any]]:
    """返回最新纳指行情数据。"""
    rows = conn.execute(
        "SELECT * FROM nasdaq_market_data WHERE symbol = ? ORDER BY fetched_at DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def gate_mcp_source_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """返回 Gate MCP 数据源的整体状态摘要。"""
    btc = conn.execute(
        "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM btc_contract_metrics"
    ).fetchone()
    sentiment = conn.execute(
        "SELECT COUNT(*) AS count, MAX(snapshot_time) AS latest FROM market_sentiment_snapshots"
    ).fetchone()
    square = conn.execute(
        "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM gate_square_posts"
    ).fetchone()
    raw = conn.execute(
        "SELECT COUNT(*) AS count, MAX(created_at) AS latest FROM gate_mcp_raw_records"
    ).fetchone()
    memories = conn.execute(
        "SELECT COUNT(*) AS count FROM market_memories WHERE is_active = 1"
    ).fetchone()
    source_accounts = conn.execute(
        "SELECT COUNT(*) AS count FROM analyst_source_accounts WHERE enabled = 1"
    ).fetchone()
    followed_posts = conn.execute(
        "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM gate_square_posts WHERE is_followed_user = 1"
    ).fetchone()
    nasdaq = conn.execute(
        "SELECT COUNT(*) AS count, MAX(fetched_at) AS latest FROM nasdaq_market_data"
    ).fetchone()
    return {
        "btc_contract_metrics": {"count": btc["count"] if btc else 0, "latest": btc["latest"] if btc else None},
        "sentiment_snapshots": {"count": sentiment["count"] if sentiment else 0, "latest": sentiment["latest"] if sentiment else None},
        "square_posts": {"count": square["count"] if square else 0, "latest": square["latest"] if square else None},
        "mcp_raw_records": {"count": raw["count"] if raw else 0, "latest": raw["latest"] if raw else None},
        "active_memories": {"count": memories["count"] if memories else 0},
        "analyst_source_accounts": {"count": source_accounts["count"] if source_accounts else 0},
        "followed_user_posts": {"count": followed_posts["count"] if followed_posts else 0, "latest": followed_posts["latest"] if followed_posts else None},
        "nasdaq_market_data": {"count": nasdaq["count"] if nasdaq else 0, "latest": nasdaq["latest"] if nasdaq else None},
    }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _default_client(conn: sqlite3.Connection) -> GateMCPClient:
    """从数据库 settings 读取配置创建客户端。"""
    from .services import get_setting_value

    base_url = str(get_setting_value(conn, "gate_mcp.base_url", "https://api.gatemcp.ai"))
    timeout = int(get_setting_value(conn, "gate_mcp.timeout_seconds", 30))
    return GateMCPClient(base_url=base_url, timeout=timeout)
