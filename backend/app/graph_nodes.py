from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta
from typing import Any

from .database import parse_dt, row_to_dict, rows_to_dicts, utc_now
from .llm_client import call_llm_node, standard_node_output


def as_json(value: Any) -> str:
    # 统一 JSON 序列化方式，保证中文不被转义。
    return json.dumps(value, ensure_ascii=False, default=str)


def payload_value(payload: Any, name: str, default: Any = None) -> Any:
    # 兼容 Pydantic 模型和普通对象读取字段。
    return getattr(payload, name, default)


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    # 记录节点输入时排除数据库连接，避免不可序列化。
    return {key: value for key, value in state.items() if key != "conn"}


# 这些键对应枚举、ID 或时间，不做展示文本翻译。
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
    "should_execute",
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


# 本地兜底翻译表，用于模型不可用时翻译常见展示短语。
LOCAL_TRANSLATION_MAP = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
    "success": "成功",
    "failed": "失败",
    "partial": "部分正确",
    "bullish": "看多",
    "bearish": "看空",
    "sideways": "震荡",
    "open_long": "开多",
    "open_short": "开空",
    "observe": "观望",
    "wait for ai output": "等待 AI 输出",
    "node output updated": "节点输出已更新",
}


def has_chinese_text(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def has_english_text(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", value))


def local_translate_text(value: str) -> str:
    text = value.strip()
    translated = LOCAL_TRANSLATION_MAP.get(text.lower())
    if translated:
        return translated
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
        "trend": "趋势",
        "uptrend": "上升趋势",
        "downtrend": "下降趋势",
        "neutral": "中性",
        "volatility": "波动率",
        "funding rate": "资金费率",
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


def translate_display_payload(value: Any, key: str = "") -> Any:
    # 递归翻译面向用户展示的英文文本。
    if isinstance(value, dict):
        return {item_key: translate_display_payload(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [translate_display_payload(item, key) for item in value]
    if isinstance(value, str):
        if key in DISPLAY_TEXT_SKIP_KEYS or not has_english_text(value) or has_chinese_text(value):
            return value
        return local_translate_text(value)
    return value


def payload_has_translatable_english(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        return any(payload_has_translatable_english(item_value, item_key) for item_key, item_value in value.items())
    if isinstance(value, list):
        return any(payload_has_translatable_english(item, key) for item in value)
    if isinstance(value, str):
        return key not in DISPLAY_TEXT_SKIP_KEYS and has_english_text(value) and not has_chinese_text(value)
    return False


def collect_display_payload(state: dict[str, Any]) -> dict[str, Any]:
    # 从当前 Graph 状态中抽取可能需要前端展示的字段。
    return {
        "parsed_predictions": state.get("parsed_predictions", []),
        "signal": state.get("signal", {}),
        "risk": state.get("risk"),
        "decision_label": state.get("decision_label"),
        "trade_signal_explanation": output_data(state, "trade_signal_explanation"),
        "verification_explanation": output_data(state, "verification_explanation"),
        "failure_reason": output_data(state, "failure_reason"),
        "report": state.get("report", {}),
    }


def replace_node_data(state: dict[str, Any], node_name: str, data: Any) -> None:
    # 替换已记录节点输出中的 data，保持回放内容与展示内容一致。
    node_outputs = state.get("node_outputs") or {}
    node_output = node_outputs.get(node_name)
    if isinstance(node_output, dict):
        node_output["data"] = data


def apply_display_payload(state: dict[str, Any], payload: dict[str, Any]) -> None:
    # 将翻译后的展示字段写回 Graph 状态。
    if payload.get("parsed_predictions"):
        state["parsed_predictions"] = payload["parsed_predictions"]
    if payload.get("signal"):
        state["signal"] = payload["signal"]
    if payload.get("risk") is not None:
        state["risk"] = payload["risk"]
    if payload.get("decision_label") is not None:
        state["decision_label"] = payload["decision_label"]
    if payload.get("report"):
        state["report"] = payload["report"]
    for node_name in ("trade_signal_explanation", "verification_explanation", "failure_reason"):
        if node_name in payload:
            replace_node_data(state, node_name, payload[node_name])


def record_node(state: dict[str, Any], node_name: str, output: dict[str, Any], input_snapshot: dict[str, Any] | None = None) -> None:
    # 将每个节点的输入输出落库，供前端实时流和节点回放使用。
    conn: sqlite3.Connection = state["conn"]
    try:
        cursor = conn.execute(
            """
            INSERT INTO agent_node_runs (
                agent_run_id, graph_name, node_name, status, input_snapshot, output_snapshot,
                error_message, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.get("agent_run_id"),
                state.get("graph_name", "unknown"),
                node_name,
                "success" if output.get("success") else "failed",
                as_json(input_snapshot or compact_state(state)),
                as_json(output),
                "；".join(output.get("errors") or []),
                utc_now(),
                utc_now(),
            ),
        )
        state.setdefault("node_run_ids", []).append(cursor.lastrowid)
        conn.commit()
    except sqlite3.OperationalError:
        pass


def apply_node_output(state: dict[str, Any], node_name: str, output: dict[str, Any], input_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    # 合并节点输出、收集警告错误，并持久化节点运行记录。
    node_outputs = dict(state.get("node_outputs") or {})
    node_outputs[node_name] = output
    state["node_outputs"] = node_outputs
    if output.get("errors"):
        state.setdefault("errors", []).extend(output["errors"])
    if output.get("warnings"):
        state.setdefault("warnings", []).extend(output["warnings"])
    record_node(state, node_name, output, input_snapshot)
    return state


def output_data(state: dict[str, Any], node_name: str) -> dict[str, Any]:
    return ((state.get("node_outputs") or {}).get(node_name) or {}).get("data") or {}


def llm_or_fallback(
    state: dict[str, Any],
    graph_name: str,
    node_name: str,
    values: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    # 优先调用 LLM 节点；失败时返回规则兜底数据，保证流程可继续。
    try:
        output = call_llm_node(graph_name, node_name, values)
    except Exception as exc:
        output = standard_node_output(node_name, success=False, errors=[str(exc)])
    if output.get("success"):
        return output
    return standard_node_output(
        node_name,
        data=fallback,
        success=True,
        warnings=[f"LLM 节点失败，已使用规则回退：{'; '.join(output.get('errors') or [])}"],
    )


def display_translation_node(state: dict[str, Any]) -> dict[str, Any]:
    # 对 Graph 输出中面向用户的英文文本做中文化处理。
    display_payload = translate_display_payload(collect_display_payload(state))
    if payload_has_translatable_english(display_payload):
        output = llm_or_fallback(
            state,
            str(state.get("graph_name") or "opinion_ingestion"),
            "display_translation",
            {"display_payload": display_payload},
            {"translated_payload": display_payload},
        )
        translated = output.get("data", {}).get("translated_payload") or display_payload
    else:
        translated = display_payload
        output = standard_node_output("display_translation", {"translated_payload": translated, "translated": False})
    if isinstance(translated, dict):
        apply_display_payload(state, translated)
    return apply_node_output(state, "display_translation", output, {"display_payload": display_payload})


def known_analysts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, source FROM analysts ORDER BY updated_at DESC LIMIT 100").fetchall()
    return rows_to_dicts(rows)


def normalize_direction(value: Any) -> str:
    text = str(value or "unknown").lower()
    if text in {"bullish", "long", "up"}:
        return "bullish"
    if text in {"bearish", "short", "down"}:
        return "bearish"
    if text in {"neutral", "range", "sideways"}:
        return "sideways"
    return "sideways"


def normalize_horizon(value: Any) -> str:
    text = str(value or "short").lower()
    if text in {"scalp", "intraday"}:
        return "intraday"
    if text in {"short", "short_term"}:
        return "short"
    if text in {"medium", "mid_term"}:
        return "medium"
    if text in {"long", "long_term"}:
        return "long"
    return "short"


def normalize_confidence(value: Any) -> str:
    text = str(value or "medium").lower()
    if text in {"high", "medium", "low"}:
        return text
    return "medium"


def normalize_prediction(item: dict[str, Any], current_price: float) -> dict[str, Any]:
    # 将 LLM 或规则解析出的预测统一整理成入库字段。
    target = item.get("target_price")
    if target is None and item.get("target_price_min") is not None and item.get("target_price_max") is not None:
        target = (float(item["target_price_min"]) + float(item["target_price_max"])) / 2
    direction = normalize_direction(item.get("direction"))
    horizon = normalize_horizon(item.get("horizon"))
    confidence = normalize_confidence(item.get("confidence") or item.get("analyst_confidence_level"))
    target_price = round(float(target), 2) if target not in (None, "", "null") else None
    summary = item.get("summary") or item.get("evidence_text") or f"{horizon} {direction}"
    if target_price:
        summary = f"{summary}，目标价 {target_price}"
    return {
        "direction": direction,
        "target_price": target_price,
        "horizon": horizon,
        "confidence": confidence,
        "summary": str(summary),
        "verification_time": None,
        "needs_user_confirmation": bool(item.get("needs_user_confirmation", False)),
        "evidence_text": item.get("evidence_text"),
        "current_price": current_price,
    }


def prediction_horizon_rank(prediction: dict[str, Any]) -> tuple[int, int, int]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}.get(str(prediction.get("confidence") or "medium"), 2)
    has_target = 1 if prediction.get("target_price") not in (None, "", "null") else 0
    summary_length = len(str(prediction.get("summary") or prediction.get("evidence_text") or ""))
    return confidence_rank, has_target, summary_length


def dedupe_predictions_by_horizon_direction(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_horizon_direction: dict[tuple[str, str], dict[str, Any]] = {}
    for prediction in predictions:
        horizon = normalize_horizon(prediction.get("horizon"))
        direction = normalize_direction(prediction.get("direction"))
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


def text_cleaning_node(state: dict[str, Any]) -> dict[str, Any]:
    payload = state["payload"]
    content = str(payload_value(payload, "content", "")).strip()
    fallback = {"cleaned_text": content, "removed_noise_summary": "规则回退：保留原文", "has_price_view": True, "language": "zh"}
    output = llm_or_fallback(state, "opinion_ingestion", "text_cleaning", {"raw_text": content}, fallback)
    return apply_node_output(state, "text_cleaning", output, {"raw_text": content})


def btc_relevance_node(state: dict[str, Any]) -> dict[str, Any]:
    cleaned_text = output_data(state, "text_cleaning").get("cleaned_text") or payload_value(state["payload"], "content", "")
    lower = str(cleaned_text).lower()
    fallback = {
        "is_btc_related": "btc" in lower or "比特币" in lower or "大饼" in lower or True,
        "contains_price_prediction": True,
        "contains_trade_plan": False,
        "contains_market_commentary_only": False,
        "btc_relevant_text": cleaned_text,
        "irrelevant_assets": [],
        "reason": "规则回退：按用户提交的 BTC 观点处理",
    }
    output = llm_or_fallback(state, "opinion_ingestion", "btc_relevance", {"cleaned_text": cleaned_text}, fallback)
    return apply_node_output(state, "btc_relevance", output, {"cleaned_text": cleaned_text})


def analyst_identification_node(state: dict[str, Any]) -> dict[str, Any]:
    payload = state["payload"]
    cleaned_text = output_data(state, "text_cleaning").get("cleaned_text") or payload_value(payload, "content", "")
    submitted = payload_value(payload, "analyst_name", "未命名分析师")
    fallback = {
        "analyst_detected": bool(str(submitted).strip()),
        "analyst_name": str(submitted).strip() or "未命名分析师",
        "matched_existing_analyst_id": None,
        "possible_aliases": [],
        "source_platform": "unknown",
        "needs_user_confirmation": not bool(str(submitted).strip()),
        "reason": "规则回退：采用用户填写的分析师名称",
    }
    values = {"cleaned_text": cleaned_text, "submitted_analyst_name": submitted, "known_analysts": known_analysts(state["conn"])}
    output = llm_or_fallback(state, "opinion_ingestion", "analyst_identification", values, fallback)
    return apply_node_output(state, "analyst_identification", output, values)


def publish_time_extraction_node(state: dict[str, Any]) -> dict[str, Any]:
    payload = state["payload"]
    submitted = payload_value(payload, "published_at", None)
    fallback = {
        "published_at": submitted or utc_now(),
        "time_type": "explicit" if submitted else "missing",
        "original_time_expression": submitted,
        "needs_user_confirmation": False,
    }
    values = {
        "current_time": utc_now(),
        "cleaned_text": output_data(state, "text_cleaning").get("cleaned_text") or payload_value(payload, "content", ""),
        "submitted_published_at": submitted,
    }
    output = llm_or_fallback(state, "opinion_ingestion", "publish_time_extraction", values, fallback)
    return apply_node_output(state, "publish_time_extraction", output, values)


def opinion_summary_node(state: dict[str, Any]) -> dict[str, Any]:
    text = output_data(state, "btc_relevance").get("btc_relevant_text") or payload_value(state["payload"], "content", "")
    fallback = {"summary": text[:220], "key_points": [text[:220]], "mentioned_levels": {}, "risk_notes": []}
    output = llm_or_fallback(state, "opinion_ingestion", "opinion_summary", {"btc_relevant_text": text}, fallback)
    return apply_node_output(state, "opinion_summary", output, {"btc_relevant_text": text})


def prediction_extraction_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 先尝试 LLM 抽取预测，失败或为空时使用本地中文规则解析兜底。
    text = output_data(state, "btc_relevance").get("btc_relevant_text") or payload_value(state["payload"], "content", "")
    rule_predictions = rules.parse_opinion(str(text), float(state["current_price"]))
    fallback = {"predictions": rule_predictions, "unverifiable_statements": []}
    output = llm_or_fallback(state, "opinion_ingestion", "prediction_extraction", {"btc_relevant_text": text, "current_price": state["current_price"]}, fallback)
    predictions = [normalize_prediction(item, float(state["current_price"])) for item in output.get("data", {}).get("predictions", []) if isinstance(item, dict)]
    if not predictions:
        predictions = [normalize_prediction(item, float(state["current_price"])) for item in rule_predictions]
    predictions = dedupe_predictions_by_horizon(predictions)
    state["parsed_predictions"] = predictions
    output["data"]["predictions"] = predictions
    return apply_node_output(state, "prediction_extraction", output, {"btc_relevant_text": text})


def time_normalization_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    published_at = output_data(state, "publish_time_extraction").get("published_at") or utc_now()
    normalized = []
    for prediction in state.get("parsed_predictions", []):
        horizon = normalize_horizon(prediction.get("horizon"))
        verification_time = (parse_dt(published_at) + timedelta(days=rules.horizon_days(horizon))).isoformat()
        item = {**prediction, "horizon": horizon, "verification_time": verification_time}
        normalized.append(item)
    normalized = dedupe_predictions_by_horizon(normalized)
    state["parsed_predictions"] = normalized
    output = standard_node_output("time_normalization", {"predictions": normalized})
    return apply_node_output(state, "time_normalization", output, {"published_at": published_at, "predictions": normalized})


def confidence_parsing_node(state: dict[str, Any]) -> dict[str, Any]:
    predictions = []
    for prediction in state.get("parsed_predictions", []):
        prediction["confidence"] = normalize_confidence(prediction.get("confidence"))
        predictions.append(prediction)
    predictions = dedupe_predictions_by_horizon(predictions)
    state["parsed_predictions"] = predictions
    output = standard_node_output("confidence_parsing", {"predictions": predictions})
    return apply_node_output(state, "confidence_parsing", output, {"predictions": predictions})


def data_anomaly_check_node(state: dict[str, Any]) -> dict[str, Any]:
    current_price = float(state["current_price"])
    details: list[str] = []
    for prediction in state.get("parsed_predictions", []):
        target = prediction.get("target_price")
        if target and prediction["direction"] == "bullish" and float(target) < current_price:
            details.append("看涨预测目标价低于当前价格")
        if target and prediction["direction"] == "bearish" and float(target) > current_price:
            details.append("看跌预测目标价高于当前价格")
    data = {"has_anomaly": bool(details), "anomaly_types": [], "severity": "medium" if details else "low", "details": details, "needs_user_confirmation": bool(details), "suggested_fix": details[0] if details else None}
    output = standard_node_output("data_anomaly_check", data, needs_human_review=bool(details))
    return apply_node_output(state, "data_anomaly_check", output, {"predictions": state.get("parsed_predictions", [])})


def human_confirmation_decision_node(state: dict[str, Any]) -> dict[str, Any]:
    anomaly = output_data(state, "data_anomaly_check")
    unknown_predictions = [item for item in state.get("parsed_predictions", []) if item.get("direction") == "sideways" and not item.get("target_price")]
    needs_review = bool(anomaly.get("needs_user_confirmation")) or bool(unknown_predictions)
    data = {
        "can_auto_save": True,
        "needs_user_confirmation": needs_review,
        "confirmation_fields": ["direction", "target_price"] if needs_review else [],
        "blocking_reasons": list(anomaly.get("details") or []),
        "suggested_user_question": "请确认方向、目标价或验证周期是否正确。" if needs_review else None,
    }
    state["needs_user_confirmation"] = needs_review
    output = standard_node_output("human_confirmation_decision", data, needs_human_review=needs_review)
    return apply_node_output(state, "human_confirmation_decision", output, {"anomaly": anomaly})


def persist_opinion_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 根据是否需要人工确认，决定写入复核草稿还是直接写入正式预测。
    conn = state["conn"]
    payload = state["payload"]
    analyst_data = output_data(state, "analyst_identification")
    time_data = output_data(state, "publish_time_extraction")
    analyst_name = analyst_data.get("analyst_name") or payload_value(payload, "analyst_name", "未命名分析师")
    now = utc_now()
    published_at = time_data.get("published_at") or payload_value(payload, "published_at", None) or now
    content = str(payload_value(payload, "content", "")).strip()
    if state.get("needs_user_confirmation"):
        review_payload = {
            "graph_name": state.get("graph_name"),
            "input_payload": {
                "analyst_name": payload_value(payload, "analyst_name", None),
                "content": content,
                "source_url": payload_value(payload, "source_url", None),
                "published_at": payload_value(payload, "published_at", None),
            },
            "confirmation_fields": output_data(state, "human_confirmation_decision").get("confirmation_fields", []),
            "blocking_reasons": output_data(state, "human_confirmation_decision").get("blocking_reasons", []),
            "node_outputs": state.get("node_outputs", {}),
        }
        review_cursor = conn.execute(
            """
            INSERT INTO human_review_items (raw_opinion_id, graph_run_id, review_type, status, payload, suggested_question, created_at)
            VALUES (?, ?, 'opinion_ingestion', 'pending', ?, ?, ?)
            """,
            (None, state.get("agent_run_id"), as_json(review_payload), output_data(state, "human_confirmation_decision").get("suggested_user_question"), now),
        )
        review_item_id = review_cursor.lastrowid
        draft_payload = {
            "input_payload": review_payload["input_payload"],
            "parsed_predictions": dedupe_predictions_by_horizon(state.get("parsed_predictions", [])),
            "node_outputs": state.get("node_outputs", {}),
        }
        conn.execute(
            """
            INSERT INTO opinion_review_drafts (
                review_item_id, analyst_name, source_url, published_at, current_price,
                draft_payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_item_id,
                str(analyst_name),
                payload_value(payload, "source_url", None),
                published_at,
                float(state["current_price"]),
                as_json(draft_payload),
                now,
                now,
            ),
        )
        conn.commit()
        state["review_item_id"] = review_item_id
        state["prediction_ids"] = []
        state["predictions"] = []
        state["result"] = {
            "review_item_id": review_item_id,
            "review_status": "pending_confirmation",
            "predictions": [],
        }
        output = standard_node_output("persist_opinion", {"review_item_id": review_item_id, "pending_confirmation": True})
        return apply_node_output(state, "persist_opinion", output, {"review_item_id": review_item_id})
    created = rules.persist_structured_opinion(
        conn,
        str(analyst_name),
        content,
        payload_value(payload, "source_url", None),
        published_at,
        float(state["current_price"]),
        dedupe_predictions_by_horizon(state.get("parsed_predictions", [])),
    )
    raw_opinion = created.get("raw_opinion") or {}
    state["raw_opinion_id"] = created.get("raw_opinion_id")
    state["analyst"] = created.get("analyst")
    state["prediction_ids"] = created.get("prediction_ids", [])
    state["predictions"] = created.get("predictions", [])
    state["result"] = {"raw_opinion": raw_opinion, "predictions": created.get("predictions", [])}
    conn.commit()
    output = standard_node_output("persist_opinion", {"raw_opinion_id": created.get("raw_opinion_id"), "prediction_ids": created.get("prediction_ids", [])})
    return apply_node_output(state, "persist_opinion", output, {"prediction_ids": created.get("prediction_ids", [])})


def load_trade_context_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 加载行情摘要和待验证预测，作为虚拟交易信号输入。
    summary = rules.market_summary(state["conn"])
    predictions = rules.pending_predictions_for_agent(state["conn"])
    state["market"] = summary
    state["pending_predictions"] = predictions
    output = standard_node_output("load_trade_context", {"market": summary, "pending_prediction_count": len(predictions)})
    return apply_node_output(state, "load_trade_context", output, {"focus_prediction_ids": state.get("focus_prediction_ids", [])})


def rule_based_signal_scoring_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 使用规则模型计算 AI 聚合账户的多空加权分。
    signal = rules.build_ai_trade_signal(state["conn"])
    state["signal"] = signal
    output = standard_node_output("rule_based_signal_scoring", signal)
    return apply_node_output(state, "rule_based_signal_scoring", output, {"market": state["market"], "predictions": state.get("pending_predictions", [])})


def risk_rule_node(state: dict[str, Any]) -> dict[str, Any]:
    # 根据波动率、观点分歧和预测数量生成风险提示。
    predictions = state.get("pending_predictions", [])
    summary = state["market"]
    signal = state["signal"]
    difference = signal["bull_score"] - signal["bear_score"]
    risk_parts: list[str] = []
    if summary["volatility"] >= 1.6:
        risk_parts.append("波动率偏高，建议降低仓位")
    if abs(difference) < 0.35 and predictions:
        risk_parts.append("多空观点接近，信号存在冲突")
    if not predictions:
        risk_parts.append("暂无待验证观点，Agent 只记录行情判断")
    risk = "；".join(risk_parts) if risk_parts else "风险正常，按轻仓模拟"
    state["risk"] = risk
    output = standard_node_output("risk_rule", {"risk": risk, "difference": round(difference, 3)})
    return apply_node_output(state, "risk_rule", output, {"signal": signal})


def trade_decision_rule_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 将交易信号转换成开多、开空或观望决策。
    signal = state["signal"]
    decision = signal.get("decision", "observe")
    decision_label = {"open_long": "开多", "open_short": "开空", "observe": "观望"}.get(decision, str(decision))
    should_execute = bool(signal.get("should_execute"))
    focus = (signal.get("supporting_predictions") or [None])[0]
    state["decision"] = decision
    state["decision_label"] = decision_label
    state["should_execute"] = should_execute
    state["focus_prediction"] = focus
    output = standard_node_output(
        "trade_decision_rule",
        {"decision": decision, "decision_label": decision_label, "should_execute": should_execute, "focus_prediction": focus},
    )
    return apply_node_output(state, "trade_decision_rule", output, {"signal": signal})


def trade_signal_explanation_node(state: dict[str, Any]) -> dict[str, Any]:
    fallback = {
        "signal_explanation": f"规则信号：多头分 {state['signal']['bull_score']}，空头分 {state['signal']['bear_score']}，决策为 {state['decision_label']}。",
        "position_logic": "AI 账户根据所有交易员预测、评分、置信度、周期和市场趋势聚合出虚拟交易信号。",
        "exit_logic": "出现反向信号时先平旧仓，再按规则开新仓。",
        "risk_notes": [state.get("risk", "")],
    }
    values = {
        "prediction": state.get("focus_prediction"),
        "trading_rules": "AI 聚合账户按加权多空分、置信度与波动率决定方向和名义仓位，信号不足时观望。",
        "trade_signal": {
            "signal": state.get("signal"),
            "decision": state.get("decision"),
            "should_execute": state.get("should_execute"),
            "risk": state.get("risk"),
        },
        "market_summary": state.get("market"),
    }
    output = llm_or_fallback(state, "virtual_trade", "trade_signal_explanation", values, fallback)
    return apply_node_output(state, "trade_signal_explanation", output, values)


def persist_agent_run_node(state: dict[str, Any]) -> dict[str, Any]:
    # 保存一次 Agent 运行摘要，并把前置节点记录关联到该运行。
    conn = state["conn"]
    summary = state["market"]
    signal = state["signal"]
    market_text = f"BTC {summary['latest_price']}，24h {summary['change_24h']}%，{summary['trend_label']}，支撑 {summary['support']}，压力 {summary['resistance']}"
    opinion_summary = f"多头 {signal['bull_count']} 条，空头 {signal['bear_count']} 条，多头分 {signal['bull_score']}，空头分 {signal['bear_score']}"
    input_snapshot = {
        "market": summary,
        "pending_predictions": state.get("pending_predictions", [])[:12],
        "focus_prediction_ids": state.get("focus_prediction_ids", []),
        "graph": "virtual_trade",
        "node_outputs": state.get("node_outputs", {}),
    }
    output_snapshot = {
        "decision": state["decision"],
        "decision_label": state["decision_label"],
        "risk": state["risk"],
        "signal": signal,
        "should_execute": state["should_execute"],
        "trade_signal_explanation": output_data(state, "trade_signal_explanation"),
    }
    cursor = conn.execute(
        """
        INSERT INTO agent_runs (
            trigger, market_summary, opinion_summary, decision, risk, should_execute,
            input_snapshot, output_snapshot, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            state.get("trigger", "manual"),
            market_text,
            opinion_summary,
            state["decision"],
            state["risk"],
            1 if state["should_execute"] else 0,
            as_json(input_snapshot),
            as_json(output_snapshot),
            utc_now(),
        ),
    )
    state["agent_run_id"] = cursor.lastrowid
    node_run_ids = state.get("node_run_ids", [])
    if node_run_ids:
        placeholders = ",".join("?" for _ in node_run_ids)
        conn.execute(
            f"""
            UPDATE agent_node_runs
            SET agent_run_id = ?
            WHERE id IN ({placeholders}) AND agent_run_id IS NULL
            """,
            [state["agent_run_id"], *node_run_ids],
        )
    conn.commit()
    output = standard_node_output("persist_agent_run", {"agent_run_id": state["agent_run_id"], "market_summary": market_text, "opinion_summary": opinion_summary})
    return apply_node_output(state, "persist_agent_run", output, input_snapshot)


def execute_virtual_trade_rule_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 根据 Agent 决策执行 AI 聚合账户的虚拟交易，并更新运行输出。
    market_text = output_data(state, "persist_agent_run").get("market_summary", "")
    trade_event = rules.execute_ai_trade_decision(state["conn"], int(state["agent_run_id"]), state.get("signal"), market_text)
    state["trade_event"] = trade_event
    output_snapshot = {
        "decision": state["decision"],
        "decision_label": state["decision_label"],
        "risk": state["risk"],
        "signal": state["signal"],
        "should_execute": state["should_execute"],
        "trade_event": trade_event,
        "trade_signal_explanation": output_data(state, "trade_signal_explanation"),
    }
    state["conn"].execute("UPDATE agent_runs SET output_snapshot = ? WHERE id = ?", (as_json(output_snapshot), state["agent_run_id"]))
    state["conn"].commit()
    row = state["conn"].execute("SELECT * FROM agent_runs WHERE id = ?", (state["agent_run_id"],)).fetchone()
    result = row_to_dict(row) or {}
    result["output"] = output_snapshot
    state["result"] = result
    output = standard_node_output("execute_virtual_trade_rule", {"trade_event": trade_event})
    return apply_node_output(state, "execute_virtual_trade_rule", output, {"agent_run_id": state.get("agent_run_id")})


def load_due_predictions_node(state: dict[str, Any]) -> dict[str, Any]:
    # 读取已经到验证时间的待验证预测。
    rows = state["conn"].execute(
        """
        SELECT * FROM predictions
        WHERE status = 'pending' AND verification_time <= ?
        ORDER BY verification_time ASC
        """,
        (utc_now(),),
    ).fetchall()
    predictions = rows_to_dicts(rows)
    state["due_predictions"] = predictions
    output = standard_node_output("load_due_predictions", {"due_count": len(predictions), "prediction_ids": [item["id"] for item in predictions]})
    return apply_node_output(state, "load_due_predictions", output, {})


def rule_based_verification_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 对每条到期预测执行本地规则验证。
    verified = []
    for prediction in state.get("due_predictions", []):
        verified.append(rules.verify_prediction(state["conn"], prediction))
    state["conn"].commit()
    state["verified"] = verified
    output = standard_node_output("rule_based_verification", {"verified": verified, "verified_count": len(verified)})
    return apply_node_output(state, "rule_based_verification", output, {"due_predictions": state.get("due_predictions", [])})


def verification_explanation_node(state: dict[str, Any]) -> dict[str, Any]:
    explanations = []
    for item in state.get("verified", []):
        prediction = next((prediction for prediction in state.get("due_predictions", []) if prediction["id"] == item["prediction_id"]), {})
        fallback = {
            "plain_language_summary": f"预测验证结果为 {item['status']}，方向为 {item['actual_direction']}，目标价{'触达' if item['target_hit'] else '未触达'}。",
            "direction_explanation": f"验证期方向判定为 {item['actual_direction']}。",
            "target_explanation": "系统按验证窗口最高价/最低价判断目标是否触达。",
            "time_explanation": "系统按预测创建时间到当前时间的行情窗口验证。",
            "final_result_explanation": f"综合得分 {item['score']}，状态 {item['status']}。",
            "user_visible_tags": [item["status"]],
        }
        output = llm_or_fallback(
            state,
            "prediction_verification",
            "verification_explanation",
            {"prediction": prediction, "verification_market_data": {}, "verification_result": item},
            fallback,
        )
        explanations.append({"prediction_id": item["prediction_id"], **output.get("data", {})})
    output = standard_node_output("verification_explanation", {"items": explanations})
    return apply_node_output(state, "verification_explanation", output, {"verified": state.get("verified", [])})


def failure_reason_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    market = rules.market_summary(state["conn"])
    reasons = []
    for item in state.get("verified", []):
        if item.get("status") != "failed":
            continue
        prediction = next((prediction for prediction in state.get("due_predictions", []) if prediction["id"] == item["prediction_id"]), {})
        fallback = {
            "failure_reason_category": "unclear",
            "failure_reason_summary": "系统只能确认该预测未达到成功阈值，具体原因需要结合行情路径复核。",
            "supporting_evidence": [f"综合得分 {item['score']}"],
            "should_reduce_direction_score": True,
            "should_reduce_target_score": not item.get("target_hit"),
            "should_reduce_stability_score": False,
        }
        output = llm_or_fallback(
            state,
            "prediction_verification",
            "failure_reason",
            {"prediction": prediction, "verification_result": item, "market_summary": market, "indicator_summary": market, "view_change_records": []},
            fallback,
        )
        reasons.append({"prediction_id": item["prediction_id"], **output.get("data", {})})
    output = standard_node_output("failure_reason", {"items": reasons})
    return apply_node_output(state, "failure_reason", output, {"verified": state.get("verified", [])})


def save_verification_report_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 保存预测验证解释和失败原因，供预测详情页展示。
    explanations = {item["prediction_id"]: item for item in output_data(state, "verification_explanation").get("items", [])}
    reasons = {item["prediction_id"]: item for item in output_data(state, "failure_reason").get("items", [])}
    predictions = {item["id"]: item for item in state.get("due_predictions", [])}
    reports: dict[int, dict[str, Any]] = {}
    for item in state.get("verified", []):
        prediction_id = int(item["prediction_id"])
        reports[prediction_id] = rules.save_prediction_verification_report(
            state["conn"],
            predictions.get(prediction_id, {}),
            item,
            explanations.get(prediction_id),
            reasons.get(prediction_id),
            commit=False,
        )
    state["conn"].commit()
    items = [{**item, "report": reports.get(int(item["prediction_id"]))} for item in state.get("verified", [])]
    result = {"verified_count": len(items), "items": items}
    state["result"] = result
    output = standard_node_output("save_verification_report", result)
    return apply_node_output(state, "save_verification_report", output, result)


def report_direction_distribution(predictions: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    # 按预测周期统计多空震荡分布。
    distributions: dict[str, dict[str, int]] = {
        "all": {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0},
        "intraday": {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0},
        "short": {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0},
        "medium": {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0},
        "long": {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0},
    }
    for prediction in predictions:
        direction = str(prediction.get("direction") or "unknown")
        horizon = str(prediction.get("horizon") or "unknown")
        if direction not in distributions["all"]:
            direction = "unknown"
        if horizon not in distributions:
            distributions[horizon] = {"bullish": 0, "bearish": 0, "sideways": 0, "unknown": 0}
        distributions["all"][direction] += 1
        distributions[horizon][direction] += 1
    return distributions


def report_weighted_consensus(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    # 按分析师评分和置信度计算加权共识方向。
    scores = {"bullish": 0.0, "bearish": 0.0, "sideways": 0.0}
    for prediction in predictions:
        direction = str(prediction.get("direction") or "sideways")
        if direction not in scores:
            direction = "sideways"
        weight = max(0.25, min(float(prediction.get("total_score") or 50) / 100, 1.25))
        confidence = prediction.get("confidence")
        if confidence == "high":
            weight *= 1.25
        elif confidence == "low":
            weight *= 0.7
        scores[direction] += weight
    dominant = max(scores, key=scores.get) if any(scores.values()) else "mixed"
    ordered = sorted(scores.values(), reverse=True)
    if len(ordered) > 1 and ordered[0] - ordered[1] < 0.35:
        dominant = "mixed"
    return {"direction": dominant, "scores": {key: round(value, 3) for key, value in scores.items()}}


def load_daily_report_context_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    # 汇总日报所需的行情、预测、验证、改口和账户上下文。
    context = rules.daily_report_context(state["conn"])
    market = context["market"]
    predictions = context["active_predictions"]
    state["market"] = market
    state["active_predictions"] = predictions
    state["recent_verifications"] = context["recent_verifications"]
    state["prediction_changes"] = context["prediction_changes"]
    state["account"] = context["account"]
    state["top_analysts"] = context["top_analysts"]
    state["report_context"] = context
    output = standard_node_output(
        "load_daily_report_context",
        {
            "market": market,
            "active_prediction_count": len(predictions),
            "recent_verification_count": len(context["recent_verifications"]),
            "prediction_change_count": len(context["prediction_changes"]),
            "account": context["account"],
        },
    )
    return apply_node_output(state, "load_daily_report_context", output, {})


def report_market_summary_node(state: dict[str, Any]) -> dict[str, Any]:
    # 生成日报中的行情摘要，LLM 不可用时使用规则摘要。
    market = state["market"]
    rsi = market.get("rsi_14")
    funding_rate = float(market.get("funding_rate") or 0)
    volatility = float(market.get("volatility") or 0)
    risk_notes: list[str] = []
    if volatility >= 1.6:
        risk_notes.append("ATR 相对价格偏高，短线波动风险较高")
    if rsi is not None and float(rsi) >= 70:
        risk_notes.append("RSI 进入偏热区间，需防范追高回撤")
    if rsi is not None and float(rsi) <= 30:
        risk_notes.append("RSI 进入偏冷区间，需关注反弹修复")
    if abs(funding_rate) >= 0.0003:
        risk_notes.append("资金费率偏离中性，杠杆拥挤风险上升")
    fallback = {
        "market_summary": f"BTC 最新价 {market['latest_price']}，24h {market['change_24h']}%，{market['trend_label']}。",
        "trend_state": market.get("trend", "neutral"),
        "volatility_state": "high" if volatility >= 1.6 else "low" if volatility <= 0.6 else "medium",
        "volume_state": "normal",
        "funding_state": "positive" if funding_rate > 0.0003 else "negative" if funding_rate < -0.0003 else "neutral",
        "key_support_levels": [market.get("support")],
        "key_resistance_levels": [market.get("resistance")],
        "indicator_notes": [f"RSI {market.get('rsi_14')}", f"MACD {market.get('macd')}"],
        "risk_notes": risk_notes,
        "market_snapshot": {
            "latest_price": market.get("latest_price"),
            "change_24h": market.get("change_24h"),
            "volatility": market.get("volatility"),
            "support": market.get("support"),
            "resistance": market.get("resistance"),
            "funding_rate": market.get("funding_rate"),
        },
    }
    output = llm_or_fallback(state, "daily_report", "market_summary", {"market_data": market, "technical_indicators": market, "funding_rates": market.get("funding_rate")}, fallback)
    return apply_node_output(state, "market_summary", output, {"market": market})


def analyst_consensus_node(state: dict[str, Any]) -> dict[str, Any]:
    # 生成日报中的分析师共识和分歧信息。
    predictions = state.get("active_predictions", [])
    distributions = report_direction_distribution(predictions)
    weighted = report_weighted_consensus(predictions)
    high_reliability = [
        {
            "analyst_name": prediction.get("analyst_name"),
            "direction": prediction.get("direction"),
            "horizon": prediction.get("horizon"),
            "target_price": prediction.get("target_price"),
            "confidence": prediction.get("confidence"),
            "total_score": prediction.get("total_score"),
            "summary": prediction.get("summary"),
        }
        for prediction in predictions
        if float(prediction.get("total_score") or 0) >= 70 or prediction.get("confidence") == "high"
    ][:5]
    disagreements = []
    for horizon, distribution in distributions.items():
        if horizon != "all" and distribution.get("bullish", 0) and distribution.get("bearish", 0):
            disagreements.append({"horizon": horizon, "bullish": distribution["bullish"], "bearish": distribution["bearish"]})
    fallback = {
        "consensus_summary": f"当前待验证预测 {len(predictions)} 条，多头 {distributions['all'].get('bullish', 0)} 条，空头 {distributions['all'].get('bearish', 0)} 条，加权共识为 {weighted['direction']}。",
        "short_term_distribution": distributions.get("short", {}),
        "mid_term_distribution": distributions.get("medium", {}),
        "long_term_distribution": distributions.get("long", {}),
        "all_distribution": distributions.get("all", {}),
        "weighted_consensus": weighted,
        "notable_disagreements": disagreements,
        "high_reliability_views": high_reliability,
        "top_analysts": state.get("top_analysts", []),
    }
    output = llm_or_fallback(state, "daily_report", "analyst_consensus", {"active_predictions": predictions, "analyst_metrics": state.get("top_analysts", []), "prediction_changes": state.get("prediction_changes", [])}, fallback)
    return apply_node_output(state, "analyst_consensus", output, {"predictions": predictions})


def scenario_analysis_node(state: dict[str, Any]) -> dict[str, Any]:
    # 基于行情和共识生成多头、空头、震荡等情景分析。
    market_summary_data = output_data(state, "market_summary")
    consensus = output_data(state, "analyst_consensus")
    market = state["market"]
    weighted = consensus.get("weighted_consensus") or {}
    weighted_direction = weighted.get("direction") if isinstance(weighted, dict) else weighted
    base_description = "价格围绕关键支撑与压力区间运行。"
    if market.get("trend") == "uptrend" and weighted_direction == "bullish":
        base_description = "趋势与分析师共识均偏多，基准情景偏向震荡上行。"
    elif market.get("trend") == "downtrend" and weighted_direction == "bearish":
        base_description = "趋势与分析师共识均偏空，基准情景偏向反弹承压。"
    fallback = {
        "base_case": {"scenario": "基准情景", "description": base_description, "trigger_conditions": [f"维持在 {market.get('support')} 至 {market.get('resistance')} 区间内"], "invalid_conditions": [f"有效跌破 {market.get('support')} 或突破 {market.get('resistance')}"]},
        "bullish_case": {"scenario": "多头情景", "description": "若放量突破压力位，可能进入偏强结构。", "trigger_conditions": [f"突破 {market.get('resistance')}"], "risk_factors": market_summary_data.get("risk_notes", [])},
        "bearish_case": {"scenario": "空头情景", "description": "若跌破支撑位，可能进入回调结构。", "trigger_conditions": [f"跌破 {market.get('support')}"], "risk_factors": market_summary_data.get("risk_notes", [])},
        "range_case": {"scenario": "震荡情景", "description": "若支撑压力均未突破，继续区间波动。", "range_low": market.get("support"), "range_high": market.get("resistance")},
    }
    output = llm_or_fallback(
        state,
        "daily_report",
        "scenario_analysis",
        {"market_summary": market_summary_data, "indicator_summary": market, "consensus_summary": consensus, "key_levels": {"support": market.get("support"), "resistance": market.get("resistance")}},
        fallback,
    )
    return apply_node_output(state, "scenario_analysis", output, {"market": market})


def daily_report_node(state: dict[str, Any]) -> dict[str, Any]:
    # 汇总各节点结果，形成最终日报结构。
    market_summary_data = output_data(state, "market_summary")
    consensus = output_data(state, "analyst_consensus")
    scenarios = output_data(state, "scenario_analysis")
    recent_verifications = state.get("recent_verifications", [])
    prediction_changes = state.get("prediction_changes", [])
    average_score = sum(float(item.get("final_score") or 0) for item in recent_verifications) / len(recent_verifications) if recent_verifications else 0
    success_count = sum(1 for item in recent_verifications if item.get("status") == "success")
    change_distribution: dict[str, int] = {}
    for item in prediction_changes:
        change_type = str(item.get("change_type") or "unknown")
        change_distribution[change_type] = change_distribution.get(change_type, 0) + 1
    recent_prediction_review = f"近期验证 {len(recent_verifications)} 条，成功 {success_count} 条，平均分 {round(average_score, 3)}。" if recent_verifications else "暂无近期验证摘要。"
    prediction_change_review = f"近期观点变化 {len(prediction_changes)} 条，类型分布 {change_distribution}。" if prediction_changes else "近期暂无记录的观点变化。"
    fallback = {
        "title": "BTC 每日市场观察",
        "executive_summary": market_summary_data.get("market_summary", "暂无市场摘要"),
        "market_status": market_summary_data.get("market_summary", ""),
        "technical_view": "基于本地指标与行情窗口生成。",
        "analyst_consensus": consensus.get("consensus_summary", ""),
        "key_levels": {"support": state["market"].get("support"), "resistance": state["market"].get("resistance")},
        "scenarios": list(scenarios.values()) if scenarios else [],
        "recent_prediction_review": recent_prediction_review,
        "prediction_change_review": prediction_change_review,
        "active_prediction_count": len(state.get("active_predictions", [])),
        "recent_verification_count": len(recent_verifications),
        "prediction_change_count": len(prediction_changes),
        "change_distribution": change_distribution,
        "account_snapshot": state.get("account", {}),
        "top_analysts": state.get("top_analysts", []),
        "recent_verifications": recent_verifications,
        "prediction_changes": prediction_changes,
        "generated_at": utc_now(),
        "risk_warnings": market_summary_data.get("risk_notes", []),
        "disclaimer": "本报告仅用于信息整理与历史表现分析，不构成投资建议。",
    }
    output = llm_or_fallback(
        state,
        "daily_report",
        "daily_report",
        {"market_summary": market_summary_data, "indicator_summary": state["market"], "consensus_summary": consensus, "scenario_analysis": scenarios, "recent_verification_results": recent_verifications, "prediction_changes": prediction_changes, "account_snapshot": state.get("account", {})},
        fallback,
    )
    report = {**fallback, **(output.get("data") or {})}
    output["data"] = report
    state["report"] = report
    return apply_node_output(state, "daily_report", output, report)


def save_daily_report_node(state: dict[str, Any]) -> dict[str, Any]:
    # 将日报 JSON 保存到 agent_reports 表。
    report = state.get("report", {})
    created_at = utc_now()
    cursor = state["conn"].execute(
        """
        INSERT INTO agent_reports (report_type, title, payload, created_at)
        VALUES ('daily', ?, ?, ?)
        """,
        (report.get("title", "BTC 每日市场观察"), as_json(report), created_at),
    )
    state["conn"].commit()
    result = {"id": cursor.lastrowid, "report_type": "daily", "title": report.get("title", "BTC 每日市场观察"), "created_at": created_at, "data": report, **report}
    state["result"] = result
    output = standard_node_output("save_daily_report", result)
    return apply_node_output(state, "save_daily_report", output, result)
