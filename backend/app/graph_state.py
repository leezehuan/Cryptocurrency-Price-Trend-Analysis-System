from __future__ import annotations

import sqlite3
from typing import Any, NotRequired, TypedDict


class NodeOutput(TypedDict):
    success: bool
    node_name: str
    model_confidence: float
    needs_human_review: bool
    data: dict[str, Any]
    warnings: list[str]
    errors: list[str]
    evidence: list[str]


class BaseGraphState(TypedDict):
    conn: sqlite3.Connection
    graph_name: str
    trigger: str
    agent_run_id: NotRequired[int | None]
    node_outputs: NotRequired[dict[str, NodeOutput]]
    errors: NotRequired[list[str]]
    warnings: NotRequired[list[str]]


class OpinionIngestionState(BaseGraphState):
    payload: Any
    current_price: float
    raw_opinion_id: NotRequired[int | None]
    analyst: NotRequired[dict[str, Any]]
    parsed_predictions: NotRequired[list[dict[str, Any]]]
    prediction_ids: NotRequired[list[int]]
    predictions: NotRequired[list[dict[str, Any]]]
    needs_user_confirmation: NotRequired[bool]
    review_item: NotRequired[dict[str, Any] | None]
    result: NotRequired[dict[str, Any]]


class AgentAnalysisState(BaseGraphState):
    focus_prediction_ids: NotRequired[list[int]]
    market: NotRequired[dict[str, Any]]
    pending_predictions: NotRequired[list[dict[str, Any]]]
    signal: NotRequired[dict[str, Any]]
    decision: NotRequired[str]
    decision_label: NotRequired[str]
    risk: NotRequired[str]
    focus_prediction: NotRequired[dict[str, Any] | None]
    analysis_event: NotRequired[str]
    result: NotRequired[dict[str, Any]]
    gate_context: NotRequired[dict[str, Any]]
    react_tools_used: NotRequired[list[str]]
    react_tool_results: NotRequired[dict[str, Any]]
    evidence_conflict: NotRequired[dict[str, Any]]
    reflection: NotRequired[dict[str, Any]]


class VerificationState(BaseGraphState):
    due_predictions: NotRequired[list[dict[str, Any]]]
    verified: NotRequired[list[dict[str, Any]]]
    result: NotRequired[dict[str, Any]]


class DailyReportState(BaseGraphState):
    market: NotRequired[dict[str, Any]]
    active_predictions: NotRequired[list[dict[str, Any]]]
    report: NotRequired[dict[str, Any]]
    result: NotRequired[dict[str, Any]]
