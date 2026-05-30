from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections.abc import AsyncGenerator, Generator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config_loader import load_runtime_config
from .database import clear_demo_data, connect, init_db, sync_market_history, sync_market_intervals, sync_real_market_data
from .llm_client import test_model_connection
from .scheduler import scheduler_status, start_scheduler, stop_scheduler
from .schemas import AgentRunCreate, GateCancelAllOrdersRequest, GateCancelOrderRequest, GateSyncCreate, GateUpdateLeverageRequest, GateUpdateMarginRequest, MarketHistorySyncCreate, MarketIntervalsSyncCreate, MarketMemoryCreate, MockTradeExecuteCreate, OpinionCreate, PredictionManualUpdate, ReviewConfirmCreate, ReviewRejectCreate, SettingUpdate
from .services import (
    confirm_human_review,
    create_opinion,
    create_daily_report,
    delete_prediction_manual,
    dashboard,
    get_agent_run_replay,
    get_analyst_replay,
    get_human_review_detail,
    get_prediction_replay,
    get_verification_result,
    latest_market,
    list_agent_runs,
    list_agent_node_runs,
    list_agent_stream_events,
    list_analysts,
    list_human_reviews,
    list_opinions,
    list_predictions,
    list_reports,
    list_scheduled_task_runs,
    list_settings,
    list_verification_results,
    live_market_price,
    market_series,
    market_summary,
    reject_human_review,
    cleanup_stale_settings,
    reset_default_settings,
    resolve_human_review,
    run_agent,
    run_scheduled_task,
    update_setting,
    update_prediction_manual,
    verify_due_predictions,
)
from .gate_sync import (
    active_market_memories,
    gate_mcp_source_status,
    latest_btc_contract_metrics,
    latest_sentiment_snapshot,
    list_analyst_source_accounts,
    recent_followed_user_posts,
    recent_square_posts,
)
from .mock_trade import (
    _get_testnet_client,
    execute_mock_trade,
    generate_trade_advice,
)

_shutdown_event: asyncio.Event | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    init_db()
    start_scheduler()
    yield
    _shutdown_event.set()
    stop_scheduler()


# 创建 FastAPI 应用实例，统一承载行情、预测、Agent 和报告接口。
app = FastAPI(title="BTC Agent Decision API", version="0.1.0", lifespan=lifespan)

# 允许本地前端开发服务器访问后端 API。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_bit_prefix(request: Request, call_next):
    # 兼容前端以 /bit 作为 API 前缀访问后端的部署方式。
    if request.scope["path"] == "/bit":
        request.scope["path"] = "/"
    elif request.scope["path"].startswith("/bit/"):
        request.scope["path"] = request.scope["path"][len("/bit") :]
    return await call_next(request)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    # 为每个请求创建数据库连接，请求结束后自动关闭。
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/")
def root() -> dict[str, str]:
    # 根路径返回服务基本信息，便于快速确认后端可用。
    return {"name": "BTC Agent Decision API", "docs": "/docs"}


