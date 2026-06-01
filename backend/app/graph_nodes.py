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
        "analysis_explanation": output_data(state, "agent_analysis_explanation"),
        "evidence_conflict": state.get("evidence_conflict", {}),
        "reflection": state.get("reflection", {}),
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
    if payload.get("evidence_conflict"):
        state["evidence_conflict"] = payload["evidence_conflict"]
    if payload.get("reflection"):
        state["reflection"] = payload["reflection"]
    for node_name in ("agent_analysis_explanation", "verification_explanation", "failure_reason"):
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


def record_intermediate(state: dict[str, Any], step_type: str, step_name: str, data: dict[str, Any], status: str = "success", error: str = "") -> None:
    # 将 tool/skill/LLM 等中间步骤记录到 agent_node_runs，供调试窗口实时展示。
    # step_type 前缀如 "tool"、"skill"、"llm"，node_name 格式为 "tool:gate_market_research"。
    conn: sqlite3.Connection = state.get("conn")  # type: ignore[assignment]
    if not conn:
        return
    node_name = f"{step_type}:{step_name}" if step_type else step_name
    now = utc_now()
    output = standard_node_output(node_name, data, success=(status == "success"), errors=[error] if error else [])
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
                status,
                as_json({"step_type": step_type, "step_name": step_name}),
                as_json(output),
                error,
                now,
                now,
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
        record_intermediate(state, "llm", f"{graph_name}.{node_name}", {"prompt_keys": list(values.keys()), "data_preview": str(output.get("data", ""))[:200]})
        return output
    record_intermediate(state, "llm", f"{graph_name}.{node_name}", {"error": output.get("errors", []), "fallback_used": True}, status="failed", error="；".join(output.get("errors") or []))
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
        "contains_action_plan": False,
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


def _build_gate_context(conn: sqlite3.Connection) -> dict[str, Any]:
    """构建 Gate MCP 增强上下文（合约/情绪/记忆），供 Agent 各流程复用。"""
    try:
        from .gate_sync import latest_btc_contract_metrics, latest_sentiment_snapshot, active_market_memories
        gate_ctx: dict[str, Any] = {
            "contract_metrics": latest_btc_contract_metrics(conn),
            "sentiment_snapshot": latest_sentiment_snapshot(conn),
            "active_memories": active_market_memories(conn, limit=10),
        }
    except Exception:
        gate_ctx = {}
    return gate_ctx


def load_agent_analysis_context_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    summary = rules.market_summary(state["conn"])
    predictions = rules.pending_predictions_for_agent(state["conn"])
    gate_ctx = _build_gate_context(state["conn"])
    state["market"] = summary
    state["pending_predictions"] = predictions
    state["gate_context"] = gate_ctx
    state["react_tools_used"] = []
    state["react_tool_results"] = {}
    output = standard_node_output("load_agent_analysis_context", {
        "market": summary,
        "pending_prediction_count": len(predictions),
        "gate_context_available": bool(gate_ctx.get("contract_metrics")),
    })
    return apply_node_output(state, "load_agent_analysis_context", output, {"focus_prediction_ids": state.get("focus_prediction_ids", [])})


def rule_based_consensus_scoring_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    signal = rules.build_agent_analysis_signal(state["conn"])
    state["signal"] = signal
    output = standard_node_output("rule_based_consensus_scoring", signal)
    return apply_node_output(state, "rule_based_consensus_scoring", output, {"market": state["market"], "predictions": state.get("pending_predictions", [])})


def risk_rule_node(state: dict[str, Any]) -> dict[str, Any]:
    # 根据波动率、观点分歧和预测数量生成风险提示。
    predictions = state.get("pending_predictions", [])
    summary = state["market"]
    signal = state["signal"]
    difference = signal["bull_score"] - signal["bear_score"]
    risk_parts: list[str] = []
    if summary["volatility"] >= 1.6:
        risk_parts.append("波动率偏高，短线结论需谨慎")
    if abs(difference) < 0.35 and predictions:
        risk_parts.append("多空观点接近，方向共识不强")
    if not predictions:
        risk_parts.append("暂无待验证观点，Agent 只记录行情判断")
    risk = "；".join(risk_parts) if risk_parts else "风险正常，仅记录分析判断"
    state["risk"] = risk
    output = standard_node_output("risk_rule", {"risk": risk, "difference": round(difference, 3)})
    return apply_node_output(state, "risk_rule", output, {"signal": signal})


