from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

from .database import DEFAULT_SETTINGS, parse_dt, row_to_dict, rows_to_dicts, setting_value_to_text, utc_now

# 中文规则解析使用的关键词词典，用于在 LLM 不可用时兜底识别观点方向和置信度。
BULLISH_TERMS = ["看涨", "上涨", "涨到", "涨至", "上看", "突破", "拉升", "反弹", "做多", "多头", "冲高"]
BEARISH_TERMS = ["看跌", "下跌", "跌到", "跌至", "下看", "回调", "回落", "破位", "做空", "空头", "跳水"]
SIDEWAYS_TERMS = ["震荡", "横盘", "区间", "观望"]
HIGH_CONFIDENCE_TERMS = ["确定", "强烈", "大概率", "明确", "必然"]
LOW_CONFIDENCE_TERMS = ["可能", "也许", "或许", "不确定", "倾向"]
BINANCE_SPOT_TICKER_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_FUTURES_PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
GATEIO_SPOT_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"


def refresh_due_verification_schedule() -> None:
    try:
        from .scheduler import schedule_next_due_verification
    except ImportError:
        return
    schedule_next_due_verification()


def parse_setting_value(value: str, value_type: str) -> Any:
    # 将 settings 表中的字符串值还原为业务需要的类型。
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() in {"1", "true", "yes", "on"}
    if value_type == "json":
        return json.loads(value)
    return value


def list_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    # 返回设置项列表以及按 key 聚合后的解析值。
    rows = conn.execute("SELECT * FROM settings ORDER BY key ASC").fetchall()
    items = rows_to_dicts(rows)
    values: dict[str, Any] = {}
    for item in items:
        item["parsed_value"] = parse_setting_value(str(item["value"]), str(item["value_type"]))
        values[str(item["key"])] = item["parsed_value"]
    return {"items": items, "values": values}


