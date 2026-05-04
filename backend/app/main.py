from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Generator

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .config_loader import load_runtime_config
from .database import clear_demo_data, connect, init_db, sync_market_history, sync_market_intervals, sync_real_market_data
from .llm_client import test_model_connection
from .scheduler import scheduler_status, start_scheduler, stop_scheduler
from .schemas import AgentRunCreate, MarketHistorySyncCreate, MarketIntervalsSyncCreate, OpinionCreate, PredictionManualUpdate, ReviewConfirmCreate, ReviewRejectCreate, SettingUpdate
from .services import (
    account_equity_curve,
    account_summary,
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
    list_trades,
    list_verification_results,
    live_market_price,
    market_series,
    market_summary,
    reject_human_review,
    record_ai_account_snapshot,
    record_account_snapshot,
    reset_default_settings,
    resolve_human_review,
    run_agent,
    run_scheduled_task,
    update_setting,
    update_prediction_manual,
    verify_due_predictions,
)

app = FastAPI(title="BTC Agent Decision API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_bit_prefix(request: Request, call_next):
    if request.scope["path"] == "/bit":
        request.scope["path"] = "/"
    elif request.scope["path"].startswith("/bit/"):
        request.scope["path"] = request.scope["path"][len("/bit") :]
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def shutdown() -> None:
    stop_scheduler()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/")
def root() -> dict[str, str]:
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


@app.get("/api/account")
def get_account(
    analyst_id: int | None = Query(default=None, ge=1),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return account_summary(db, analyst_id)


@app.get("/api/account/ai")
def get_ai_account(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return account_summary(db, account_type="ai")


@app.get("/api/account/equity-curve")
def get_account_equity_curve(
    limit: int = Query(default=300, ge=1, le=1000),
    analyst_id: int | None = Query(default=None, ge=1),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return account_equity_curve(db, limit, analyst_id)


@app.get("/api/account/ai/equity-curve")
def get_ai_account_equity_curve(
    limit: int = Query(default=300, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return account_equity_curve(db, limit, account_type="ai")


@app.post("/api/account/snapshot")
def post_account_snapshot(
    analyst_id: int | None = Query(default=None, ge=1),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    return record_account_snapshot(db, analyst_id)


@app.post("/api/account/ai/snapshot")
def post_ai_account_snapshot(db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return record_ai_account_snapshot(db)


@app.post("/api/config/model/test")
def post_test_model_connection() -> dict[str, object]:
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
        last_id = after_id
        while True:
            try:
                with connect() as conn:
                    events = list_agent_stream_events(conn, last_id, 50)
                for event in events:
                    last_id = max(last_id, int(event.get("id") or last_id))
                    yield f"id: {last_id}\nevent: agent_output\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                if not events:
                    heartbeat = {"id": last_id, "message": "等待 AI 输出", "type": "heartbeat"}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1.2)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                payload = {"id": last_id, "message": str(exc), "type": "error"}
                yield f"event: stream_error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/agent/runs/{agent_run_id}/nodes")
def get_agent_run_nodes(agent_run_id: int, db: sqlite3.Connection = Depends(get_db)) -> list[dict[str, object]]:
    return list_agent_node_runs(db, agent_run_id)


@app.get("/api/agent/runs/{agent_run_id}/replay")
def get_agent_run_replay_view(agent_run_id: int, db: sqlite3.Connection = Depends(get_db)) -> dict[str, object]:
    return get_agent_run_replay(db, agent_run_id)


@app.get("/api/trades")
def get_trades(
    limit: int = Query(default=100, ge=1, le=300),
    analyst_id: int | None = Query(default=None, ge=1),
    account_type: str | None = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_trades(db, limit, analyst_id, account_type)


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
    return create_daily_report(db)


@app.get("/api/reports")
def get_reports(
    limit: int = Query(default=50, ge=1, le=200),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, object]]:
    return list_reports(db, limit)
