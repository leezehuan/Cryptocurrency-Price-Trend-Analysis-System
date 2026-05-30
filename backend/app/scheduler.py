from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import connect, parse_dt
from .services import get_setting_value, run_scheduled_task

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover
    BackgroundScheduler = None

_scheduler: Any = None
DUE_VERIFICATION_JOB_ID = "verify_due_at_next_prediction"


def _run_task(task_name: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        return run_scheduled_task(conn, task_name)
    finally:
        conn.close()


def _next_pending_verification_time() -> datetime | None:
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT verification_time
            FROM predictions
            WHERE status = 'pending'
            ORDER BY verification_time ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return parse_dt(str(row["verification_time"]))


def _due_verification_retry_time() -> datetime:
    conn = connect()
    try:
        retry_minutes = int(get_setting_value(conn, "scheduler.verify_due_minutes", 30))
    finally:
        conn.close()
    return datetime.now(timezone.utc) + timedelta(minutes=max(1, retry_minutes))


def _replace_due_verification_job(run_at: datetime) -> None:
    scheduler = _scheduler
    if scheduler is None:
        return
    now = datetime.now(timezone.utc)
    scheduler.add_job(
        _run_due_verification,
        "date",
        run_date=run_at if run_at > now else now + timedelta(seconds=1),
        id=DUE_VERIFICATION_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
    )


def schedule_next_due_verification() -> None:
    scheduler = _scheduler
    if scheduler is None:
        return
    run_at = _next_pending_verification_time()
    if run_at is None:
        if scheduler.get_job(DUE_VERIFICATION_JOB_ID):
            scheduler.remove_job(DUE_VERIFICATION_JOB_ID)
        return
    _replace_due_verification_job(run_at)


def _run_due_verification() -> None:
    result = _run_task("verify_due") or {}
    if result.get("status") == "failed":
        _replace_due_verification_job(_due_verification_retry_time())
        return
    schedule_next_due_verification()


def scheduler_enabled_for_process() -> bool:
    if os.environ.get("RUN_MAIN") == "false":
        return False
    return True


def start_scheduler() -> None:
    global _scheduler
    if BackgroundScheduler is None or _scheduler is not None or not scheduler_enabled_for_process():
        return
    conn = connect()
    try:
        enabled = bool(get_setting_value(conn, "scheduler.enabled", False))
        market_sync_minutes = int(get_setting_value(conn, "scheduler.market_sync_minutes", 60))
        daily_report_hour = int(get_setting_value(conn, "scheduler.daily_report_hour", 8))
    finally:
        conn.close()
    scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler = scheduler
    if enabled:
        scheduler.add_job(_run_task, "interval", minutes=max(1, market_sync_minutes), args=["market_sync"], id="market_sync", replace_existing=True)
        scheduler.add_job(_run_task, "cron", hour=max(0, min(23, daily_report_hour)), minute=0, args=["daily_report"], id="daily_report", replace_existing=True)
    # Gate MCP 数据源定时任务（需同时启用 scheduler 和 gate_mcp）
    conn2 = connect()
    try:
        gate_mcp_enabled = bool(get_setting_value(conn2, "gate_mcp.enabled", False))
        gate_btc_minutes = int(get_setting_value(conn2, "scheduler.gate_btc_sync_minutes", 5))
        gate_news_minutes = int(get_setting_value(conn2, "scheduler.gate_news_sync_minutes", 30))
        gate_square_minutes = int(get_setting_value(conn2, "scheduler.gate_square_sync_minutes", 60))
        sentiment_minutes = int(get_setting_value(conn2, "scheduler.sentiment_build_minutes", 15))
        memory_compact_hours = int(get_setting_value(conn2, "scheduler.memory_compact_hours", 6))
        gate_info_minutes = int(get_setting_value(conn2, "scheduler.gate_info_sync_minutes", 30))
        gate_square_user_minutes = int(get_setting_value(conn2, "scheduler.gate_square_user_sync_minutes", 15))
    finally:
        conn2.close()
    if enabled and gate_mcp_enabled:
        scheduler.add_job(_run_task, "interval", minutes=max(1, gate_btc_minutes), args=["gate_btc_contract_sync"], id="gate_btc_contract_sync", replace_existing=True)
        scheduler.add_job(_run_task, "interval", minutes=max(1, gate_news_minutes), args=["gate_news_sync"], id="gate_news_sync", replace_existing=True)
        scheduler.add_job(_run_task, "interval", minutes=max(5, gate_square_minutes), args=["gate_square_hot_sync"], id="gate_square_hot_sync", replace_existing=True)
        scheduler.add_job(_run_task, "interval", minutes=max(1, sentiment_minutes), args=["market_sentiment_build"], id="market_sentiment_build", replace_existing=True)
        scheduler.add_job(_run_task, "interval", hours=max(1, memory_compact_hours), args=["market_memory_compact"], id="market_memory_compact", replace_existing=True)
        scheduler.add_job(_run_task, "interval", minutes=max(5, gate_info_minutes), args=["gate_info_sync"], id="gate_info_sync", replace_existing=True)
        scheduler.add_job(_run_task, "interval", minutes=max(5, gate_square_user_minutes), args=["gate_square_user_sync"], id="gate_square_user_sync", replace_existing=True)
    # Mock Trade 定时同步（需启用 scheduler 和 mock_trade 且配置了 API Key）
    conn3 = connect()
    try:
        mock_trade_enabled = bool(get_setting_value(conn3, "mock_trade.enabled", False))
        mock_trade_api_key = str(get_setting_value(conn3, "mock_trade.testnet_api_key", ""))
        mock_trade_sync_minutes = int(get_setting_value(conn3, "scheduler.mock_trade_sync_minutes", 5))
    finally:
        conn3.close()
    if enabled and mock_trade_enabled and mock_trade_api_key:
        scheduler.add_job(_run_task, "interval", minutes=max(1, mock_trade_sync_minutes), args=["mock_trade_sync"], id="mock_trade_sync", replace_existing=True)
    schedule_next_due_verification()
    scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict[str, Any]:
    if BackgroundScheduler is None:
        return {"available": False, "running": False, "jobs": [], "reason": "apscheduler is not installed"}
    if _scheduler is None:
        return {"available": True, "running": False, "jobs": []}
    return {
        "available": True,
        "running": _scheduler.running,
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in _scheduler.get_jobs()
        ],
    }