@app.get("/api/health")
def health(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    latest = latest_market(db)
    return {"status": "ok", "latest_price": latest["close"], "open_time": latest["open_time"]}


@app.get("/api")
def api_index() -> dict[str, object]:
    return {
        "name": "BTC Agent Decision API",
        "frontend": "http://127.0.0.1:5173/",
        "docs": "/docs",
        "health": "/api/health",
        "dashboard": "/api/dashboard",
    }


@app.get("/api/dashboard")
def get_dashboard(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return dashboard(db)


@app.get("/api/market")
def get_market(
    limit: int = Query(default=120, ge=1, le=500),
    interval: str | None = None,
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    # 返回指定周期的行情 K 线序列，用于前端图表展示。
    return market_series(db, limit, interval)


@app.get("/api/market/summary")
def get_market_summary(
    interval: str | None = None,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return market_summary(db, interval)


@app.get("/api/market/live-price")
def get_live_market_price(
    symbol: str = "BTCUSDT",
    market_type: str = "perpetual",
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return live_market_price(db, symbol, market_type)


@app.post("/api/market/sync-real")
def post_sync_real_market(
    limit: int = Query(default=500, ge=1, le=1000),
    interval: str = "1h",
    replace: bool = False,
    market_type: str = "perpetual",
    symbol: str = "BTCUSDT",
    days: int | None = Query(default=None, ge=1, le=365),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    # 从外部行情源同步真实市场数据，并可选择覆盖已有数据。
    return sync_real_market_data(db, symbol=symbol, interval=interval, limit=limit, replace=replace, market_type=market_type, days=days)


@app.post("/api/market/sync-intervals")
def post_sync_market_intervals(
    payload: MarketIntervalsSyncCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return sync_market_intervals(db, payload.intervals, symbol=payload.symbol, limit=payload.limit, replace=payload.replace, market_type=payload.market_type)


@app.post("/api/market/sync-history")
def post_sync_market_history(
    payload: MarketHistorySyncCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return sync_market_history(
        db,
        payload.intervals,
        symbol=payload.symbol,
        days=payload.days,
        replace=payload.replace,
        market_type=payload.market_type,
    )


@app.get("/api/config")
def get_config() -> dict[str, object]:
    # 返回脱敏后的运行时配置，供设置页展示。
    return load_runtime_config()


@app.get("/api/settings")
def get_settings(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return list_settings(db)


@app.put("/api/settings/{key}")
def put_setting(key: str, payload: SettingUpdate, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return update_setting(db, key, payload.value)


@app.post("/api/settings/reset-defaults")
def post_reset_default_settings(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return reset_default_settings(db)


@app.post("/api/settings/cleanup")
def post_cleanup_settings(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    """清理数据库中不在当前 DEFAULT_SETTINGS 定义中的旧设置项。"""
    return cleanup_stale_settings(db)


@app.post("/api/config/model/test")
def post_test_model_connection() -> dict[str, object]:
    # 使用配置的模型服务执行一次最小化调用，验证模型连接。
    return test_model_connection()


@app.get("/api/scheduler/status")
def get_scheduler_status() -> dict[str, object]:
    return scheduler_status()


@app.get("/api/scheduler/runs")
def get_scheduler_runs(
    limit: int = Query(default=100, ge=1, le=300),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_scheduled_task_runs(db, limit)


@app.post("/api/scheduler/tasks/{task_name}/run")
def post_run_scheduler_task(task_name: str, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return run_scheduled_task(db, task_name)


@app.get("/api/analysts")
def get_analysts(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    return list_analysts(db)


@app.get("/api/analysts/{analyst_id}/replay")
def get_analyst_replay_view(
    analyst_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return get_analyst_replay(db, analyst_id, limit)


@app.post("/api/opinions")
def post_opinion(payload: OpinionCreate, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    # 接收分析师观点，触发观点解析、预测入库和后续 Agent 运行。
    return create_opinion(db, payload)


@app.get("/api/opinions")
def get_opinions(
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_opinions(db, limit)


@app.get("/api/predictions")
def get_predictions(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_predictions(db, status, limit)


@app.get("/api/predictions/{prediction_id}/replay")
def get_prediction_replay_view(prediction_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return get_prediction_replay(db, prediction_id)


@app.put("/api/predictions/{prediction_id}")
def put_prediction_manual(
    prediction_id: int,
    payload: PredictionManualUpdate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    try:
        return update_prediction_manual(db, prediction_id, payload.dict(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/predictions/{prediction_id}")
def delete_prediction(prediction_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    try:
        return delete_prediction_manual(db, prediction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/predictions/verify-due")
def post_verify_due(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return verify_due_predictions(db)


@app.post("/api/agent/run")
def post_agent_run(
    payload: AgentRunCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return run_agent(db, payload.trigger)


@app.get("/api/agent/runs")
def get_agent_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_agent_runs(db, limit)


@app.get("/api/agent/stream-events")
def get_agent_stream_events(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_agent_stream_events(db, after_id, limit)


@app.get("/api/agent/stream")
async def get_agent_stream(after_id: int = Query(default=0, ge=0)) -> StreamingResponse:
    async def event_generator() -> Generator[str, None, None]:
        # 使用 SSE 持续推送 Agent 节点输出；无新事件时发送心跳。
        # 以当前数据库最大 id 为起点，只推送新事件，不回溯历史。
        with connect() as conn:
            max_row = conn.execute("SELECT MAX(id) FROM agent_node_runs").fetchone()
        last_id = max(after_id, int(max_row[0]) if max_row and max_row[0] else 0)
        while True:
            if _shutdown_event is not None and _shutdown_event.is_set():
                break
            try:
                with connect() as conn:
                    events = list_agent_stream_events(conn, last_id, 50)
                for event in events:
                    last_id = max(last_id, int(event.get("id") or last_id))
                    yield f"id: {last_id}\nevent: agent_output\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                if not events:
                    heartbeat = {"id": last_id, "message": "等待 AI 输出", "type": "heartbeat"}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=1.2)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                payload = {"id": last_id, "message": str(exc), "type": "error"}
                yield f"event: stream_error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/agent/runs/{agent_run_id}/nodes")
def get_agent_run_nodes(agent_run_id: int, db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    return list_agent_node_runs(db, agent_run_id)


@app.get("/api/agent/runs/{agent_run_id}/replay")
def get_agent_run_replay_view(agent_run_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return get_agent_run_replay(db, agent_run_id)


@app.get("/api/reviews")
def get_reviews(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_human_reviews(db, status, limit)


@app.get("/api/reviews/{review_id}")
def get_review_detail(review_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return get_human_review_detail(db, review_id)


@app.post("/api/reviews/{review_id}/confirm")
def post_confirm_review(
    review_id: int,
    payload: ReviewConfirmCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    # 人工确认解析结果后，将草稿预测正式入库并触发 Agent。
    return confirm_human_review(db, review_id, payload.analyst_name, payload.source_url, payload.published_at, payload.predictions)


@app.post("/api/reviews/{review_id}/reject")
def post_reject_review(
    review_id: int,
    payload: ReviewRejectCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return reject_human_review(db, review_id, payload.reason)


@app.post("/api/reviews/{review_id}/resolve")
def post_resolve_review(review_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return resolve_human_review(db, review_id)


@app.get("/api/verification-results")
def get_verification_results(
    limit: int = Query(default=100, ge=1, le=300),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_verification_results(db, limit)


@app.get("/api/predictions/{prediction_id}/verification")
def get_prediction_verification(prediction_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return get_verification_result(db, prediction_id)


@app.post("/api/reports/daily")
def post_daily_report(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    # 手动生成一份 BTC 每日报告。
    return create_daily_report(db)


@app.get("/api/reports")
def get_reports(
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_reports(db, limit)


# ---------------------------------------------------------------------------
# Gate MCP 数据源 API
# ---------------------------------------------------------------------------

@app.get("/api/sources/gate/status")
def get_gate_status(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return gate_mcp_source_status(db)


@app.post("/api/sources/gate/sync")
def post_gate_sync(
    payload: GateSyncCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    allowed = {
        "gate_btc_contract_sync", "gate_news_sync",
        "gate_square_hot_sync", "market_sentiment_build",
        "market_memory_compact", "gate_square_user_sync",
        "gate_info_sync",
    }
    results: dict[str, object] = {}
    for task in payload.tasks:
        if task not in allowed:
            results[task] = {"error": f"unknown gate task: {task}"}
            continue
        results[task] = run_scheduled_task(db, task)
    return results


@app.get("/api/market/btc-contract")
def get_btc_contract(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return latest_btc_contract_metrics(db)


@app.get("/api/square/hot")
def get_square_hot(
    limit: int = Query(default=20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return recent_square_posts(db, limit)


@app.get("/api/sentiment/market")
def get_market_sentiment(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return latest_sentiment_snapshot(db)


@app.get("/api/memory")
def get_memories(
    limit: int = Query(default=20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return active_market_memories(db, limit=limit)


@app.post("/api/memory")
def post_memory(
    payload: MarketMemoryCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    from .database import utc_now
    now = utc_now()
    cursor = db.execute(
        """
        INSERT INTO market_memories (
            memory_type, symbol, title, content, importance,
            valid_from, valid_until, is_active, source, payload,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'manual', '{}', ?, ?)
        """,
        (
            payload.memory_type, payload.symbol, payload.title,
            payload.content, payload.importance,
            payload.valid_from, payload.valid_until,
            now, now,
        ),
    )
    db.commit()
    return {"id": cursor.lastrowid, "created": True}


# ---------------------------------------------------------------------------
# Phase A: 新增 API 端点
# ---------------------------------------------------------------------------

@app.get("/api/square/user-opinions")
def get_square_user_opinions(
    limit: int = Query(default=20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return recent_followed_user_posts(db, limit)


@app.get("/api/sources/gate/accounts")
def get_gate_accounts(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    return list_analyst_source_accounts(db)


@app.post("/api/memory/refresh")
def post_memory_refresh(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return run_scheduled_task(db, "market_memory_compact")


@app.get("/api/agent/runs/{run_id}/evidence")
def get_agent_run_evidence(
    run_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    run_row = db.execute(
        "SELECT id, input_snapshot, output_snapshot FROM agent_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not run_row:
        return {"error": "agent run not found"}
    input_snap = json.loads(run_row["input_snapshot"] or "{}")
    output_snap = json.loads(run_row["output_snapshot"] or "{}")
    node_rows = db.execute(
        "SELECT node_name, input_snapshot, output_snapshot FROM agent_node_runs WHERE agent_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    nodes = []
    for nr in node_rows:
        output_data = json.loads(nr["output_snapshot"] or "{}")
        nodes.append({
            "node_name": nr["node_name"],
            "input_snapshot": json.loads(nr["input_snapshot"] or "{}"),
            "output_snapshot": output_data,
        })
    evidence_refs: list[str] = []
    for node in nodes:
        out = node.get("output_snapshot") or {}
        if isinstance(out, dict):
            evidence_refs.extend(out.get("evidence", []))
    return {
        "agent_run_id": run_id,
        "input_snapshot": input_snap,
        "output_snapshot": output_snap,
        "node_runs": nodes,
        "evidence_refs": evidence_refs,
    }


@app.get("/api/agent/runs/{run_id}/reflection")
def get_agent_run_reflection(
    run_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    run_row = db.execute(
        "SELECT id, output_snapshot FROM agent_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not run_row:
        return {"error": "agent run not found"}
    output_snap = json.loads(run_row["output_snapshot"] or "{}")
    node_rows = db.execute(
        """
        SELECT node_name, input_snapshot, output_snapshot
        FROM agent_node_runs
        WHERE agent_run_id = ? AND node_name = 'reflection_critique'
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    nodes = [
        {
            "node_name": row["node_name"],
            "input_snapshot": json.loads(row["input_snapshot"] or "{}"),
            "output_snapshot": json.loads(row["output_snapshot"] or "{}"),
        }
        for row in node_rows
    ]
    return {
        "agent_run_id": run_id,
        "reflection": output_snap.get("reflection", {}),
        "node_runs": nodes,
    }


# ---------------------------------------------------------------------------
# Mock Trade API
# ---------------------------------------------------------------------------

@app.post("/api/mock-trade/advice")
def post_mock_trade_advice(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return generate_trade_advice(db)


@app.post("/api/mock-trade/execute")
def post_mock_trade_execute(
    payload: MockTradeExecuteCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return execute_mock_trade(db, payload.direction, payload.size, payload.price_type, payload.amount_usdt, payload.price)


# ---------------------------------------------------------------------------
# Gate Testnet Proxy API — 直接查询/操作 Gate Testnet 账户
# ---------------------------------------------------------------------------

@app.get("/api/gate/account")
def get_gate_account(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    client = _get_testnet_client(db)
    if client is None:
        return {"error": "未配置 Testnet API Key"}
    return client.get_futures_account()


@app.get("/api/gate/positions")
def get_gate_positions(db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    client = _get_testnet_client(db)
    if client is None:
        return [{"error": "未配置 Testnet API Key"}]
    return client.list_positions()


@app.get("/api/gate/orders")
def get_gate_orders(
    status: str = Query(default="open", pattern="^(open|finished)$"),
    contract: str = Query(default="BTC_USDT"),
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    client = _get_testnet_client(db)
    if client is None:
        return [{"error": "未配置 Testnet API Key"}]
    return client.list_futures_orders(status=status, contract=contract, limit=limit)


@app.get("/api/gate/orders/{order_id}")
def get_gate_order(order_id: str, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    client = _get_testnet_client(db)
    if client is None:
        return {"error": "未配置 Testnet API Key"}
    return client.get_futures_order(order_id)


@app.post("/api/gate/orders/cancel")
def cancel_gate_order(payload: GateCancelOrderRequest, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    client = _get_testnet_client(db)
    if client is None:
        return {"error": "未配置 Testnet API Key"}
    return client.cancel_futures_order(order_id=payload.order_id)


@app.post("/api/gate/orders/cancel-all")
def cancel_all_gate_orders(payload: GateCancelAllOrdersRequest, db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    client = _get_testnet_client(db)
    if client is None:
        return [{"error": "未配置 Testnet API Key"}]
    return client.cancel_all_futures_orders(contract=payload.contract)


@app.post("/api/gate/positions/leverage")
def update_gate_leverage(payload: GateUpdateLeverageRequest, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    client = _get_testnet_client(db)
    if client is None:
        return {"error": "未配置 Testnet API Key"}
    return client.update_position_leverage(leverage=payload.leverage, cross_leverage_limit=payload.cross_leverage_limit)


@app.post("/api/gate/positions/margin")
def update_gate_margin(payload: GateUpdateMarginRequest, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    client = _get_testnet_client(db)
    if client is None:
        return {"error": "未配置 Testnet API Key"}
    return client.update_position_margin(change=payload.change)


@app.get("/api/gate/trades")
def get_gate_trades(
    contract: str = Query(default="BTC_USDT"),
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    client = _get_testnet_client(db)
    if client is None:
        return [{"error": "未配置 Testnet API Key"}]
    return client.get_my_trades(contract=contract, limit=limit)