def analysis_decision_rule_node(state: dict[str, Any]) -> dict[str, Any]:
    signal = state["signal"]
    decision = signal.get("decision", "observe")
    decision_label = {"bullish": "偏多", "bearish": "偏空", "observe": "观望"}.get(decision, str(decision))
    focus = (signal.get("supporting_predictions") or [None])[0]
    state["decision"] = decision
    state["decision_label"] = decision_label
    state["focus_prediction"] = focus
    output = standard_node_output(
        "analysis_decision_rule",
        {"decision": decision, "decision_label": decision_label, "focus_prediction": focus},
    )
    return apply_node_output(state, "analysis_decision_rule", output, {"signal": signal})


def agent_analysis_explanation_node(state: dict[str, Any]) -> dict[str, Any]:
    fallback = {
        "analysis_summary": f"规则分析：多头分 {state['signal']['bull_score']}，空头分 {state['signal']['bear_score']}，结论为 {state['decision_label']}。",
        "consensus_logic": "Agent 根据待验证预测、分析师评分、置信度、周期和市场趋势生成方向共识。",
        "action_scope": "仅记录分析结论，不创建任何账户或执行记录。",
        "risk_notes": [state.get("risk", "")],
    }
    values = {
        "prediction": state.get("focus_prediction"),
        "analysis_rules": "Agent 按加权多空分、置信度与波动率生成方向结论，分差不足时观望。",
        "analysis_signal": {
            "signal": state.get("signal"),
            "decision": state.get("decision"),
            "risk": state.get("risk"),
        },
        "market_summary": state.get("market"),
    }
    output = llm_or_fallback(state, "agent_analysis", "analysis_explanation", values, fallback)
    return apply_node_output(state, "agent_analysis_explanation", output, values)


def evidence_sufficiency_check_node(state: dict[str, Any]) -> dict[str, Any]:
    """固定代码判断证据是否充分，决定是否需要 ReAct Tool 补充。"""
    gate_ctx = state.get("gate_context") or {}
    predictions = state.get("pending_predictions", [])
    signal = state.get("signal", {})

    needs_supplement = False
    reasons: list[str] = []

    # 规则 1：无合约指标
    if not gate_ctx.get("contract_metrics"):
        needs_supplement = True
        reasons.append("缺少 BTC 合约指标")

    # 规则 2：无情绪快照
    if not gate_ctx.get("sentiment_snapshot"):
        needs_supplement = True
        reasons.append("缺少市场情绪快照")

    # 规则 3：置信度低且预测少
    if signal.get("confidence") == "low" and len(predictions) < 3:
        needs_supplement = True
        reasons.append("分析置信度低且预测样本不足")
    if signal.get("confidence") == "low":
        needs_supplement = True
        reasons.append("缺少技术面补充信息")

    # 规则 4：无活跃记忆
    if not gate_ctx.get("active_memories"):
        needs_supplement = True
        reasons.append("无活跃市场记忆")

    data = {
        "is_sufficient": not needs_supplement,
        "needs_supplement": needs_supplement,
        "reasons": reasons,
    }
    output = standard_node_output("evidence_sufficiency_check", data)
    return apply_node_output(state, "evidence_sufficiency_check", output, data)


def rule_based_react_tools(reasons: list[str]) -> list[str]:
    selected: list[str] = []
    if any("合约" in r for r in reasons):
        selected.append("gate_market_research")
    if any("情绪" in r for r in reasons):
        selected.append("gate_square_research")
    if any("记忆" in r for r in reasons):
        selected.append("market_memory_search")
    if any("置信度" in r or "样本" in r for r in reasons):
        selected.append("gate_news_research")
    if any("技术" in r or "链上" in r or "指标" in r for r in reasons):
        selected.append("gate_info_research")
    return list(dict.fromkeys(selected))


