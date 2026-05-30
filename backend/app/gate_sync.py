"""Gate 数据源同步服务（REST API + 市场数据计算）。

包含 BTC 合约行情/K线/资金费率同步、市场情绪快照、记忆压缩等。
"""

from __future__ import annotations

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

logger = logging.getLogger(__name__)

def _write_btc_contract_metrics(conn: sqlite3.Connection, ticker: dict[str, Any], source: str) -> None:
    """将 ticker 字典写入 btc_contract_metrics。"""
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
            _float(ticker.get("volume_24h") or ticker.get("volume_24h_settle") or ticker.get("volume_24h_quote")),
            _float(ticker.get("quanto_base_rate") or ticker.get("open_interest")),
            _float(ticker.get("high_24h")),
            _float(ticker.get("low_24h")),
            _float(ticker.get("change_percentage")),
            source,
            fetched_at,
            now,
        ),
    )
    conn.commit()

def sync_btc_contract_metrics(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate REST API 获取 BTC 合约行情并写入 btc_contract_metrics；失败时回退到 MCP。"""
    # 优先尝试 REST API
    try:
        rest = GateRestClient()
        tickers = rest.list_futures_tickers(settle="usdt", contract="BTC_USDT")
        if isinstance(tickers, list) and tickers:
            ticker = tickers[0]
            if isinstance(ticker, dict) and ticker.get("contract"):
                _write_btc_contract_metrics(conn, ticker, "gate_rest")
                logger.info("BTC contract synced via REST API")
                return _btc_metrics_and_chained(conn, client, ticker, "gate_rest")
    except Exception as exc:
        logger.warning("BTC contract REST API failed, fallback to MCP: %s", exc)

    # 回退到 MCP
    if client is None:
        client = _default_client(conn)
    errors: list[str] = []
    result: dict[str, Any] | None = None
    used_tool = ""
    for endpoint, tool_name, arguments in _btc_contract_tool_candidates(client):
        try:
            result = safe_call_tool(client, conn, endpoint, tool_name, arguments)
            used_tool = tool_name
            break
        except Exception as exc:
            errors.append(f"{endpoint}/{tool_name}: {exc}")
            continue
    if result is None:
        error_message = "; ".join(errors) if errors else "no btc contract tool candidate available"
        logger.warning("BTC contract sync failed: %s", error_message)
        return {"synced": False, "error": error_message}

    data = parse_json_from_content(result)
    ticker = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    if not ticker:
        return {"synced": False, "error": "empty ticker response", "tool": used_tool}

    _write_btc_contract_metrics(conn, ticker, "gate_mcp")
    return _btc_metrics_and_chained(conn, client, ticker, "gate_mcp")

def _btc_metrics_and_chained(conn: sqlite3.Connection, client: GateMCPClient | None, ticker: dict[str, Any], source: str) -> dict[str, Any]:
    now = utc_now()
    fetched_at = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
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
        "source": source,
        "klines": kline_result,
        "funding_history": funding_result,
        "context": context_result,
    }

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
        """
        SELECT * FROM gate_square_posts
        WHERE is_hot_post = 1
        ORDER BY hot_score DESC, likes DESC, comments DESC, repost_count DESC, publish_time DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]

