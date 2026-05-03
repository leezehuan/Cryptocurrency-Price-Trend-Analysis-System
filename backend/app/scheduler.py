from __future__ import annotations

import os
from typing import Any

from .database import connect
from .services import get_setting_value, run_scheduled_task

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover
    BackgroundScheduler = None

_scheduler: Any = None


def _run_task(task_name: str) -> None:
    conn = connect()
    try:
        run_scheduled_task(conn, task_name)
    finally:
        conn.close()


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
        verify_due_minutes = int(get_setting_value(conn, "scheduler.verify_due_minutes", 30))
        account_snapshot_minutes = int(get_setting_value(conn, "scheduler.account_snapshot_minutes", 15))
        daily_report_hour = int(get_setting_value(conn, "scheduler.daily_report_hour", 8))
    finally:
        conn.close()
    if not enabled:
        return
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(_run_task, "interval", minutes=max(1, market_sync_minutes), args=["market_sync"], id="market_sync", replace_existing=True)
    scheduler.add_job(_run_task, "interval", minutes=max(1, verify_due_minutes), args=["verify_due"], id="verify_due", replace_existing=True)
    scheduler.add_job(_run_task, "interval", minutes=max(1, account_snapshot_minutes), args=["account_snapshot"], id="account_snapshot", replace_existing=True)
    scheduler.add_job(_run_task, "cron", hour=max(0, min(23, daily_report_hour)), minute=0, args=["daily_report"], id="daily_report", replace_existing=True)
    scheduler.start()
    _scheduler = scheduler


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