def react_tool_selection_node(state: dict[str, Any]) -> dict[str, Any]:
    """根据证据缺口选择并调用 Agent Tools 补充信息。"""
    from .agent_tools import list_agent_tools, run_agent_tool

    sufficiency = output_data(state, "evidence_sufficiency_check")
    if not sufficiency.get("needs_supplement"):
        output = standard_node_output("react_tool_selection", {"tools_called": [], "skipped": True})
        return apply_node_output(state, "react_tool_selection", output, {})

    conn = state["conn"]
    gate_ctx = state.get("gate_context") or {}
    reasons = sufficiency.get("reasons", [])
    tool_catalog = list_agent_tools()
    allowed_tools = {item["name"] for item in tool_catalog}
    fallback_tools = rule_based_react_tools([str(reason) for reason in reasons])
    selection_values = {
        "evidence_gaps": reasons,
        "market_summary": state.get("market", {}),
        "analysis_signal": state.get("signal", {}),
        "gate_context": gate_ctx,
        "tool_catalog": tool_catalog,
    }
    selection_output = llm_or_fallback(
        state,
        "agent_analysis",
        "react_tool_selection",
        selection_values,
        {
            "selected_tools": fallback_tools or ["no_tool"],
            "selection_reason": "规则兜底：按证据缺口关键词选择工具",
            "expected_evidence": reasons,
        },
    )
    selected_raw = selection_output.get("data", {}).get("selected_tools") or fallback_tools
    if isinstance(selected_raw, str):
        selected_raw = [item.strip() for item in selected_raw.split(",") if item.strip()]
    selected_tools = [
        str(item)
        for item in selected_raw
        if str(item) in allowed_tools and str(item) != "no_tool"
    ]
    if not selected_tools and "no_tool" not in [str(item) for item in selected_raw]:
        selected_tools = fallback_tools
    selected_tools = list(dict.fromkeys(selected_tools))
    tools_called: list[str] = []
    tool_results: dict[str, Any] = {}

    for tool_name in selected_tools:
        try:
            result = run_agent_tool(conn, tool_name)
            tools_called.append(tool_name)
            tool_results[tool_name] = result
            record_intermediate(state, "tool", tool_name, {"found": result.get("found"), "summary": str(result)[:200]})
        except Exception as tool_exc:
            tools_called.append(tool_name)
            tool_results[tool_name] = {"error": str(tool_exc)}
            record_intermediate(state, "tool", tool_name, {"error": str(tool_exc)}, status="failed", error=str(tool_exc))
        if tool_name == "gate_market_research":
            if result.get("found") and result.get("contract_metrics"):
                gate_ctx["contract_metrics"] = result["contract_metrics"]
        elif tool_name == "market_memory_search":
            if result.get("found") and result.get("memories"):
                gate_ctx["active_memories"] = result["memories"]
        elif tool_name == "gate_square_research":
            gate_ctx["square_research"] = result
        elif tool_name == "gate_news_research":
            gate_ctx["news_research"] = result
        elif tool_name == "gate_info_research":
            gate_ctx["info_research"] = result

    state["gate_context"] = gate_ctx
    state["react_tools_used"] = tools_called
    state["react_tool_results"] = tool_results

    data = {
        "tools_called": tools_called,
        "tool_results": tool_results,
        "selection": selection_output.get("data", {}),
        "skipped": not bool(tools_called),
    }
    output = standard_node_output(
        "react_tool_selection",
        data,
        warnings=selection_output.get("warnings", []),
        evidence=[f"tool:{tool}" for tool in tools_called],
    )
    return apply_node_output(state, "react_tool_selection", output, selection_values)