def sync_btc_contract_klines(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate REST API 拉取 BTC 合约 K 线写入 market_data 表，并补全中间缺失数据、重算指标。"""
    from .database import utc_now as _utc_now, rebuild_indicator_group
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

    rest = GateRestClient()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    for interval in intervals:
        gate_interval = intervals_map[interval]
        interval_sec = int(gate_interval)

        last_row = conn.execute(
            """
            SELECT MAX(open_time) as max_open_time
            FROM market_data
            WHERE symbol = ? AND market_type = ? AND interval = ?
            """,
            ("BTCUSDT", "perpetual", interval),
        ).fetchone()
        from_ts: int | None = None
        limit = 200
        if last_row and last_row["max_open_time"]:
            try:
                max_dt = datetime.fromisoformat(last_row["max_open_time"])
                max_ts = int(max_dt.timestamp())
                from_ts = max_ts + interval_sec
                needed = (now_ts - from_ts) // interval_sec + 2
                limit = min(max(needed, 50), 1000)
            except Exception:
                from_ts = None
                limit = 200

        try:
            klines = rest.list_futures_candlesticks(
                settle="usdt", contract="BTC_USDT", interval=gate_interval, limit=limit, from_ts=from_ts
            )
        except Exception as exc:
            logger.warning("BTC kline REST API failed for %s: %s", interval, exc)
            errors[interval] = str(exc)
            continue

        for kline in klines:
            if not isinstance(kline, (list, tuple)) or len(kline) < 6:
                continue
            try:
                # Gate REST K线格式: [time, volume, close, high, low, open]
                ts = kline[0]
                open_time = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else now
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
                        _float(kline[5]),   # open
                        _float(kline[3]),   # high
                        _float(kline[4]),   # low
                        _float(kline[2]),   # close
                        _float(kline[1]),   # volume
                        0.0,
                        now,
                    ),
                )
                total += 1
            except Exception:
                continue

        try:
            rebuild_indicator_group(conn, "BTCUSDT", "perpetual", interval)
            conn.commit()
        except Exception as exc:
            logger.warning("Rebuild indicators failed for %s: %s", interval, exc)

        try:
            funding_row = conn.execute(
                """
                SELECT funding_rate FROM btc_contract_metrics
                WHERE symbol = 'BTC_USDT' AND funding_rate IS NOT NULL
                ORDER BY fetched_at DESC LIMIT 1
                """
            ).fetchone()
            if funding_row and funding_row["funding_rate"] is not None:
                conn.execute(
                    """
                    UPDATE market_data
                    SET funding_rate = ?
                    WHERE symbol = ? AND market_type = ? AND interval = ?
                      AND open_time = (
                          SELECT MAX(open_time) FROM market_data
                          WHERE symbol = ? AND market_type = ? AND interval = ?
                      )
                    """,
                    (funding_row["funding_rate"], "BTCUSDT", "perpetual", interval,
                     "BTCUSDT", "perpetual", interval),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("Update funding_rate failed for %s: %s", interval, exc)

    return {"synced": True, "klines_inserted": total, "intervals": intervals, "errors": errors}

def sync_btc_funding_rate_history(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate REST API 拉取 BTC 资金费率历史。"""
    try:
        rest = GateRestClient()
        rates = rest.list_futures_funding_rate(settle="usdt", contract="BTC_USDT", limit=20)
    except Exception as exc:
        logger.warning("BTC funding rate history REST API failed: %s", exc)
        return {"synced": False, "error": str(exc)}

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
                ) VALUES ('BTC_USDT', 0, ?, 'gate_rest_funding', ?, ?)
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
    """通过 Gate REST API 直接获取合约深度、成交、统计等上下文，作为 raw records 保存。"""
    rest = GateRestClient()
    settle = "usdt"
    contract = "BTC_USDT"
    called: dict[str, str] = {}
    errors: dict[str, str] = {}
    now = utc_now()

    endpoints = {
        "order_book": ("depth", lambda: rest.list_futures_order_book(settle, contract, limit=10)),
        "trades": ("trades", lambda: rest.list_futures_trades(settle, contract, limit=100)),
        "contract_stats": ("stats", lambda: rest.list_contract_stats(settle, contract, limit=100)),
        "liquidation": ("liq_orders", lambda: rest.list_liquidated_orders(settle, contract, limit=100)),
        "premium_index": ("premium", lambda: rest.list_futures_premium_index(settle, contract, interval="1h", limit=100)),
    }

    for group, (endpoint, caller) in endpoints.items():
        try:
            data = caller()
            record_mcp_call(
                conn,
                f"rest/{endpoint}",
                endpoint,
                {"settle": settle, "contract": contract},
                {"response": data},
                "success",
                None,
                0,
            )
            called[group] = endpoint
        except Exception as exc:
            logger.warning("BTC contract context REST %s failed: %s", endpoint, exc)
            errors[group] = str(exc)

    return {"synced": bool(called), "called": called, "errors": errors, "available_groups": sorted(called)}

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
    return {
        "btc_contract_metrics": {"count": btc["count"] if btc else 0, "latest": btc["latest"] if btc else None},
        "sentiment_snapshots": {"count": sentiment["count"] if sentiment else 0, "latest": sentiment["latest"] if sentiment else None},
        "square_posts": {"count": square["count"] if square else 0, "latest": square["latest"] if square else None},
        "mcp_raw_records": {"count": raw["count"] if raw else 0, "latest": raw["latest"] if raw else None},
        "active_memories": {"count": memories["count"] if memories else 0},
        "analyst_source_accounts": {"count": source_accounts["count"] if source_accounts else 0},
        "followed_user_posts": {"count": followed_posts["count"] if followed_posts else 0, "latest": followed_posts["latest"] if followed_posts else None},
    }

def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def _int(value: Any) -> int:
    numeric = _float(value)
    return int(numeric) if numeric is not None else 0