def get_setting_value(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    # 优先读取数据库设置，缺失时回退到 DEFAULT_SETTINGS 或传入默认值。
    row = conn.execute("SELECT value, value_type FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return parse_setting_value(str(row["value"]), str(row["value_type"]))
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key][0]
    return default


def update_setting(conn: sqlite3.Connection, key: str, value: Any) -> dict[str, Any]:
    # 新增或更新单个设置项，并保持 value_type 和描述信息。
    row = conn.execute("SELECT * FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        value_type = str(row["value_type"])
        description = row["description"]
    else:
        value_type = DEFAULT_SETTINGS.get(key, (value, "json" if isinstance(value, (dict, list)) else type(value).__name__, ""))[1]
        description = DEFAULT_SETTINGS.get(key, (None, "", ""))[2]
    conn.execute(
        """
        INSERT INTO settings (key, value, value_type, description, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, value_type = excluded.value_type, description = excluded.description, updated_at = excluded.updated_at
        """,
        (key, setting_value_to_text(value, value_type), value_type, description, utc_now()),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM settings WHERE key = ?", (key,)).fetchone()
    item = row_to_dict(updated) or {}
    if item:
        item["parsed_value"] = parse_setting_value(str(item["value"]), str(item["value_type"]))
    return item


def reset_default_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    # 将所有内置设置恢复为默认值。
    for key, (value, value_type, description) in DEFAULT_SETTINGS.items():
        conn.execute(
            """
            INSERT INTO settings (key, value, value_type, description, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, value_type = excluded.value_type, description = excluded.description, updated_at = excluded.updated_at
            """,
            (key, setting_value_to_text(value, value_type), value_type, description, utc_now()),
        )
    conn.commit()
    cleanup_stale_settings(conn)
    return list_settings(conn)


def cleanup_stale_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """删除数据库中不在 DEFAULT_SETTINGS 定义中的旧设置项，并返回清理结果。"""
    valid_keys = set(DEFAULT_SETTINGS.keys())
    rows = conn.execute("SELECT key FROM settings").fetchall()
    stale_keys = [str(row["key"]) for row in rows if str(row["key"]) not in valid_keys]
    deleted = 0
    for key in stale_keys:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        deleted += 1
    if deleted:
        conn.commit()
        logger.info("Cleaned up %d stale settings: %s", deleted, stale_keys)
    return {"deleted": deleted, "stale_keys": stale_keys}


def latest_market(
    conn: sqlite3.Connection,
    symbol: str | None = None,
    interval: str | None = None,
    market_type: str | None = None,
) -> dict[str, Any]:
    # 获取指定交易对、市场类型和周期下的最新一根 K 线及指标。
    symbol = symbol or str(get_setting_value(conn, "market.symbol", "BTCUSDT"))
    interval = interval or str(get_setting_value(conn, "market.default_interval", "1h"))
    market_type = market_type or "perpetual"
    row = conn.execute(
        """
        SELECT
            md.id AS market_id,
            md.symbol,
            md.market_type,
            md.interval,
            md.open_time,
            md.open,
            md.high,
            md.low,
            md.close,
            md.volume,
            md.funding_rate,
            i.ma_20,
            i.ema_20,
            i.rsi_14,
            i.macd,
            i.atr_14,
            i.bb_upper,
            i.bb_lower
        FROM market_data md
        LEFT JOIN indicators i ON i.market_data_id = md.id
        WHERE md.symbol = ? AND md.interval = ? AND md.market_type = ?
        ORDER BY md.open_time DESC
        LIMIT 1
        """,
        (symbol.upper(), interval, market_type),
    ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT
                md.id AS market_id,
                md.symbol,
                md.market_type,
                md.interval,
                md.open_time,
                md.open,
                md.high,
                md.low,
                md.close,
                md.volume,
                md.funding_rate,
                i.ma_20,
                i.ema_20,
                i.rsi_14,
                i.macd,
                i.atr_14,
                i.bb_upper,
                i.bb_lower
            FROM market_data md
            LEFT JOIN indicators i ON i.market_data_id = md.id
            ORDER BY md.open_time DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("market data is empty")
    return row_to_dict(row) or {}


def fetch_live_market_price(symbol: str = "BTCUSDT", market_type: str = "perpetual") -> dict[str, Any]:
    # 从 Gate.io 实时行情接口读取最新价格。
    symbol = symbol.upper()
    gateio_pair = symbol.replace("USDT", "_USDT")
    query = urllib.parse.urlencode({"currency_pair": gateio_pair})
    url = f"{GATEIO_SPOT_TICKERS_URL}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "btc-agent-mvp/0.1"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    item = data[0] if isinstance(data, list) and data else {}
    price = float(item.get("last") or 0)
    if price <= 0:
        raise RuntimeError("gateio_spot_ticker: live price is empty")
    return {
        "symbol": symbol,
        "market_type": market_type,
        "price": price,
        "funding_rate": None,
        "source": "gateio_spot_ticker",
        "fetched_at": utc_now(),
    }


def live_market_price(conn: sqlite3.Connection, symbol: str = "BTCUSDT", market_type: str = "perpetual") -> dict[str, Any]:
    # 优先返回实时价格；外部接口失败时回退到数据库最新价格。
    try:
        result = fetch_live_market_price(symbol, market_type)
        if result["price"] > 0:
            return result
    except Exception as exc:
        error = str(exc)
    else:
        error = "live price is empty"
    fallback = latest_market(conn, symbol=symbol, market_type=market_type)
    return {
        "symbol": fallback.get("symbol") or symbol.upper(),
        "market_type": fallback.get("market_type") or market_type,
        "price": float(fallback.get("close") or 0),
        "funding_rate": fallback.get("funding_rate"),
        "source": "db_fallback",
        "fetched_at": utc_now(),
        "open_time": fallback.get("open_time"),
        "error": error,
    }


def market_series(
    conn: sqlite3.Connection,
    limit: int = 120,
    interval: str | None = None,
    symbol: str | None = None,
    market_type: str | None = None,
) -> list[dict[str, Any]]:
    # 查询行情序列并按时间升序返回，便于前端直接绘图。
    symbol = symbol or str(get_setting_value(conn, "market.symbol", "BTCUSDT"))
    interval = interval or str(get_setting_value(conn, "market.default_interval", "1h"))
    market_type = market_type or "perpetual"
    rows = conn.execute(
        """
        SELECT
            md.id,
            md.open_time,
            md.open,
            md.high,
            md.low,
            md.close,
            md.volume,
            md.funding_rate,
            i.ma_20,
            i.ema_20,
            i.rsi_14,
            i.macd,
            i.atr_14,
            i.bb_upper,
            i.bb_lower
        FROM market_data md
        LEFT JOIN indicators i ON i.market_data_id = md.id
        WHERE md.symbol = ? AND md.interval = ? AND md.market_type = ?
        ORDER BY md.open_time DESC
        LIMIT ?
        """,
        (symbol.upper(), interval, market_type, limit),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT
                md.id,
                md.open_time,
                md.open,
                md.high,
                md.low,
                md.close,
                md.volume,
                md.funding_rate,
                i.ma_20,
                i.ema_20,
                i.rsi_14,
                i.macd,
                i.atr_14,
                i.bb_upper,
                i.bb_lower
            FROM market_data md
            LEFT JOIN indicators i ON i.market_data_id = md.id
            WHERE md.symbol = ? AND md.interval = ?
            ORDER BY md.open_time DESC
            LIMIT ?
            """,
            (symbol.upper(), interval, limit),
        ).fetchall()
    return list(reversed(rows_to_dicts(rows)))


def market_summary(conn: sqlite3.Connection, interval: str | None = None) -> dict[str, Any]:
    # 基于最近行情计算涨跌幅、支撑压力、波动率和趋势标签。
    latest = latest_market(conn, interval=interval)
    rows = market_series(conn, 48, interval=latest["interval"], symbol=latest["symbol"], market_type=latest["market_type"])
    reference = rows[-25] if len(rows) >= 25 else rows[0]
    change_24h = ((latest["close"] - reference["close"]) / reference["close"]) * 100 if reference["close"] else 0
    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    support = min(lows[-24:]) if lows else latest["low"]
    resistance = max(highs[-24:]) if highs else latest["high"]
    ma_20 = latest.get("ma_20") or latest["close"]
    macd = latest.get("macd") or 0
    atr = latest.get("atr_14") or 0
    volatility = atr / latest["close"] * 100 if latest["close"] else 0
    if latest["close"] > ma_20 and macd >= 0:
        trend = "uptrend"
        trend_label = "偏多趋势"
    elif latest["close"] < ma_20 and macd <= 0:
        trend = "downtrend"
        trend_label = "偏空趋势"
    else:
        trend = "sideways"
        trend_label = "震荡整理"
    return {
        "latest_price": latest["close"],
        "symbol": latest["symbol"],
        "market_type": latest["market_type"],
        "interval": latest["interval"],
        "open_time": latest["open_time"],
        "change_24h": round(change_24h, 2),
        "trend": trend,
        "trend_label": trend_label,
        "volatility": round(volatility, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "rsi_14": latest.get("rsi_14"),
        "macd": latest.get("macd"),
        "atr_14": latest.get("atr_14"),
        "funding_rate": latest.get("funding_rate"),
        "close_series": closes[-24:],
    }


def get_or_create_analyst(conn: sqlite3.Connection, name: str, source: str | None = None) -> dict[str, Any]:
    # 按名称复用已有分析师，不存在时创建新分析师。
    clean_name = name.strip()
    row = conn.execute("SELECT * FROM analysts WHERE name = ?", (clean_name,)).fetchone()
    if row:
        return row_to_dict(row) or {}
    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO analysts (name, source, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (clean_name, source, now, now),
    )
    conn.commit()
    created = conn.execute("SELECT * FROM analysts WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(created) or {}


def direction_label(direction: str) -> str:
    labels = {"bullish": "看涨", "bearish": "看跌", "sideways": "震荡"}
    return labels.get(direction, direction)


def normalize_prediction_direction(value: Any) -> str:
    # 将模型或用户输入的方向别名统一归一化为内部枚举。
    text = str(value or "sideways").lower()
    if text in {"bull", "long", "up", "buy", "bullish"}:
        return "bullish"
    if text in {"bear", "short", "down", "sell", "bearish"}:
        return "bearish"
    if text in {"range", "neutral", "sideway", "sideways"}:
        return "sideways"
    return "unknown"


# 这些字段是枚举、ID 或时间，不参与面向用户的文本本地化。
DISPLAY_TEXT_SKIP_KEYS = {
    "id",
    "agent_run_id",
    "analyst_id",
    "prediction_id",
    "raw_opinion_id",
    "direction",
    "horizon",
    "confidence",
    "status",
    "decision",
    "created_at",
    "updated_at",
    "verification_time",
    "published_at",
    "source_url",
    "symbol",
    "market_type",
    "report_type",
    "quality_label",
    "change_type",
}


def display_has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def display_has_english(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", value))


def localize_display_text(value: str) -> str:
    replacements = {
        "bullish": "看多",
        "bearish": "看空",
        "sideways": "震荡",
        "high confidence": "高置信度",
        "medium confidence": "中等置信度",
        "low confidence": "低置信度",
        "target price": "目标价",
        "support": "支撑",
        "resistance": "压力",
        "risk": "风险",
        "market summary": "市场摘要",
        "uptrend": "上升趋势",
        "downtrend": "下降趋势",
        "open long": "开多",
        "open short": "开空",
        "observe": "观望",
        "success": "成功",
        "failed": "失败",
    }
    result = value
    for source, target in replacements.items():
        result = re.sub(source, target, result, flags=re.IGNORECASE)
    return result


def localize_display_payload(value: Any, key: str = "") -> Any:
    # 递归本地化展示数据中的英文短语。
    if isinstance(value, dict):
        return {item_key: localize_display_payload(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [localize_display_payload(item, key) for item in value]
    if isinstance(value, str):
        if key in DISPLAY_TEXT_SKIP_KEYS or display_has_chinese(value) or not display_has_english(value):
            return value
        return localize_display_text(value)
    return value


def horizon_days(horizon: str, conn: sqlite3.Connection | None = None) -> int:
    # 根据预测周期和系统设置计算默认验证天数。
    mapping = {
        "intraday": int(get_setting_value(conn, "prediction.horizon_intraday_days", 1)) if conn else 1,
        "short": int(get_setting_value(conn, "prediction.horizon_short_days", 7)) if conn else 7,
        "medium": int(get_setting_value(conn, "prediction.horizon_medium_days", 30)) if conn else 30,
        "long": int(get_setting_value(conn, "prediction.horizon_long_days", 90)) if conn else 90,
    }
    return mapping.get(horizon, 7)


def horizon_label(horizon: str) -> str:
    labels = {"intraday": "日内", "short": "短期", "medium": "中期", "long": "长期"}
    return labels.get(horizon, horizon)


def detect_horizon(text: str) -> str:
    # 使用中文时间表达粗略判断预测周期。
    if any(term in text for term in ["日内", "今天", "今日", "24小时"]):
        return "intraday"
    if any(term in text for term in ["短线", "短期", "本周", "几天"]):
        return "short"
    if any(term in text for term in ["中线", "中期", "本月", "几周"]):
        return "medium"
    if any(term in text for term in ["长线", "长期", "季度", "半年", "一年"]):
        return "long"
    return "short"


def detect_direction(text: str) -> str:
    # 使用关键词判断观点方向；多空同时出现时按首次出现的位置决策。
    bullish = any(term in text for term in BULLISH_TERMS)
    bearish = any(term in text for term in BEARISH_TERMS)
    sideways = any(term in text for term in SIDEWAYS_TERMS)
    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    if sideways and not bullish and not bearish:
        return "sideways"
    if bullish and bearish:
        first_bullish = min((text.find(term) for term in BULLISH_TERMS if term in text), default=10**6)
        first_bearish = min((text.find(term) for term in BEARISH_TERMS if term in text), default=10**6)
        return "bullish" if first_bullish < first_bearish else "bearish"
    return "sideways"


def detect_confidence(text: str, target_price: float | None, direction: str) -> str:
    # 根据置信度关键词、目标价和方向给出兜底置信度。
    if direction == "sideways" or target_price is None:
        return "low"
    if any(term in text for term in HIGH_CONFIDENCE_TERMS):
        return "high"
    if any(term in text for term in LOW_CONFIDENCE_TERMS):
        return "medium"
    return "medium"


def extract_prices(text: str) -> list[float]:
    # 从中文观点中提取目标价，支持“7万”和 5-7 位数字价格。
    prices: list[float] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*万", text):
        prices.append(float(match.group(1)) * 10000)
    normalized = re.sub(r"\d+(?:\.\d+)?\s*万", " ", text)
    for match in re.finditer(r"(?<![A-Za-z0-9.])([1-9]\d{4,6}(?:\.\d+)?)(?![A-Za-z0-9.])", normalized):
        prices.append(float(match.group(1)))
    unique: list[float] = []
    for price in prices:
        if price >= 10000 and price not in unique:
            unique.append(price)
    return unique


def split_opinion_text(text: str) -> list[str]:
    # 按中文标点切分观点，提升多目标价、多周期预测的解析粒度。
    parts = [part.strip() for part in re.split(r"[。；;\n]", text) if part.strip()]
    clauses: list[str] = []
    for part in parts:
        sub_parts = [sub.strip() for sub in re.split(r"[，,]", part) if sub.strip()]
        if len(sub_parts) <= 1:
            clauses.append(part)
        else:
            clauses.extend(sub_parts)
    return clauses or [text]


def parse_opinion(content: str, current_price: float) -> list[dict[str, Any]]:
    # 规则兜底版观点解析：从文本中抽取方向、周期、目标价和摘要。
    predictions: list[dict[str, Any]] = []
    clauses = split_opinion_text(content)
    for clause in clauses:
        prices = extract_prices(clause)
        if not prices and clause != content:
            continue
        direction = detect_direction(clause)
        horizon = detect_horizon(clause)
        if prices:
            for price in prices:
                confidence = detect_confidence(clause, price, direction)
                predictions.append(
                    {
                        "direction": direction,
                        "target_price": round(price, 2),
                        "horizon": horizon,
                        "confidence": confidence,
                        "summary": f"{horizon_label(horizon)}{direction_label(direction)}，目标价 {round(price, 2)}",
                    }
                )
        elif direction != "sideways":
            confidence = detect_confidence(clause, None, direction)
            predictions.append(
                {
                    "direction": direction,
                    "target_price": None,
                    "horizon": horizon,
                    "confidence": confidence,
                    "summary": f"{horizon_label(horizon)}{direction_label(direction)}，未给出明确目标价",
                }
            )
    if predictions:
        return predictions
    horizon = detect_horizon(content)
    return [
        {
            "direction": "sideways",
            "target_price": None,
            "horizon": horizon,
            "confidence": "low",
            "summary": f"{horizon_label(horizon)}观点不明确，按观望记录",
        }
    ]


def prediction_horizon_key(value: Any) -> str:
    text = str(value or "short").lower()
    if text in {"scalp", "intraday"}:
        return "intraday"
    if text in {"medium", "mid_term"}:
        return "medium"
    if text in {"long", "long_term"}:
        return "long"
    return "short"


def prediction_horizon_rank(prediction: dict[str, Any]) -> tuple[int, int, int]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}.get(str(prediction.get("confidence") or "medium").lower(), 2)
    has_target = 1 if prediction.get("target_price") not in (None, "", "null") else 0
    summary_length = len(str(prediction.get("summary") or prediction.get("evidence_text") or ""))
    return confidence_rank, has_target, summary_length


def dedupe_predictions_by_horizon_direction(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 同一周期和方向只保留信息质量最高的一条预测。
    best_by_horizon_direction: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction in predictions:
        horizon = prediction_horizon_key(prediction.get("horizon"))
        direction = normalize_prediction_direction(prediction.get("direction"))
        item = {**prediction, "horizon": horizon, "direction": direction}
        key = (horizon, direction)
        current = best_by_horizon_direction.get(key)
        if current is None or prediction_horizon_rank(item) > prediction_horizon_rank(current):
            best_by_horizon_direction[key] = item
    horizon_order = {"intraday": 0, "short": 1, "medium": 2, "long": 3}
    direction_order = {"bullish": 0, "bearish": 1, "sideways": 2, "unknown": 3}
    return sorted(
        best_by_horizon_direction.values(),
        key=lambda item: (
            horizon_order.get(str(item.get("horizon")), 99),
            direction_order.get(str(item.get("direction")), 99),
        ),
    )


def dedupe_predictions_by_horizon(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_predictions_by_horizon_direction(predictions)


def persist_structured_opinion(
    conn: sqlite3.Connection,
    analyst_name: str,
    content: str,
    source_url: str | None,
    published_at: str | None,
    current_price: float,
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    # 将结构化观点写入 raw_opinions 和 predictions，并检测是否存在改口。
    analyst = get_or_create_analyst(conn, str(analyst_name), source_url)
    now = utc_now()
    normalized_published_at = published_at or now
    cursor = conn.execute(
        """
        INSERT INTO raw_opinions (analyst_id, content, source_url, published_at, parsed_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (analyst["id"], content.strip(), source_url, normalized_published_at, now, now),
    )
    raw_opinion_id = cursor.lastrowid
    prediction_ids: list[int] = []
    saved_predictions: list[dict[str, Any]] = []
    for parsed in dedupe_predictions_by_horizon(predictions):
        verification_time = parsed.get("verification_time") or (parse_dt(normalized_published_at) + timedelta(days=horizon_days(parsed.get("horizon", "short"), conn))).isoformat()
        pred_cursor = conn.execute(
            """
            INSERT INTO predictions (
                analyst_id, raw_opinion_id, direction, target_price, horizon, current_price,
                verification_time, status, confidence, summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                analyst["id"],
                raw_opinion_id,
                parsed.get("direction", "sideways"),
                parsed.get("target_price"),
                parsed.get("horizon", "short"),
                current_price,
                verification_time,
                parsed.get("confidence", "medium"),
                parsed.get("summary") or "结构化预测",
                now,
                now,
            ),
        )
        prediction_id = pred_cursor.lastrowid
        prediction_ids.append(prediction_id)
        detect_prediction_conflicts(conn, analyst["id"], prediction_id)
        row = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
        saved_predictions.append(row_to_dict(row) or {})
    conn.execute("UPDATE analysts SET latest_opinion = ?, updated_at = ? WHERE id = ?", (content.strip(), now, analyst["id"]))
    conn.commit()
    recompute_analyst_metrics(conn, analyst["id"])
    refresh_due_verification_schedule()
    raw_opinion = conn.execute("SELECT * FROM raw_opinions WHERE id = ?", (raw_opinion_id,)).fetchone()
    return {
        "analyst": analyst,
        "raw_opinion": row_to_dict(raw_opinion),
        "raw_opinion_id": raw_opinion_id,
        "prediction_ids": prediction_ids,
        "predictions": saved_predictions,
    }


def get_human_review_detail(conn: sqlite3.Connection, review_id: int) -> dict[str, Any]:
    # 读取人工复核项及其草稿，并对展示字段做中文本地化。
    row = conn.execute("SELECT * FROM human_review_items WHERE id = ?", (review_id,)).fetchone()
    review = row_to_dict(row) or {}
    if not review:
        return {}
    draft_row = conn.execute("SELECT * FROM opinion_review_drafts WHERE review_item_id = ? ORDER BY id DESC LIMIT 1", (review_id,)).fetchone()
    draft = row_to_dict(draft_row) or {}
    review["data"] = localize_display_payload(json.loads(review.get("payload") or "{}"))
    if draft:
        review["draft"] = localize_display_payload(json.loads(draft.get("draft_payload") or "{}"))
        review["draft_meta"] = {
            "id": draft.get("id"),
            "analyst_name": draft.get("analyst_name"),
            "source_url": draft.get("source_url"),
            "published_at": draft.get("published_at"),
            "current_price": draft.get("current_price"),
            "created_at": draft.get("created_at"),
            "updated_at": draft.get("updated_at"),
        }
    return review


def confirm_human_review(
    conn: sqlite3.Connection,
    review_id: int,
    analyst_name: str | None = None,
    source_url: str | None = None,
    published_at: str | None = None,
    predictions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    detail = get_human_review_detail(conn, review_id)
    if not detail:
        return {}
    draft_meta = detail.get("draft_meta") or {}
    draft = detail.get("draft") or {}
    input_payload = draft.get("input_payload") or {}
    normalized_predictions = predictions or draft.get("parsed_predictions") or []
    created = persist_structured_opinion(
        conn,
        analyst_name or draft_meta.get("analyst_name") or input_payload.get("analyst_name") or "未命名分析师",
        input_payload.get("content") or (detail.get("data") or {}).get("payload", {}).get("content") or "",
        source_url if source_url is not None else draft_meta.get("source_url"),
        published_at if published_at is not None else draft_meta.get("published_at"),
        float(draft_meta.get("current_price") or input_payload.get("current_price") or latest_market(conn)["close"]),
        normalized_predictions,
    )
    now = utc_now()
    conn.execute(
        "UPDATE human_review_items SET status = 'resolved', raw_opinion_id = ?, resolved_at = ? WHERE id = ?",
        (created["raw_opinion_id"], now, review_id),
    )
    conn.execute(
        "UPDATE opinion_review_drafts SET updated_at = ? WHERE review_item_id = ?",
        (now, review_id),
    )
    conn.commit()
    agent_run = None
    if created.get("prediction_ids"):
        agent_run = run_agent(conn, "review_confirmed", created.get("prediction_ids"))
    return {
        "review": get_human_review_detail(conn, review_id),
        "raw_opinion": created["raw_opinion"],
        "predictions": created["predictions"],
        "prediction_ids": created["prediction_ids"],
        "agent_run": agent_run,
    }


def reject_human_review(conn: sqlite3.Connection, review_id: int, reason: str | None = None) -> dict[str, Any]:
    # 标记人工复核项为拒绝，并记录拒绝原因。
    detail = get_human_review_detail(conn, review_id)
    if not detail:
        return {}
    payload = detail.get("data") or {}
    payload["rejection_reason"] = reason or "人工确认拒绝"
    payload["rejected_at"] = utc_now()
    conn.execute(
        "UPDATE human_review_items SET status = 'rejected', payload = ?, resolved_at = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), utc_now(), review_id),
    )
    conn.commit()
    return get_human_review_detail(conn, review_id)


def create_opinion(conn: sqlite3.Connection, payload: Any) -> dict[str, Any]:
    # 运行观点入库 Graph，并在无需人工确认时自动触发分析 Agent。
    from .graphs import run_opinion_ingestion_graph

    current_price = latest_market(conn)["close"]
    state = run_opinion_ingestion_graph(conn, payload, current_price)
    result = state.get("result", {})
    agent_run = None
    if not state.get("needs_user_confirmation") and state.get("prediction_ids"):
        agent_run = run_agent(conn, "opinion_created", state.get("prediction_ids", []))
    return {
        **result,
        "agent_run": agent_run,
        "needs_user_confirmation": state.get("needs_user_confirmation", False),
        "graph": {
            "name": "opinion_ingestion",
            "node_outputs": state.get("node_outputs", {}),
            "warnings": state.get("warnings", []),
            "errors": state.get("errors", []),
        },
    }


def confidence_rank(value: str | None) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(value or "medium", 2)


def target_change_pct(old_target: Any, new_target: Any) -> float | None:
    if old_target is None or new_target is None:
        return None
    old_value = float(old_target)
    if old_value == 0:
        return None
    return abs(float(new_target) - old_value) / abs(old_value)


def prediction_change_record(
    conn: sqlite3.Connection,
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any] | None:
    # 比较同一分析师的新旧预测，识别方向反转、目标价变化和周期变化。
    target_threshold = float(get_setting_value(conn, "prediction.target_change_threshold_pct", 0.03))
    confidence_gap_threshold = int(get_setting_value(conn, "prediction.confidence_change_threshold_tiers", 2))
    horizon_severity = float(get_setting_value(conn, "prediction.horizon_change_penalty_factor", 0.75))
    changes: list[dict[str, Any]] = []
    old_direction = str(old.get("direction") or "")
    new_direction = str(new.get("direction") or "")
    same_horizon = old.get("horizon") == new.get("horizon")
    same_direction = old_direction == new_direction
    if same_horizon and old_direction != new_direction and old_direction != "sideways" and new_direction != "sideways":
        changes.append({"type": "direction_reversal", "severity": 1.0, "label": "方向反转"})
    target_pct = target_change_pct(old.get("target_price"), new.get("target_price"))
    if (same_horizon or same_direction) and target_pct is not None and target_pct >= target_threshold:
        changes.append({"type": "target_price_shift", "severity": min(1.0, target_pct / max(target_threshold, 0.0001)), "label": f"目标价变化 {round(target_pct * 100, 2)}%"})
    elif same_horizon and (old.get("target_price") is None) != (new.get("target_price") is None):
        if old.get("target_price") is None:
            changes.append({"type": "target_price_added", "severity": 0.6, "label": "目标价新增"})
        else:
            changes.append({"type": "target_price_removed", "severity": 0.6, "label": "目标价移除"})
    if not same_horizon and same_direction:
        changes.append({"type": "horizon_shift", "severity": max(0.0, min(1.0, horizon_severity)), "label": "预测周期变化"})
    confidence_gap = abs(confidence_rank(old.get("confidence")) - confidence_rank(new.get("confidence")))
    if same_horizon and confidence_gap >= confidence_gap_threshold:
        changes.append({"type": "confidence_shift", "severity": min(1.0, confidence_gap / 2), "label": "置信度明显变化"})
    if not changes:
        return None
    severity = max(float(item["severity"]) for item in changes)
    change_type = "multi_change" if len(changes) > 1 else str(changes[0]["type"])
    return {
        "change_type": change_type,
        "change_severity": round(severity, 3),
        "reason": "；".join(str(item["label"]) for item in changes),
        "changes": changes,
        "target_change_pct": round(target_pct * 100, 4) if target_pct is not None else None,
        "confidence_gap": confidence_gap,
    }


def detect_prediction_conflicts(conn: sqlite3.Connection, analyst_id: int, new_prediction_id: int) -> None:
    # 新预测入库后，查找同分析师未到期预测并记录“改口”版本。
    new_prediction_row = conn.execute("SELECT * FROM predictions WHERE id = ?", (new_prediction_id,)).fetchone()
    if new_prediction_row is None:
        return
    new_prediction = row_to_dict(new_prediction_row) or {}
    old_predictions = rows_to_dicts(conn.execute(
        """
        SELECT * FROM predictions
        WHERE analyst_id = ?
          AND id != ?
          AND raw_opinion_id != ?
          AND status = 'pending'
          AND verification_time > ?
          AND (horizon = ? OR direction = ?)
        ORDER BY created_at DESC
        """,
        (analyst_id, new_prediction_id, new_prediction.get("raw_opinion_id"), utc_now(), new_prediction.get("horizon"), new_prediction.get("direction")),
    ).fetchall())
    changed_count = 0
    for old in old_predictions:
        change = prediction_change_record(conn, old, new_prediction)
        if not change:
            continue
        exists = conn.execute(
            "SELECT id FROM prediction_versions WHERE prediction_id = ? AND new_prediction_id = ? LIMIT 1",
            (old["id"], new_prediction_id),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "UPDATE predictions SET status = 'modified', is_modified = 1, updated_at = ? WHERE id = ?",
            (utc_now(), old["id"]),
        )
        conn.execute(
            """
            INSERT INTO prediction_versions (
                prediction_id, old_direction, old_target_price, old_horizon, old_confidence,
                new_prediction_id, new_direction, new_target_price, new_horizon, new_confidence,
                change_type, change_severity, reason, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                old["id"],
                old["direction"],
                old["target_price"],
                old.get("horizon"),
                old.get("confidence"),
                new_prediction_id,
                new_prediction.get("direction"),
                new_prediction.get("target_price"),
                new_prediction.get("horizon"),
                new_prediction.get("confidence"),
                change["change_type"],
                change["change_severity"],
                change["reason"],
                json.dumps({"old_prediction": old, "new_prediction": new_prediction, "change": change}, ensure_ascii=False, default=str),
                utc_now(),
            ),
        )
        changed_count += 1
    if changed_count:
        conn.execute(
            "UPDATE analysts SET stability_score = MAX(0, stability_score - ?), updated_at = ? WHERE id = ?",
            (changed_count * 8, utc_now(), analyst_id),
        )


def list_opinions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ro.*, a.name AS analyst_name
        FROM raw_opinions ro
        JOIN analysts a ON a.id = ro.analyst_id
        ORDER BY ro.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def list_predictions(conn: sqlite3.Connection, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    # 返回预测列表，并附带最近一次验证结果和最近一次改口记录。
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE p.status = ?"
        params.append(status)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            p.*,
            a.name AS analyst_name,
            vr.final_score AS latest_final_score,
            vr.quality_label AS latest_quality_label,
            vr.direction_score AS latest_direction_score,
            vr.target_score AS latest_target_score,
            vr.target_distance_pct AS latest_target_distance_pct,
            vr.price_change_pct AS latest_price_change_pct,
            pv.change_type AS latest_change_type,
            pv.change_severity AS latest_change_severity,
            pv.reason AS latest_change_reason,
            pv.old_target_price AS latest_old_target_price,
            pv.new_target_price AS latest_new_target_price
        FROM predictions p
        JOIN analysts a ON a.id = p.analyst_id
        LEFT JOIN verification_results vr ON vr.id = (
            SELECT MAX(vr2.id)
            FROM verification_results vr2
            WHERE vr2.prediction_id = p.id
        )
        LEFT JOIN prediction_versions pv ON pv.id = (
            SELECT MAX(pv2.id)
            FROM prediction_versions pv2
            WHERE pv2.prediction_id = p.id OR pv2.new_prediction_id = p.id
        )
        {where}
        ORDER BY p.verification_time ASC, p.created_at ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return rows_to_dicts(rows)


def get_prediction_item(conn: sqlite3.Connection, prediction_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            p.*,
            a.name AS analyst_name,
            vr.final_score AS latest_final_score,
            vr.quality_label AS latest_quality_label,
            vr.direction_score AS latest_direction_score,
            vr.target_score AS latest_target_score,
            vr.target_distance_pct AS latest_target_distance_pct,
            vr.price_change_pct AS latest_price_change_pct,
            pv.change_type AS latest_change_type,
            pv.change_severity AS latest_change_severity,
            pv.reason AS latest_change_reason,
            pv.old_target_price AS latest_old_target_price,
            pv.new_target_price AS latest_new_target_price
        FROM predictions p
        JOIN analysts a ON a.id = p.analyst_id
        LEFT JOIN verification_results vr ON vr.id = (
            SELECT MAX(vr2.id)
            FROM verification_results vr2
            WHERE vr2.prediction_id = p.id
        )
        LEFT JOIN prediction_versions pv ON pv.id = (
            SELECT MAX(pv2.id)
            FROM prediction_versions pv2
            WHERE pv2.prediction_id = p.id OR pv2.new_prediction_id = p.id
        )
        WHERE p.id = ?
        """,
        (prediction_id,),
    ).fetchone()
    return row_to_dict(row) or {}


def update_prediction_manual(conn: sqlite3.Connection, prediction_id: int, values: dict[str, Any]) -> dict[str, Any]:
    # 人工修正预测字段，并写入 prediction_versions 作为审计记录。
    current = row_to_dict(conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()) or {}
    if not current:
        raise ValueError("prediction not found")
    allowed = {
        "direction": {"bullish", "bearish", "sideways"},
        "horizon": {"intraday", "short", "medium", "long"},
        "status": {"pending", "success", "failed", "modified"},
        "confidence": {"low", "medium", "high"},
    }
    updates: dict[str, Any] = {}
    for key in ("direction", "horizon", "status", "confidence"):
        if key in values and values[key] is not None:
            value = str(values[key]).strip()
            if value not in allowed[key]:
                raise ValueError(f"invalid {key}: {value}")
            updates[key] = value
    if "target_price" in values:
        target_price = values["target_price"]
        updates["target_price"] = float(target_price) if target_price not in (None, "") else None
    if values.get("verification_time") is not None:
        updates["verification_time"] = parse_dt(str(values["verification_time"])).isoformat()
    if values.get("summary") is not None:
        summary = str(values["summary"]).strip()
        if not summary:
            raise ValueError("summary cannot be empty")
        updates["summary"] = summary
    if not updates:
        return get_prediction_item(conn, prediction_id)
    updates["updated_at"] = utc_now()
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(f"UPDATE predictions SET {set_clause} WHERE id = ?", [*updates.values(), prediction_id])
    conn.execute(
        """
        INSERT INTO prediction_versions (
            prediction_id, old_direction, old_target_price, old_horizon, old_confidence,
            new_prediction_id, new_direction, new_target_price, new_horizon, new_confidence,
            change_type, change_severity, reason, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_id,
            current.get("direction"),
            current.get("target_price"),
            current.get("horizon"),
            current.get("confidence"),
            prediction_id,
            updates.get("direction", current.get("direction")),
            updates.get("target_price", current.get("target_price")),
            updates.get("horizon", current.get("horizon")),
            updates.get("confidence", current.get("confidence")),
            "manual_correction",
            0.5,
            "人工修正预测",
            json.dumps({"before": current, "updates": updates}, ensure_ascii=False, default=str),
            utc_now(),
        ),
    )
    recompute_analyst_metrics(conn, int(current["analyst_id"]))
    conn.commit()
    refresh_due_verification_schedule()
    return get_prediction_item(conn, prediction_id)


def delete_prediction_manual(conn: sqlite3.Connection, prediction_id: int) -> dict[str, Any]:
    current = row_to_dict(conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()) or {}
    if not current:
        raise ValueError("prediction not found")
    analyst_id = int(current["analyst_id"])
    conn.execute("DELETE FROM verification_reports WHERE prediction_id = ?", (prediction_id,))
    conn.execute("DELETE FROM verification_results WHERE prediction_id = ?", (prediction_id,))
    conn.execute("DELETE FROM prediction_versions WHERE prediction_id = ? OR new_prediction_id = ?", (prediction_id, prediction_id))
    conn.execute("DELETE FROM predictions WHERE id = ?", (prediction_id,))
    recompute_analyst_metrics(conn, analyst_id)
    conn.commit()
    refresh_due_verification_schedule()
    return {"deleted": True, "prediction_id": prediction_id}


def list_analysts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            a.*,
            COUNT(p.id) AS prediction_count,
            SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN p.status IN ('success', 'failed') THEN 1 ELSE 0 END) AS verified_count
        FROM analysts a
        LEFT JOIN predictions p ON p.analyst_id = a.id
        GROUP BY a.id
        ORDER BY a.total_score DESC, a.updated_at DESC
        """
    ).fetchall()
    return rows_to_dicts(rows)


def pending_predictions_for_agent(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    # 读取 Agent 生成方向分析所需的待验证预测及分析师评分指标。
    rows = conn.execute(
        """
        SELECT
            p.*,
            a.name AS analyst_name,
            a.total_score,
            a.weighted_win_rate,
            a.direction_accuracy,
            a.target_accuracy,
            a.modification_rate,
            a.stability_score
        FROM predictions p
        JOIN analysts a ON a.id = p.analyst_id
        WHERE p.status = 'pending'
        ORDER BY p.created_at DESC
        LIMIT 50
        """
    ).fetchall()
    return rows_to_dicts(rows)


def recent_verification_results_for_report(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            vr.id,
            vr.prediction_id,
            vr.status,
            vr.final_score,
            vr.direction_score,
            vr.target_score,
            vr.quality_label,
            vr.price_change_pct,
            vr.target_distance_pct,
            vr.created_at,
            p.direction,
            p.horizon,
            p.target_price,
            p.summary,
            a.name AS analyst_name,
            rep.plain_language_summary,
            rep.failure_reason
        FROM verification_results vr
        JOIN predictions p ON p.id = vr.prediction_id
        JOIN analysts a ON a.id = p.analyst_id
        LEFT JOIN verification_reports rep ON rep.id = (
            SELECT MAX(rep2.id)
            FROM verification_reports rep2
            WHERE rep2.prediction_id = vr.prediction_id
        )
        ORDER BY vr.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def recent_prediction_changes_for_report(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            pv.id,
            pv.prediction_id,
            pv.new_prediction_id,
            pv.old_direction,
            pv.new_direction,
            pv.old_target_price,
            pv.new_target_price,
            pv.old_horizon,
            pv.new_horizon,
            pv.old_confidence,
            pv.new_confidence,
            pv.change_type,
            pv.change_severity,
            pv.reason,
            pv.created_at,
            a.name AS analyst_name
        FROM prediction_versions pv
        JOIN predictions p ON p.id = pv.prediction_id
        JOIN analysts a ON a.id = p.analyst_id
        WHERE pv.change_type != 'manual_correction'
        ORDER BY pv.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_to_dicts(rows)


def daily_report_context(conn: sqlite3.Connection) -> dict[str, Any]:
    market = market_summary(conn)
    active_predictions = pending_predictions_for_agent(conn)
    recent_verifications = recent_verification_results_for_report(conn)
    prediction_changes = recent_prediction_changes_for_report(conn)
    top_analysts = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "total_score": item.get("total_score"),
            "weighted_win_rate": item.get("weighted_win_rate"),
            "direction_accuracy": item.get("direction_accuracy"),
            "target_accuracy": item.get("target_accuracy"),
            "modification_rate": item.get("modification_rate"),
            "prediction_count": item.get("prediction_count"),
        }
        for item in list_analysts(conn)[:5]
    ]
    # Gate MCP 增强上下文（只读附加，失败不影响主流程）
    gate_context: dict[str, Any] = {}
    try:
        from .gate_sync import latest_btc_contract_metrics, latest_sentiment_snapshot, active_market_memories
        gate_context["btc_contract"] = latest_btc_contract_metrics(conn)
        gate_context["sentiment"] = latest_sentiment_snapshot(conn)
        gate_context["memories"] = active_market_memories(conn, limit=10)
    except Exception:
        pass

    return {
        "market": market,
        "active_predictions": active_predictions,
        "recent_verifications": recent_verifications,
        "prediction_changes": prediction_changes,
        "top_analysts": top_analysts,
        "gate_context": gate_context,
    }


def choose_focus_prediction(predictions: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    for prediction in predictions:
        if prediction["direction"] == direction:
            return prediction
    return None


def score_predictions(predictions: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    bull_score = 0.0
    bear_score = 0.0
    bull_count = 0
    bear_count = 0
    for prediction in predictions:
        weight = max(0.35, min((prediction.get("total_score") or 50) / 100, 1.2))
        confidence = prediction.get("confidence")
        if confidence == "high":
            weight *= 1.2
        elif confidence == "low":
            weight *= 0.7
        target_price = prediction.get("target_price")
        latest_price = summary["latest_price"]
        if prediction["direction"] == "bullish":
            bull_count += 1
            if target_price and target_price <= latest_price:
                weight *= 0.5
            bull_score += weight
        elif prediction["direction"] == "bearish":
            bear_count += 1
            if target_price and target_price >= latest_price:
                weight *= 0.5
            bear_score += weight
    if summary["trend"] == "uptrend":
        bull_score += 0.25
    elif summary["trend"] == "downtrend":
        bear_score += 0.25
    return {
        "bull_score": round(bull_score, 3),
        "bear_score": round(bear_score, 3),
        "bull_count": bull_count,
        "bear_count": bear_count,
    }


def ai_prediction_weight(prediction: dict[str, Any], summary: dict[str, Any]) -> float:
    weight = max(0.25, min(float(prediction.get("total_score") or 50) / 100, 1.4))
    weighted_win_rate = float(prediction.get("weighted_win_rate") or 0)
    direction_accuracy = float(prediction.get("direction_accuracy") or 0)
    stability_score = float(prediction.get("stability_score") or 100)
    modification_rate = float(prediction.get("modification_rate") or 0)
    accuracy_factor = 0.85 + min(max((weighted_win_rate + direction_accuracy) / 200, 0), 1) * 0.5
    weight *= accuracy_factor
    weight *= max(0.55, min(stability_score / 100, 1.15))
    weight *= max(0.55, 1 - min(modification_rate, 100) / 180)
    confidence = prediction.get("confidence")
    if confidence == "high":
        weight *= 1.22
    elif confidence == "low":
        weight *= 0.72
    horizon_factor = {"intraday": 0.9, "short": 1.0, "medium": 0.88, "long": 0.75}.get(str(prediction.get("horizon") or ""), 0.9)
    weight *= horizon_factor
    target_price = prediction.get("target_price")
    latest_price = float(summary["latest_price"])
    if prediction.get("direction") == "bullish" and target_price and float(target_price) <= latest_price:
        weight *= 0.5
    if prediction.get("direction") == "bearish" and target_price and float(target_price) >= latest_price:
        weight *= 0.5
    return max(0.0, weight)


def build_agent_analysis_signal(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = market_summary(conn)
    predictions = pending_predictions_for_agent(conn)
    bull_score = 0.0
    bear_score = 0.0
    bull_count = 0
    bear_count = 0
    weighted_predictions: list[dict[str, Any]] = []
    for prediction in predictions:
        weight = ai_prediction_weight(prediction, summary)
        item = {
            "id": prediction.get("id"),
            "analyst_id": prediction.get("analyst_id"),
            "analyst_name": prediction.get("analyst_name"),
            "direction": prediction.get("direction"),
            "target_price": prediction.get("target_price"),
            "horizon": prediction.get("horizon"),
            "confidence": prediction.get("confidence"),
            "summary": prediction.get("summary"),
            "weight": round(weight, 4),
        }
        weighted_predictions.append(item)
        if prediction.get("direction") == "bullish":
            bull_count += 1
            bull_score += weight
        elif prediction.get("direction") == "bearish":
            bear_count += 1
            bear_score += weight
    if summary["trend"] == "uptrend":
        bull_score += 0.35
    elif summary["trend"] == "downtrend":
        bear_score += 0.35
    difference = bull_score - bear_score
    total_score = bull_score + bear_score
    threshold = max(0.55, total_score * 0.18)
    if difference >= threshold:
        decision = "bullish"
        direction = "bullish"
    elif difference <= -threshold:
        decision = "bearish"
        direction = "bearish"
    else:
        decision = "observe"
        direction = "sideways"
    confidence_score = abs(difference) / total_score if total_score else 0
    confidence = "high" if confidence_score >= 0.35 else "medium" if confidence_score >= 0.18 else "low"
    risk_notes: list[str] = []
    if float(summary.get("volatility") or 0) >= 1.6:
        risk_notes.append("波动率偏高，需谨慎解读短线方向")
    if confidence == "low":
        risk_notes.append("多空加权分差有限")
    if not predictions:
        risk_notes.append("暂无有效分析师预测")
    supporting_direction = direction if direction in {"bullish", "bearish"} else None
    supporting_predictions = [
        item for item in sorted(weighted_predictions, key=lambda value: float(value.get("weight") or 0), reverse=True)
        if supporting_direction is None or item.get("direction") == supporting_direction
    ][:8]
    # Gate MCP 增强信号（只读附加，失败不影响核心信号）
    gate_signal: dict[str, Any] = {}
    try:
        from .gate_sync import latest_btc_contract_metrics, latest_sentiment_snapshot
        gate_signal["btc_contract"] = latest_btc_contract_metrics(conn)
        gate_signal["sentiment"] = latest_sentiment_snapshot(conn)
        sentiment = gate_signal.get("sentiment", {})
        if sentiment.get("overall_sentiment") in ("extreme_fear", "fear"):
            risk_notes.append("Gate 情绪偏恐慌，注意短期下行风险")
        elif sentiment.get("overall_sentiment") in ("extreme_greed", "greed"):
            risk_notes.append("Gate 情绪偏贪婪，注意回调风险")
    except Exception:
        pass
    return {
        "decision": decision,
        "direction": direction,
        "confidence": confidence,
        "confidence_score": round(confidence_score, 4),
        "bull_score": round(bull_score, 4),
        "bear_score": round(bear_score, 4),
        "difference": round(difference, 4),
        "threshold": round(threshold, 4),
        "bull_count": bull_count,
        "bear_count": bear_count,
        "risk_notes": risk_notes,
        "supporting_predictions": supporting_predictions,
        "gate_signal": gate_signal,
    }


def run_agent(conn: sqlite3.Connection, trigger: str = "manual", focus_prediction_ids: list[int] | None = None) -> dict[str, Any]:
    from .graphs import run_agent_analysis_graph

    state = run_agent_analysis_graph(conn, trigger, focus_prediction_ids)
    return state.get("result", {})


def verify_due_predictions(conn: sqlite3.Connection) -> dict[str, Any]:
    # 对外暴露的到期预测验证入口，内部执行验证 Graph。
    from .graphs import run_prediction_verification_graph

    state = run_prediction_verification_graph(conn)
    return state.get("result", {"verified_count": 0, "items": []})


def prediction_quality_label(score: float) -> str:
    # 将综合评分映射为前端展示的质量标签。
    if score >= 0.8:
        return "high_quality_success"
    if score >= 0.6:
        return "basic_success"
    if score >= 0.4:
        return "partial"
    return "failed"


def verification_label_text(value: str | None) -> str:
    return {
        "high_quality_success": "高质量成功",
        "basic_success": "基本成功",
        "partial": "部分正确",
        "failed": "失败",
        "success": "成功",
        "bullish": "上涨",
        "bearish": "下跌",
        "sideways": "震荡",
    }.get(value or "", value or "未知")


def build_verification_report(
    prediction: dict[str, Any],
    verification: dict[str, Any],
    explanation: dict[str, Any] | None = None,
    failure_reason: dict[str, Any] | None = None,
    context_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 生成面向用户的预测验证报告，包含评分拆解和行情路径。
    score = float(verification.get("score") or verification.get("final_score") or 0)
    status = str(verification.get("status") or "unknown")
    quality_label = str(verification.get("quality_label") or prediction_quality_label(score))
    actual_direction = str(verification.get("actual_direction") or "")
    target_hit = bool(verification.get("target_hit"))
    target_distance = verification.get("target_distance_pct")
    price_change = verification.get("price_change_pct", verification.get("change"))
    summary = (explanation or {}).get("plain_language_summary")
    if not summary:
        target_text = "目标价已触达" if target_hit else "目标价未触达"
        distance_text = f"，距离目标约 {round(float(target_distance), 2)}%" if target_distance is not None else ""
        summary = (
            f"该预测验证为{verification_label_text(quality_label)}，综合得分 {round(score, 3)}。"
            f"验证窗口内 BTC 方向判定为{verification_label_text(actual_direction)}，"
            f"价格变化约 {round(float(price_change or 0), 2)}%，{target_text}{distance_text}。"
        )
    failure_summary = (failure_reason or {}).get("failure_reason_summary")
    if not failure_summary and status == "failed":
        if float(verification.get("direction_score") or 0) < 1 and not target_hit:
            failure_summary = "方向判断与验证期行情不一致，且目标价未触达。"
        elif float(verification.get("direction_score") or 0) < 1:
            failure_summary = "主要失败原因是方向判断与验证期行情不一致。"
        elif not target_hit:
            failure_summary = "主要失败原因是验证窗口内目标价未触达或接近度不足。"
        else:
            failure_summary = "综合得分低于成功阈值，需要复核时间分或修改惩罚。"
    payload = {
        "verification": verification,
        "prediction": prediction,
        "explanation": explanation,
        "failure_reason": failure_reason,
        "scoring_breakdown": {
            "direction_score": verification.get("direction_score"),
            "target_score": verification.get("target_score"),
            "time_score": verification.get("time_score"),
            "modified_penalty": verification.get("modified_penalty"),
            "final_score": score,
            "quality_label": quality_label,
        },
        "market_path": {
            "price_change_pct": price_change,
            "highest_price": verification.get("highest_price"),
            "lowest_price": verification.get("lowest_price"),
            "latest_price": verification.get("latest_price"),
            "closest_price": verification.get("closest_price"),
            "target_distance_pct": target_distance,
        },
        "context_snapshot": context_snapshot or {},
    }
    return {
        "prediction_id": verification.get("prediction_id") or prediction.get("id"),
        "plain_language_summary": summary,
        "failure_reason": failure_summary,
        "payload": payload,
    }


def save_prediction_verification_report(
    conn: sqlite3.Connection,
    prediction: dict[str, Any],
    verification: dict[str, Any],
    explanation: dict[str, Any] | None = None,
    failure_reason: dict[str, Any] | None = None,
    context_snapshot: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    # 将预测验证报告保存到 verification_reports 表。
    report = build_verification_report(prediction, verification, explanation, failure_reason, context_snapshot)
    cursor = conn.execute(
        """
        INSERT INTO verification_reports (prediction_id, plain_language_summary, failure_reason, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            report["prediction_id"],
            report["plain_language_summary"],
            report["failure_reason"],
            json.dumps(report["payload"], ensure_ascii=False, default=str),
            utc_now(),
        ),
    )
    if commit:
        conn.commit()
    report["id"] = int(cursor.lastrowid)
    report["data"] = report["payload"]
    return report


def verify_prediction(conn: sqlite3.Connection, prediction: dict[str, Any]) -> dict[str, Any]:
    # 根据验证窗口内行情计算方向分、目标价分、时间分和最终状态。
    latest = latest_market(conn)
    base_price = float(prediction["current_price"])
    latest_price = float(latest["close"])
    change = (latest_price - base_price) / base_price if base_price else 0
    threshold = float(get_setting_value(conn, "prediction.direction_threshold", 0.01))
    target_tolerance = float(get_setting_value(conn, "prediction.target_tolerance", 0.05))
    if change > threshold:
        actual_direction = "bullish"
    elif change < -threshold:
        actual_direction = "bearish"
    else:
        actual_direction = "sideways"
    direction_score = 1.0 if actual_direction == prediction["direction"] else 0.0
    market_rows = conn.execute(
        """
        SELECT * FROM market_data
        WHERE open_time >= ? AND open_time <= ?
        ORDER BY open_time ASC
        """,
        (prediction["created_at"], utc_now()),
    ).fetchall()
    if market_rows:
        highest = max(float(row["high"]) for row in market_rows)
        lowest = min(float(row["low"]) for row in market_rows)
    else:
        highest = float(latest["high"])
        lowest = float(latest["low"])
    target_price = prediction.get("target_price")
    closest_price = latest_price
    target_distance_pct = None
    if target_price is None:
        target_score = 0.5
        target_hit = False
    elif prediction["direction"] == "bullish":
        target_hit = highest >= float(target_price)
        closest_price = highest
        distance = abs(float(target_price) - closest_price) / float(target_price)
        target_distance_pct = distance * 100
        target_score = 1.0 if target_hit else max(0.0, 1 - distance / target_tolerance)
    elif prediction["direction"] == "bearish":
        target_hit = lowest <= float(target_price)
        closest_price = lowest
        distance = abs(float(target_price) - closest_price) / float(target_price)
        target_distance_pct = distance * 100
        target_score = 1.0 if target_hit else max(0.0, 1 - distance / target_tolerance)
    else:
        target_hit = abs(change) <= threshold
        closest_price = latest_price
        target_distance_pct = abs(change) * 100
        target_score = 1.0 if target_hit else 0.2
    verification_time = parse_dt(prediction["verification_time"])
    time_score = 1.0 if verification_time <= parse_dt(utc_now()) else 0.0
    counted_change = conn.execute(
        """
        SELECT id
        FROM prediction_versions
        WHERE prediction_id = ?
          AND change_type != 'manual_correction'
        LIMIT 1
        """,
        (prediction["id"],),
    ).fetchone()
    modified_penalty = 0.2 if counted_change else 0.0
    score = max(0.0, direction_score * 0.45 + target_score * 0.4 + time_score * 0.15 - modified_penalty)
    success = score >= 0.6
    status = "success" if success else "failed"
    quality_label = prediction_quality_label(score)
    conn.execute(
        """
        UPDATE predictions
        SET status = ?, is_success = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, 1 if success else 0, utc_now(), prediction["id"]),
    )
    payload = {
        "prediction": prediction,
        "actual_direction": actual_direction,
        "direction_score": round(direction_score, 3),
        "target_hit": target_hit,
        "target_score": round(target_score, 3),
        "time_score": round(time_score, 3),
        "modified_penalty": round(modified_penalty, 3),
        "price_change_pct": round(change * 100, 4),
        "closest_price": round(closest_price, 2),
        "target_distance_pct": round(target_distance_pct, 4) if target_distance_pct is not None else None,
        "quality_label": quality_label,
        "highest_price": round(highest, 2),
        "lowest_price": round(lowest, 2),
    }
    conn.execute(
        """
        INSERT INTO verification_results (
            prediction_id, base_price, latest_price, highest_price, lowest_price, actual_direction,
            direction_score, target_hit, target_score, time_score, modified_penalty, final_score,
            price_change_pct, closest_price, target_distance_pct, quality_label,
            status, verification_window_start, verification_window_end, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction["id"],
            round(base_price, 2),
            round(latest_price, 2),
            round(highest, 2),
            round(lowest, 2),
            actual_direction,
            round(direction_score, 3),
            1 if target_hit else 0,
            round(target_score, 3),
            round(time_score, 3),
            round(modified_penalty, 3),
            round(score, 3),
            round(change * 100, 4),
            round(closest_price, 2),
            round(target_distance_pct, 4) if target_distance_pct is not None else None,
            quality_label,
            status,
            prediction["created_at"],
            utc_now(),
            json.dumps(payload, ensure_ascii=False),
            utc_now(),
        ),
    )
    recompute_analyst_metrics(conn, int(prediction["analyst_id"]))
    return {
        "prediction_id": prediction["id"],
        "status": status,
        "score": round(score, 3),
        "actual_direction": actual_direction,
        "target_hit": target_hit,
        "latest_price": latest_price,
        "change": round(change * 100, 2),
        "highest_price": round(highest, 2),
        "lowest_price": round(lowest, 2),
        "direction_score": round(direction_score, 3),
        "target_score": round(target_score, 3),
        "time_score": round(time_score, 3),
        "modified_penalty": round(modified_penalty, 3),
        "price_change_pct": round(change * 100, 4),
        "closest_price": round(closest_price, 2),
        "target_distance_pct": round(target_distance_pct, 4) if target_distance_pct is not None else None,
        "quality_label": quality_label,
    }


def analyst_horizon_rate(rows: list[dict[str, Any]], horizon: str) -> float:
    horizon_rows = [item for item in rows if item.get("horizon") == horizon]
    if not horizon_rows:
        return 0.0
    return sum(float(item.get("final_score") or 0) for item in horizon_rows) / len(horizon_rows) * 100


def recompute_analyst_metrics(conn: sqlite3.Connection, analyst_id: int) -> None:
    predictions_stats = conn.execute(
        """
        SELECT
            COUNT(DISTINCT p.id) AS prediction_count,
            COUNT(DISTINCT CASE WHEN pv.change_type != 'manual_correction' THEN pv.prediction_id END) AS modified
        FROM predictions p
        LEFT JOIN prediction_versions pv ON pv.prediction_id = p.id
        WHERE p.analyst_id = ?
        """,
        (analyst_id,),
    ).fetchone()
    verification_rows = rows_to_dicts(
        conn.execute(
            """
            SELECT
                vr.final_score,
                vr.direction_score,
                vr.target_score,
                vr.target_hit,
                vr.status,
                p.horizon
            FROM verification_results vr
            JOIN predictions p ON p.id = vr.prediction_id
            WHERE p.analyst_id = ?
              AND vr.id = (
                  SELECT MAX(vr2.id)
                  FROM verification_results vr2
                  WHERE vr2.prediction_id = vr.prediction_id
              )
            """,
            (analyst_id,),
        ).fetchall()
    )
    verified = len(verification_rows)
    wins = sum(1 for item in verification_rows if float(item.get("final_score") or 0) >= 0.6)
    direction_wins = sum(1 for item in verification_rows if float(item.get("direction_score") or 0) >= 1)
    target_hits = sum(1 for item in verification_rows if int(item.get("target_hit") or 0) == 1)
    weighted_win_rate = (sum(float(item.get("final_score") or 0) for item in verification_rows) / verified * 100) if verified else 0
    average_prediction_score = weighted_win_rate
    hard_win_rate = (wins / verified * 100) if verified else 0
    direction_win_rate = (direction_wins / verified * 100) if verified else 0
    target_hit_rate = (target_hits / verified * 100) if verified else 0
    prediction_count = int(predictions_stats["prediction_count"] or 0)
    modified = int(predictions_stats["modified"] or 0)
    modification_rate = (modified / prediction_count * 100) if prediction_count else 0
    stability_score = max(0, 100 - modification_rate)
    if verified:
        score = weighted_win_rate * 0.5 + direction_win_rate * 0.2 + target_hit_rate * 0.15 + stability_score * 0.15
    else:
        score = 50 + stability_score * 0.1
    score = max(0, min(100, score))
    conn.execute(
        """
        UPDATE analysts
        SET total_score = ?, direction_win_rate = ?, target_hit_rate = ?, stability_score = ?,
            hard_win_rate = ?, weighted_win_rate = ?, direction_accuracy = ?, target_accuracy = ?,
            modification_rate = ?, intraday_win_rate = ?, short_win_rate = ?, medium_win_rate = ?,
            long_win_rate = ?, average_prediction_score = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            round(score, 2),
            round(direction_win_rate, 2),
            round(target_hit_rate, 2),
            round(stability_score, 2),
            round(hard_win_rate, 2),
            round(weighted_win_rate, 2),
            round(direction_win_rate, 2),
            round(target_hit_rate, 2),
            round(modification_rate, 2),
            round(analyst_horizon_rate(verification_rows, "intraday"), 2),
            round(analyst_horizon_rate(verification_rows, "short"), 2),
            round(analyst_horizon_rate(verification_rows, "medium"), 2),
            round(analyst_horizon_rate(verification_rows, "long"), 2),
            round(average_prediction_score, 2),
            utc_now(),
            analyst_id,
        ),
    )
    conn.commit()


def latest_agent_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    result = row_to_dict(row)
    if result:
        result["output"] = json.loads(result.get("output_snapshot") or "{}")
    return result


def dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = market_summary(conn)
    pending_count = conn.execute("SELECT COUNT(*) AS count FROM predictions WHERE status = 'pending'").fetchone()["count"]
    due_count = conn.execute(
        "SELECT COUNT(*) AS count FROM predictions WHERE status = 'pending' AND verification_time <= ?",
        (utc_now(),),
    ).fetchone()["count"]
    analysts = list_analysts(conn)[:5]
    return {
        "market": summary,
        "pending_prediction_count": pending_count,
        "due_prediction_count": due_count,
        "latest_agent_run": latest_agent_run(conn),
        "top_analysts": analysts,
    }


def record_scheduled_task_start(conn: sqlite3.Connection, task_name: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO scheduled_task_runs (task_name, status, result, started_at)
        VALUES (?, 'running', '{}', ?)
        """,
        (task_name, utc_now()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_scheduled_task_finish(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE scheduled_task_runs
        SET status = ?, result = ?, error_message = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, json.dumps(result or {}, ensure_ascii=False), error_message, utc_now(), run_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)).fetchone()
    item = row_to_dict(row) or {}
    if item:
        item["data"] = json.loads(item.get("result") or "{}")
    return item


def list_scheduled_task_runs(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM scheduled_task_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["data"] = json.loads(item.get("result") or "{}")
    return items


def run_scheduled_market_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .database import sync_market_intervals

    intervals = get_setting_value(conn, "market.intervals", ["1h", "4h", "1d"])
    if isinstance(intervals, str):
        intervals = [item.strip() for item in intervals.split(",") if item.strip()]
    symbol = str(get_setting_value(conn, "market.symbol", "BTCUSDT"))
    return sync_market_intervals(conn, list(intervals), symbol=symbol, replace=False, market_type="perpetual")


def run_scheduled_verify_due(conn: sqlite3.Connection) -> dict[str, Any]:
    return verify_due_predictions(conn)


def run_scheduled_daily_report(conn: sqlite3.Connection) -> dict[str, Any]:
    return create_daily_report(conn)


def run_scheduled_gate_btc_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .gate_sync import sync_btc_contract_metrics
    return sync_btc_contract_metrics(conn)


def run_scheduled_gate_news_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .content_sync import sync_gate_news
    return sync_gate_news(conn)


def run_scheduled_gate_square_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .content_sync import sync_gate_square_hot
    return sync_gate_square_hot(conn)


def run_scheduled_sentiment_build(conn: sqlite3.Connection) -> dict[str, Any]:
    from .gate_sync import build_market_sentiment_snapshot
    return build_market_sentiment_snapshot(conn)


def run_scheduled_memory_compact(conn: sqlite3.Connection) -> dict[str, Any]:
    from .gate_sync import compact_market_memories
    return compact_market_memories(conn)


def run_scheduled_gate_square_user_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .content_sync import sync_gate_square_user_opinions
    return sync_gate_square_user_opinions(conn)


def run_scheduled_gate_info_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    from .gate_mcp import sync_gate_info
    return sync_gate_info(conn)


def run_scheduled_task(conn: sqlite3.Connection, task_name: str) -> dict[str, Any]:
    run_id = record_scheduled_task_start(conn, task_name)
    try:
        if task_name == "market_sync":
            result = run_scheduled_market_sync(conn)
        elif task_name == "verify_due":
            result = run_scheduled_verify_due(conn)
        elif task_name == "daily_report":
            result = run_scheduled_daily_report(conn)
        elif task_name == "gate_btc_contract_sync":
            result = run_scheduled_gate_btc_sync(conn)
        elif task_name == "gate_news_sync":
            result = run_scheduled_gate_news_sync(conn)
        elif task_name == "gate_square_hot_sync":
            result = run_scheduled_gate_square_sync(conn)
        elif task_name == "market_sentiment_build":
            result = run_scheduled_sentiment_build(conn)
        elif task_name == "market_memory_compact":
            result = run_scheduled_memory_compact(conn)
        elif task_name == "gate_square_user_sync":
            result = run_scheduled_gate_square_user_sync(conn)
        elif task_name == "gate_info_sync":
            result = run_scheduled_gate_info_sync(conn)
        elif task_name == "mock_trade_sync":
            from .mock_trade import sync_mock_account_state
            result = sync_mock_account_state(conn)
        else:
            raise ValueError(f"unknown scheduled task: {task_name}")
    except Exception as exc:
        return record_scheduled_task_finish(conn, run_id, "failed", {}, str(exc))
    return record_scheduled_task_finish(conn, run_id, "success", result, None)


def list_agent_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["market_summary"] = localize_display_payload(item.get("market_summary"))
        item["opinion_summary"] = localize_display_payload(item.get("opinion_summary"))
        item["risk"] = localize_display_payload(item.get("risk"))
        item["output"] = localize_display_payload(json.loads(item.get("output_snapshot") or "{}"))
    return items


def list_agent_node_runs(conn: sqlite3.Connection, agent_run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM agent_node_runs
        WHERE agent_run_id = ?
        ORDER BY id ASC
        """,
        (agent_run_id,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["input"] = json.loads(item.get("input_snapshot") or "{}")
        item["output"] = localize_display_payload(json.loads(item.get("output_snapshot") or "{}"))
    return items


def list_agent_stream_events(conn: sqlite3.Connection, after_id: int = 0, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT anr.*, ar.trigger, ar.decision, ar.risk
        FROM agent_node_runs anr
        LEFT JOIN agent_runs ar ON ar.id = anr.agent_run_id
        WHERE anr.id > ?
        ORDER BY anr.id ASC
        LIMIT ?
        """,
        (after_id, limit),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows_to_dicts(rows):
        output = localize_display_payload(json.loads(row.get("output_snapshot") or "{}"))
        data = output.get("data") or {}
        summary = ""
        if isinstance(data, dict):
            summary = (
                data.get("analysis_event")
                or data.get("decision_label")
                or data.get("risk")
                or data.get("market_summary")
                or data.get("summary")
                or data.get("plain_language_summary")
                or ""
            )
        if not summary:
            summary = "节点输出已更新"
        events.append(
            {
                "id": row.get("id"),
                "agent_run_id": row.get("agent_run_id"),
                "graph_name": row.get("graph_name"),
                "node_name": row.get("node_name"),
                "status": row.get("status"),
                "message": summary,
                "output": output,
                "created_at": row.get("finished_at") or row.get("started_at"),
            }
        )
    return events


def list_human_reviews(conn: sqlite3.Connection, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM human_review_items
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["suggested_question"] = localize_display_payload(item.get("suggested_question"))
        item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
    return items


def list_verification_results(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT vr.*, p.summary, a.name AS analyst_name
        FROM verification_results vr
        JOIN predictions p ON p.id = vr.prediction_id
        JOIN analysts a ON a.id = p.analyst_id
        ORDER BY vr.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["summary"] = localize_display_payload(item.get("summary"))
        item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
        item["report"] = get_latest_verification_report(conn, int(item["prediction_id"]), item)
    return items


def get_latest_verification_report(conn: sqlite3.Connection, prediction_id: int, verification: dict[str, Any] | None = None) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM verification_reports
        WHERE prediction_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (prediction_id,),
    ).fetchone()
    report = row_to_dict(row) or {}
    if report:
        report["plain_language_summary"] = localize_display_payload(report.get("plain_language_summary"))
        report["failure_reason"] = localize_display_payload(report.get("failure_reason"))
        report["data"] = localize_display_payload(json.loads(report.get("payload") or "{}"))
        return report
    if not verification:
        return {}
    prediction_row = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
    return build_verification_report(row_to_dict(prediction_row) or {}, verification)


def get_verification_result(conn: sqlite3.Connection, prediction_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT vr.*, p.summary, a.name AS analyst_name
        FROM verification_results vr
        JOIN predictions p ON p.id = vr.prediction_id
        JOIN analysts a ON a.id = p.analyst_id
        WHERE vr.prediction_id = ?
        ORDER BY vr.id DESC
        LIMIT 1
        """,
        (prediction_id,),
    ).fetchone()
    item = row_to_dict(row) or {}
    if item:
        item["summary"] = localize_display_payload(item.get("summary"))
        item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
        item["report"] = get_latest_verification_report(conn, prediction_id, item)
    return item


def get_prediction_replay(conn: sqlite3.Connection, prediction_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.*, a.name AS analyst_name, a.total_score, ro.content AS raw_content, ro.source_url, ro.published_at, ro.created_at AS raw_created_at
        FROM predictions p
        JOIN analysts a ON a.id = p.analyst_id
        JOIN raw_opinions ro ON ro.id = p.raw_opinion_id
        WHERE p.id = ?
        """,
        (prediction_id,),
    ).fetchone()
    prediction = row_to_dict(row) or {}
    if not prediction:
        return {}
    review_row = conn.execute(
        "SELECT id FROM human_review_items WHERE raw_opinion_id = ? ORDER BY id DESC LIMIT 1",
        (prediction["raw_opinion_id"],),
    ).fetchone()
    versions = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM prediction_versions
            WHERE prediction_id = ? OR new_prediction_id = ?
            ORDER BY created_at ASC
            """,
            (prediction_id, prediction_id),
        ).fetchall()
    )
    for item in versions:
        item["reason"] = localize_display_payload(item.get("reason"))
        item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
    verification_result = get_verification_result(conn, prediction_id)
    verification_report = get_latest_verification_report(conn, prediction_id, verification_result)
    raw_opinion = {
        "id": prediction.get("raw_opinion_id"),
        "content": prediction.get("raw_content"),
        "source_url": prediction.get("source_url"),
        "published_at": prediction.get("published_at"),
        "created_at": prediction.get("raw_created_at"),
    }
    analyst = {
        "id": prediction.get("analyst_id"),
        "name": prediction.get("analyst_name"),
        "total_score": prediction.get("total_score"),
    }
    return {
        "prediction": prediction,
        "raw_opinion": raw_opinion,
        "analyst": analyst,
        "review": get_human_review_detail(conn, int(review_row["id"])) if review_row else None,
        "versions": versions,
        "verification_result": verification_result,
        "verification_report": verification_report,
    }


def get_analyst_replay(conn: sqlite3.Connection, analyst_id: int, limit: int = 50) -> dict[str, Any]:
    analyst_row = conn.execute("SELECT * FROM analysts WHERE id = ?", (analyst_id,)).fetchone()
    analyst = row_to_dict(analyst_row) or {}
    if not analyst:
        return {}
    predictions = rows_to_dicts(
        conn.execute(
            """
            SELECT p.*
            FROM predictions p
            WHERE p.analyst_id = ?
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (analyst_id, limit),
        ).fetchall()
    )
    prediction_ids = [int(item["id"]) for item in predictions]
    verification_map: dict[int, dict[str, Any]] = {}
    version_count_map: dict[int, int] = {}
    if prediction_ids:
        placeholders = ",".join("?" for _ in prediction_ids)
        verification_rows = rows_to_dicts(
            conn.execute(
                f"SELECT * FROM verification_results WHERE prediction_id IN ({placeholders}) ORDER BY id DESC",
                prediction_ids,
            ).fetchall()
        )
        for item in verification_rows:
            if int(item["prediction_id"]) not in verification_map:
                item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
                verification_map[int(item["prediction_id"])] = item
        version_rows = conn.execute(
            f"SELECT prediction_id, COUNT(*) AS count FROM prediction_versions WHERE prediction_id IN ({placeholders}) GROUP BY prediction_id",
            prediction_ids,
        ).fetchall()
        version_count_map = {int(row["prediction_id"]): int(row["count"]) for row in version_rows}
    for item in predictions:
        item["verification_result"] = verification_map.get(int(item["id"]))
        item["version_count"] = version_count_map.get(int(item["id"]), 0)
    return {
        "analyst": analyst,
        "predictions": predictions,
    }


def get_agent_run_replay(conn: sqlite3.Connection, agent_run_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (agent_run_id,)).fetchone()
    agent_run = row_to_dict(row) or {}
    if not agent_run:
        return {}
    agent_run["input"] = json.loads(agent_run.get("input_snapshot") or "{}")
    agent_run["output"] = json.loads(agent_run.get("output_snapshot") or "{}")
    nodes = list_agent_node_runs(conn, agent_run_id)
    focus_prediction_ids = list(agent_run.get("input", {}).get("focus_prediction_ids") or [])
    focus_predictions: list[dict[str, Any]] = []
    if focus_prediction_ids:
        placeholders = ",".join("?" for _ in focus_prediction_ids)
        focus_predictions = rows_to_dicts(
            conn.execute(
                f"SELECT p.*, a.name AS analyst_name FROM predictions p JOIN analysts a ON a.id = p.analyst_id WHERE p.id IN ({placeholders})",
                focus_prediction_ids,
            ).fetchall()
        )
    return {
        "agent_run": agent_run,
        "nodes": nodes,
        "focus_predictions": focus_predictions,
    }


def resolve_human_review(conn: sqlite3.Connection, review_id: int) -> dict[str, Any]:
    conn.execute(
        "UPDATE human_review_items SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (utc_now(), review_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM human_review_items WHERE id = ?", (review_id,)).fetchone()
    result = row_to_dict(row) or {}
    if result:
        result["data"] = localize_display_payload(json.loads(result.get("payload") or "{}"))
    return result


def create_daily_report(conn: sqlite3.Connection) -> dict[str, Any]:
    from .graphs import run_daily_report_graph

    state = run_daily_report_graph(conn)
    return state.get("result", {})


def list_reports(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agent_reports ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["title"] = localize_display_payload(item.get("title"))
        item["data"] = localize_display_payload(json.loads(item.get("payload") or "{}"))
    return items