def evidence_conflict_judgement_node(state: dict[str, Any]) -> dict[str, Any]:
    gate_ctx = state.get("gate_context", {})
    market_signal = {
        "market": state.get("market", {}),
        "signal": state.get("signal", {}),
        "decision": state.get("decision"),
        "risk": state.get("risk"),
        "react_tool_results": state.get("react_tool_results", {}),
    }
    analyst_opinions = state.get("pending_predictions", [])[:20]
    sentiment_snapshot = gate_ctx.get("sentiment_snapshot", {})
    market_memories = gate_ctx.get("active_memories", [])
    fallback = {
        "has_conflict": False,
        "conflict_points": [],
        "source_credibility": {
            "market_signal": "medium",
            "analyst_opinions": "medium",
            "sentiment_snapshot": "medium" if sentiment_snapshot else "low",
            "market_memories": "medium" if market_memories else "low",
        },
        "overall_confidence": state.get("signal", {}).get("confidence", "medium"),
        "recommended_weight_adjustments": {},
        "summary": "规则兜底：未发现明确多源冲突。",
    }
    decision = state.get("decision")
    sentiment = str((sentiment_snapshot or {}).get("overall_sentiment") or "")
    if decision == "bullish" and sentiment in {"fear", "extreme_fear", "bearish"}:
        fallback["has_conflict"] = True
        fallback["conflict_points"].append("Agent 偏多，但情绪快照偏恐慌/看空")
        fallback["overall_confidence"] = "low"
    if decision == "bearish" and sentiment in {"greed", "extreme_greed", "bullish"}:
        fallback["has_conflict"] = True
        fallback["conflict_points"].append("Agent 偏空，但情绪快照偏贪婪/看多")
        fallback["overall_confidence"] = "low"
    output = llm_or_fallback(
        state,
        "skills",
        "evidence_conflict_judgement",
        {
            "market_signal": market_signal,
            "analyst_opinions": analyst_opinions,
            "sentiment_snapshot": sentiment_snapshot,
            "market_memories": market_memories,
        },
        fallback,
    )
    conflict_data = output.get("data", {})
    state["evidence_conflict"] = conflict_data
    if conflict_data.get("has_conflict"):
        points = "；".join(str(item) for item in conflict_data.get("conflict_points", [])[:3])
        if points and points not in state.get("risk", ""):
            state["risk"] = f"{state.get('risk', '')}；证据冲突：{points}" if state.get("risk") else f"证据冲突：{points}"
    return apply_node_output(state, "evidence_conflict_judgement", output, {"market_signal": market_signal, "gate_context": gate_ctx})


