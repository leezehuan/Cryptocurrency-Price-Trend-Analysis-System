from __future__ import annotations

import os
import sqlite3
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from psycopg import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.database import DB_PATH, connect, init_db

TABLES = [
    "analysts",
    "raw_opinions",
    "predictions",
    "prediction_versions",
    "market_data",
    "indicators",
    "virtual_trades",
    "virtual_account_snapshots",
    "agent_runs",
    "agent_node_runs",
    "human_review_items",
    "opinion_review_drafts",
    "verification_reports",
    "verification_results",
    "agent_reports",
    "settings",
    "scheduled_task_runs",
]

SEQUENCE_TABLES = [table for table in TABLES if table != "settings"]


def masked_database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or ""
    parsed = urllib.parse.urlsplit(url)
    if not parsed.password:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    auth = parsed.username or ""
    if auth:
        auth = f"{auth}:***@"
    return urllib.parse.urlunsplit((parsed.scheme, f"{auth}{host}", parsed.path, parsed.query, parsed.fragment))


def sqlite_rows(path: Path, table: str) -> tuple[list[str], list[sqlite3.Row]]:
    source = sqlite3.connect(path)
    source.row_factory = sqlite3.Row
    try:
        columns = [row["name"] for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
        rows = source.execute(f"SELECT * FROM {table} ORDER BY rowid ASC").fetchall()
        return columns, rows
    finally:
        source.close()


def reset_sequence(conn: Any, table: str) -> None:
    if "DATABASE_URL" not in os.environ and "POSTGRES_DSN" not in os.environ:
        return
    conn.execute(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', 'id'),
            COALESCE((SELECT MAX(id) FROM {table}), 1),
            (SELECT COUNT(*) FROM {table}) > 0
        )
        """
    )


def normalize_snapshot_types(conn: Any) -> None:
    conn.execute(
        """
        UPDATE virtual_account_snapshots
        SET snapshot_type = 'analyst'
        WHERE analyst_id IS NOT NULL
          AND (snapshot_type IS NULL OR snapshot_type = 'aggregate')
        """
    )


def migrate(sqlite_path: Path) -> None:
    if "DATABASE_URL" not in os.environ and "POSTGRES_DSN" not in os.environ:
        raise RuntimeError("请先设置 DATABASE_URL 或 POSTGRES_DSN，避免误把 SQLite 源库作为迁移目标。")
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    try:
        init_db()
        conn = connect()
    except OperationalError as exc:
        raise RuntimeError(
            "无法连接 PostgreSQL，请检查 DATABASE_URL/POSTGRES_DSN 的主机、端口、数据库名、用户名和密码。"
            f"当前连接串：{masked_database_url()}"
        ) from exc
    try:
        for table in reversed(TABLES):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        for table in TABLES:
            columns, rows = sqlite_rows(sqlite_path, table)
            if not rows:
                print(f"{table}: 0")
                continue
            column_sql = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            query = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
            values = [tuple(row[column] for column in columns) for row in rows]
            conn.executemany(query, values)
            print(f"{table}: {len(rows)}")
        conn.commit()
        normalize_snapshot_types(conn)
        conn.commit()
        for table in SEQUENCE_TABLES:
            reset_sequence(conn, table)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    sqlite_path = Path(os.getenv("SQLITE_DB_PATH", str(DB_PATH)))
    migrate(sqlite_path)
    print("sqlite to postgres migration complete")


if __name__ == "__main__":
    main()
