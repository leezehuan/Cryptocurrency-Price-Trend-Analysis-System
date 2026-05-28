from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .graph_nodes import (
    agent_analysis_explanation_node,
    analyst_consensus_node,
    analyst_identification_node,
    analysis_decision_rule_node,
    btc_relevance_node,
    confidence_parsing_node,
    daily_report_node,
    data_anomaly_check_node,
    display_translation_node,
    evidence_conflict_judgement_node,
    evidence_sufficiency_check_node,
    failure_reason_node,
    finalize_agent_analysis_node,
    human_confirmation_decision_node,
    load_agent_analysis_context_node,
    load_daily_report_context_node,
    load_due_predictions_node,
    opinion_summary_node,
    persist_agent_run_node,
    persist_opinion_node,
    prediction_extraction_node,
    publish_time_extraction_node,
    react_tool_selection_node,
    reflection_critique_node,
    report_market_summary_node,
    rule_based_consensus_scoring_node,
    rule_based_verification_node,
    risk_rule_node,
    save_daily_report_node,
    save_verification_report_node,
    scenario_analysis_node,
    text_cleaning_node,
    time_normalization_node,
    verification_explanation_node,
)

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = None
    StateGraph = None

NodeFn = Callable[[dict[str, Any]], dict[str, Any]]

# 观点入库流程：清洗文本、解析分析师与预测、检查异常，最后入库或转人工确认。
OPINION_INGESTION_NODES: list[tuple[str, NodeFn]] = [
    ("text_cleaning", text_cleaning_node),
    ("btc_relevance", btc_relevance_node),
    ("analyst_identification", analyst_identification_node),
    ("publish_time_extraction", publish_time_extraction_node),
    ("opinion_summary", opinion_summary_node),
    ("prediction_extraction", prediction_extraction_node),
    ("time_normalization", time_normalization_node),
    ("confidence_parsing", confidence_parsing_node),
    ("data_anomaly_check", data_anomaly_check_node),
    ("human_confirmation_decision", human_confirmation_decision_node),
    ("display_translation", display_translation_node),
    ("persist_opinion", persist_opinion_node),
]

# Agent 分析流程：加载行情和待验证预测，生成方向共识，检查证据充分性，按需补充，反思后记录运行。
AGENT_ANALYSIS_NODES: list[tuple[str, NodeFn]] = [
    ("load_agent_analysis_context", load_agent_analysis_context_node),
    ("rule_based_consensus_scoring", rule_based_consensus_scoring_node),
    ("risk_rule", risk_rule_node),
    ("analysis_decision_rule", analysis_decision_rule_node),
    ("agent_analysis_explanation", agent_analysis_explanation_node),
    ("evidence_sufficiency_check", evidence_sufficiency_check_node),
    ("react_tool_selection", react_tool_selection_node),
    ("evidence_conflict_judgement", evidence_conflict_judgement_node),
    ("reflection_critique", reflection_critique_node),
    ("display_translation", display_translation_node),
    ("persist_agent_run", persist_agent_run_node),
    ("finalize_agent_analysis", finalize_agent_analysis_node),
]

# 预测验证流程：找出到期预测，按规则评分，并生成解释性报告。
PREDICTION_VERIFICATION_NODES: list[tuple[str, NodeFn]] = [
    ("load_due_predictions", load_due_predictions_node),
    ("rule_based_verification", rule_based_verification_node),
    ("verification_explanation", verification_explanation_node),
    ("failure_reason", failure_reason_node),
    ("display_translation", display_translation_node),
    ("save_verification_report", save_verification_report_node),
]

# 每日报告流程：汇总行情、共识、情景分析和预测表现，最后保存日报。
DAILY_REPORT_NODES: list[tuple[str, NodeFn]] = [
    ("load_daily_report_context", load_daily_report_context_node),
    ("market_summary", report_market_summary_node),
    ("analyst_consensus", analyst_consensus_node),
    ("scenario_analysis", scenario_analysis_node),
    ("daily_report", daily_report_node),
    ("display_translation", display_translation_node),
    ("save_daily_report", save_daily_report_node),
]


def run_sequential(nodes: list[tuple[str, NodeFn]], state: dict[str, Any]) -> dict[str, Any]:
    # 当 LangGraph 不可用或执行失败时，按列表顺序逐个运行节点。
    current = state
    for _, node in nodes:
        current = node(current)
    return current


def build_langgraph(nodes: list[tuple[str, NodeFn]]) -> Any:
    # 将节点列表编排成线性 LangGraph 工作流。
    if StateGraph is None or END is None:
        return None
    workflow = StateGraph(dict)
    for name, node in nodes:
        workflow.add_node(name, node)
    workflow.set_entry_point(nodes[0][0])
    for index in range(len(nodes) - 1):
        workflow.add_edge(nodes[index][0], nodes[index + 1][0])
    workflow.add_edge(nodes[-1][0], END)
    return workflow.compile()


def run_graph(graph_name: str, nodes: list[tuple[str, NodeFn]], state: dict[str, Any]) -> dict[str, Any]:
    # 统一入口：优先使用 LangGraph，失败时自动回退到顺序执行。
    state.setdefault("graph_name", graph_name)
    state.setdefault("node_outputs", {})
    state.setdefault("errors", [])
    state.setdefault("warnings", [])
    graph = build_langgraph(nodes)
    if graph is None:
        return run_sequential(nodes, state)
    try:
        return graph.invoke(state)
    except Exception as exc:
        state.setdefault("warnings", []).append(f"LangGraph 执行失败，已切换顺序执行：{exc}")
        return run_sequential(nodes, state)


def run_opinion_ingestion_graph(conn: sqlite3.Connection, payload: Any, current_price: float) -> dict[str, Any]:
    # 运行分析师观点解析与入库流程。
    return run_graph(
        "opinion_ingestion",
        OPINION_INGESTION_NODES,
        {
            "conn": conn,
            "trigger": "opinion_created",
            "payload": payload,
            "current_price": current_price,
            "agent_run_id": None,
        },
    )


def run_agent_analysis_graph(
    conn: sqlite3.Connection,
    trigger: str = "manual",
    focus_prediction_ids: list[int] | None = None,
) -> dict[str, Any]:
    return run_graph(
        "agent_analysis",
        AGENT_ANALYSIS_NODES,
        {
            "conn": conn,
            "trigger": trigger,
            "focus_prediction_ids": focus_prediction_ids or [],
            "agent_run_id": None,
        },
    )


def run_prediction_verification_graph(conn: sqlite3.Connection) -> dict[str, Any]:
    # 运行到期预测验证流程。
    return run_graph(
        "prediction_verification",
        PREDICTION_VERIFICATION_NODES,
        {
            "conn": conn,
            "trigger": "verify_due_predictions",
            "agent_run_id": None,
        },
    )


def run_daily_report_graph(conn: sqlite3.Connection, trigger: str = "manual") -> dict[str, Any]:
    # 运行 BTC 每日报告生成流程。
    return run_graph(
        "daily_report",
        DAILY_REPORT_NODES,
        {
            "conn": conn,
            "trigger": trigger,
            "agent_run_id": None,
        },
    )