def reflection_critique_node(state: dict[str, Any]) -> dict[str, Any]:
    """结论输出前的反思检查，验证证据充分性和是否存在过度判断。"""
    conclusion = {
        "decision": state.get("decision"),
        "decision_label": state.get("decision_label"),
        "risk": state.get("risk"),
        "signal": state.get("signal"),
        "analysis_explanation": output_data(state, "agent_analysis_explanation"),
        "evidence_conflict": state.get("evidence_conflict", {}),
    }
    evidence = {
        "gate_context": state.get("gate_context", {}),
        "react_tools_used": state.get("react_tools_used", []),
        "react_tool_results": state.get("react_tool_results", {}),
        "evidence_conflict": state.get("evidence_conflict", {}),
        "pending_prediction_count": len(state.get("pending_predictions", [])),
    }
    market_context = state.get("market", {})

    fallback = {
        "is_adequate": True,
        "confidence_adjustment": 0.0,
        "weak_points": [],
        "over_judgements": [],
        "missing_evidence": [],
        "correction_suggestion": "",
        "revised_risk_notes": [],
    }

    # 简单规则检查
    signal = state.get("signal", {})
    if signal.get("confidence") == "low" and state.get("decision") != "observe":
        fallback["is_adequate"] = False
        fallback["weak_points"].append("置信度低但给出了方向性结论")
        fallback["confidence_adjustment"] = -0.1
    if not state.get("pending_predictions"):
        fallback["weak_points"].append("无待验证预测作为决策依据")

    output = llm_or_fallback(
        state,
        "skills",
        "reflection_critique",
        {"agent_conclusion": conclusion, "evidence_used": evidence, "market_context": market_context},
        fallback,
    )

    reflection_data = output.get("data", {})
    state["reflection"] = reflection_data

    # 如果反思建议修正风险提示，追加到 state
    revised_notes = reflection_data.get("revised_risk_notes", [])
    if revised_notes:
        current_risk = state.get("risk", "")
        extra = "；".join(str(n) for n in revised_notes if n)
        if extra and extra not in current_risk:
            state["risk"] = f"{current_risk}；{extra}" if current_risk else extra

    return apply_node_output(state, "reflection_critique", output, {"conclusion": conclusion})


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
        "graph": "agent_analysis",
        "gate_context": state.get("gate_context", {}),
        "node_outputs": state.get("node_outputs", {}),
    }
    output_snapshot = {
        "decision": state["decision"],
        "decision_label": state["decision_label"],
        "risk": state["risk"],
        "signal": signal,
        "analysis_explanation": output_data(state, "agent_analysis_explanation"),
        "react_tools_used": state.get("react_tools_used", []),
        "react_tool_results": state.get("react_tool_results", {}),
        "evidence_conflict": state.get("evidence_conflict", {}),
        "gate_context_summary": state.get("gate_context", {}),
        "reflection": state.get("reflection", {}),
    }
    cursor = conn.execute(
        """
        INSERT INTO agent_runs (
            trigger, market_summary, opinion_summary, decision, risk,
            input_snapshot, output_snapshot, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            state.get("trigger", "manual"),
            market_text,
            opinion_summary,
            state["decision"],
            state["risk"],
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


def finalize_agent_analysis_node(state: dict[str, Any]) -> dict[str, Any]:
    output_snapshot = {
        "decision": state["decision"],
        "decision_label": state["decision_label"],
        "risk": state["risk"],
        "signal": state["signal"],
        "analysis_explanation": output_data(state, "agent_analysis_explanation"),
        "react_tools_used": state.get("react_tools_used", []),
        "react_tool_results": state.get("react_tool_results", {}),
        "evidence_conflict": state.get("evidence_conflict", {}),
        "gate_context_summary": state.get("gate_context", {}),
        "reflection": state.get("reflection", {}),
    }
    state["conn"].execute("UPDATE agent_runs SET output_snapshot = ? WHERE id = ?", (as_json(output_snapshot), state["agent_run_id"]))
    state["conn"].commit()
    row = state["conn"].execute("SELECT * FROM agent_runs WHERE id = ?", (state["agent_run_id"],)).fetchone()
    result = row_to_dict(row) or {}
    result["output"] = output_snapshot
    state["result"] = result
    output = standard_node_output("finalize_agent_analysis", {})
    return apply_node_output(state, "finalize_agent_analysis", output, {"agent_run_id": state.get("agent_run_id")})


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
    gate_ctx = state.get("gate_context") or _build_gate_context(state["conn"])
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
            {"prediction": prediction, "verification_market_data": {}, "verification_result": item, "gate_context": gate_ctx},
            fallback,
        )
        explanations.append({"prediction_id": item["prediction_id"], **output.get("data", {})})
    output = standard_node_output("verification_explanation", {"items": explanations})
    return apply_node_output(state, "verification_explanation", output, {"verified": state.get("verified", [])})


def failure_reason_node(state: dict[str, Any]) -> dict[str, Any]:
    from . import services as rules

    market = rules.market_summary(state["conn"])
    gate_ctx = state.get("gate_context") or _build_gate_context(state["conn"])
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
            {"prediction": prediction, "verification_result": item, "market_summary": market, "indicator_summary": market, "view_change_records": [], "gate_context": gate_ctx},
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
    gate_ctx = state.get("gate_context") or _build_gate_context(state["conn"])
    context_snapshot = {
        "gate_context": gate_ctx,
        "market_summary": rules.market_summary(state["conn"]),
    }
    reports: dict[int, dict[str, Any]] = {}
    for item in state.get("verified", []):
        prediction_id = int(item["prediction_id"])
        reports[prediction_id] = rules.save_prediction_verification_report(
            state["conn"],
            predictions.get(prediction_id, {}),
            item,
            explanations.get(prediction_id),
            reasons.get(prediction_id),
            context_snapshot=context_snapshot,
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

    context = rules.daily_report_context(state["conn"])
    market = context["market"]
    predictions = context["active_predictions"]
    gate_ctx = _build_gate_context(state["conn"])
    state["market"] = market
    state["active_predictions"] = predictions
    state["recent_verifications"] = context["recent_verifications"]
    state["prediction_changes"] = context["prediction_changes"]
    state["top_analysts"] = context["top_analysts"]
    state["report_context"] = context
    state["gate_context"] = gate_ctx
    output = standard_node_output(
        "load_daily_report_context",
        {
            "market": market,
            "active_prediction_count": len(predictions),
            "recent_verification_count": len(context["recent_verifications"]),
            "prediction_change_count": len(context["prediction_changes"]),
            "gate_context_available": bool(gate_ctx.get("contract_metrics")),
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
    _sentiment_labels = {"bullish": "看涨", "bearish": "看跌", "sideways": "震荡", "neutral": "中性", "unknown": "未知"}
    def sentiment_label(val: str) -> str:
        return _sentiment_labels.get(val, val)
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
    gate_ctx = state.get("gate_context") or {}
    contract_status = ""
    sentiment_status = ""
    memory_status = ""
    sentiment_topics: list[str] = []
    if gate_ctx.get("contract_metrics"):
        cm = gate_ctx["contract_metrics"]
        contract_status = f"合约状态 — 标记价格: {cm.get('last_price')}, 资金费率: {cm.get('funding_rate')}, 持仓量: {cm.get('open_interest')}"
    if gate_ctx.get("sentiment_snapshot"):
        ss = gate_ctx["sentiment_snapshot"]
        sentiment_status = f"情绪快照 — {sentiment_label(ss.get('overall_sentiment', 'unknown'))}, 看涨占比: {ss.get('bull_ratio')}, 看跌占比: {ss.get('bear_ratio')}"
        try:
            sentiment_topics = json.loads(ss.get("dominant_topics") or "[]")
        except (json.JSONDecodeError, TypeError):
            sentiment_topics = []
    if gate_ctx.get("active_memories"):
        mems = gate_ctx["active_memories"]
        memory_status = f"活跃记忆 {len(mems)} 条"
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
        "top_analysts": state.get("top_analysts", []),
        "recent_verifications": recent_verifications,
        "prediction_changes": prediction_changes,
        "contract_status": contract_status,
        "sentiment_status": sentiment_status,
        "memory_status": memory_status,
        "sentiment_topics": sentiment_topics,
        "memory_summary": [item.get("title") for item in gate_ctx.get("active_memories", [])[:5]],
        "generated_at": utc_now(),
        "risk_warnings": market_summary_data.get("risk_notes", []),
        "disclaimer": "本报告仅用于信息整理与历史表现分析，不构成投资建议。",
    }
    output = llm_or_fallback(
        state,
        "daily_report",
        "daily_report",
        {"market_summary": market_summary_data, "indicator_summary": state["market"], "consensus_summary": consensus, "scenario_analysis": scenarios, "recent_verification_results": recent_verifications, "prediction_changes": prediction_changes, "gate_context": gate_ctx},
        fallback,
    )
    report = {**fallback, **(output.get("data") or {})}
    # 规范化 scenarios：LLM 可能返回字符串列表或缺少 scenario/description 的对象，
    # 此时回退使用 scenario_analysis 节点的结构化输出。
    raw_scenarios = report.get("scenarios")
    well_structured = (
        isinstance(raw_scenarios, list)
        and raw_scenarios
        and isinstance(raw_scenarios[0], dict)
        and (raw_scenarios[0].get("scenario") or raw_scenarios[0].get("description"))
    )
    if not well_structured:
        report["scenarios"] = list(scenarios.values()) if scenarios else []
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
