from __future__ import annotations

import math
import sqlite3
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bit_agent.sqlite3"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
GATEIO_SPOT_CANDLESTICKS_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

DEFAULT_SETTINGS: dict[str, tuple[Any, str, str]] = {
    "scheduler.enabled": (False, "bool", "是否启用后台定时任务"),
    "scheduler.market_sync_minutes": (60, "int", "行情同步间隔分钟数"),
    "scheduler.verify_due_minutes": (30, "int", "到期预测验证间隔分钟数"),
    "scheduler.daily_report_hour": (8, "int", "每日自动报告 UTC 小时"),
    "prediction.horizon_intraday_days": (1, "int", "日内预测默认验证天数"),
    "prediction.horizon_short_days": (7, "int", "短期预测默认验证天数"),
    "prediction.horizon_medium_days": (30, "int", "中期预测默认验证天数"),
    "prediction.horizon_long_days": (90, "int", "长期预测默认验证天数"),
    "prediction.direction_threshold": (0.01, "float", "方向验证涨跌阈值"),
    "prediction.target_tolerance": (0.05, "float", "目标价接近率容忍区间"),
    "prediction.target_change_threshold_pct": (0.03, "float", "观点变化检测目标价相对变化阈值"),
    "prediction.confidence_change_threshold_tiers": (2, "int", "观点变化检测置信度档位变化阈值"),
    "prediction.horizon_change_penalty_factor": (0.75, "float", "观点变化检测周期变化严重度因子"),
    "account.initial_balance_usdt": (10000, "float", "合约虚拟账户初始权益"),
    "account.leverage": (2, "float", "合约虚拟账户默认杠杆倍数"),
    "account.market_type": ("perpetual", "str", "合约虚拟账户使用的行情类型"),
    "account.snapshot_interval_minutes": (15, "int", "账户权益快照刷新间隔分钟数"),
    "trade.notional_usdt": (1000, "float", "单次虚拟交易名义本金"),
    "trade.taker_fee_rate": (0.0005, "float", "虚拟交易 taker 手续费率"),
    "trade.funding_fee_enabled": (True, "bool", "是否按行情资金费率估算合约资金费"),
    "market.symbol": ("BTCUSDT", "str", "默认行情交易对"),
    "market.intervals": (["1m", "5m", "15m", "1h", "4h", "1d"], "json", "默认同步行情周期列表"),
    "market.default_interval": ("1h", "str", "默认展示行情周期"),
    "scheduler.account_snapshot_minutes": (15, "int", "账户权益快照任务间隔分钟数"),
}

ID_TABLES = {
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
    "scheduled_task_runs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PostgresCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None) -> None:
        self.cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self) -> dict[str, Any] | None:
        return self.cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.cursor.fetchall())


class PostgresConnection:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def execute(self, query: str, params: Any = ()) -> PostgresCursor:
        converted = convert_sql_for_postgres(query, returning_id=True)
        cursor = self.conn.cursor()
        cursor.execute(converted, params or ())
        lastrowid = None
        if "RETURNING ID" in converted.upper():
            row = cursor.fetchone()
            if row and "id" in row:
                lastrowid = int(row["id"])
        return PostgresCursor(cursor, lastrowid)

    def executemany(self, query: str, params: list[Any] | tuple[Any, ...]) -> PostgresCursor:
        converted = convert_sql_for_postgres(query, returning_id=False)
        cursor = self.conn.cursor()
        cursor.executemany(converted, params)
        return PostgresCursor(cursor)

    def executescript(self, script: str) -> None:
        for statement in convert_schema_for_postgres(script).split(";"):
            if statement.strip():
                self.conn.execute(statement)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def database_url() -> str | None:
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN")


def database_backend() -> str:
    return "postgres" if database_url() else "sqlite"


def convert_placeholders(query: str) -> str:
    return re.sub(r"\?", "%s", query)


def insert_target_table(query: str) -> str | None:
    match = re.search(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", query, re.IGNORECASE)
    return match.group(1).lower() if match else None


def append_returning_id(query: str) -> str:
    stripped = query.strip()
    upper = stripped.upper()
    if not upper.startswith("INSERT") or "RETURNING" in upper:
        return query
    table = insert_target_table(query)
    if table not in ID_TABLES:
        return query
    return f"{query.rstrip().rstrip(';')} RETURNING id"


def convert_sql_for_postgres(query: str, returning_id: bool = False) -> str:
    converted = query
    converted = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO\s+settings", "INSERT INTO settings", converted, flags=re.IGNORECASE)
    converted = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO\s+market_data", "INSERT INTO market_data", converted, flags=re.IGNORECASE)
    converted = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO\s+indicators", "INSERT INTO indicators", converted, flags=re.IGNORECASE)
    if re.search(r"\bINSERT\s+INTO\s+settings\b", converted, re.IGNORECASE) and "ON CONFLICT" not in converted.upper():
        converted = f"{converted.rstrip().rstrip(';')} ON CONFLICT(key) DO NOTHING"
    if re.search(r"\bINSERT\s+INTO\s+market_data\b", converted, re.IGNORECASE) and "ON CONFLICT" not in converted.upper():
        converted = f"{converted.rstrip().rstrip(';')} ON CONFLICT(symbol, market_type, interval, open_time) DO NOTHING"
    if re.search(r"\bINSERT\s+INTO\s+indicators\b", converted, re.IGNORECASE) and "ON CONFLICT" not in converted.upper():
        converted = f"{converted.rstrip().rstrip(';')} ON CONFLICT(market_data_id) DO UPDATE SET ma_20 = excluded.ma_20, ema_20 = excluded.ema_20, rsi_14 = excluded.rsi_14, macd = excluded.macd, atr_14 = excluded.atr_14, bb_upper = excluded.bb_upper, bb_lower = excluded.bb_lower, created_at = excluded.created_at"
    converted = convert_placeholders(converted)
    converted = converted.replace("MAX(0, stability_score - %s)", "GREATEST(0, stability_score - %s)")
    return append_returning_id(converted) if returning_id else converted


def convert_schema_for_postgres(script: str) -> str:
    converted = script
    converted = converted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    converted = converted.replace("REAL", "DOUBLE PRECISION")
    return converted


def connect() -> Any:
    url = database_url()
    if url:
        if psycopg is None or dict_row is None:
            raise RuntimeError("DATABASE_URL 已设置，但未安装 psycopg。请运行 pip install -r backend/requirements.txt")
        return PostgresConnection(psycopg.connect(url, row_factory=dict_row))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def setting_value_to_text(value: Any, value_type: str) -> str:
    if value_type == "json":
        return json.dumps(value, ensure_ascii=False)
    if value_type == "bool":
        return "true" if bool(value) else "false"
    return str(value)


def seed_default_settings(conn: sqlite3.Connection) -> None:
    for key, (value, value_type, description) in DEFAULT_SETTINGS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (key, value, value_type, description, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, setting_value_to_text(value, value_type), value_type, description, utc_now()),
        )
    intervals_row = conn.execute("SELECT value FROM settings WHERE key = 'market.intervals'").fetchone()
    if intervals_row:
        try:
            intervals = json.loads(str(intervals_row["value"]))
        except json.JSONDecodeError:
            intervals = []
        if intervals == ["1h", "4h", "1d"]:
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'market.intervals'",
                (setting_value_to_text(DEFAULT_SETTINGS["market.intervals"][0], "json"), utc_now()),
            )


def migrate_market_data_unique_constraint(conn: sqlite3.Connection) -> None:
    if database_backend() != "sqlite":
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'market_data'").fetchone()
    if not row or "open_time TEXT NOT NULL UNIQUE" not in (row["sql"] or ""):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE IF EXISTS indicators")
    conn.execute("ALTER TABLE market_data RENAME TO market_data_old")
    conn.execute(
        """
        CREATE TABLE market_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            funding_rate REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(symbol, market_type, interval, open_time)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO market_data (
            id, symbol, market_type, interval, open_time, open, high, low, close, volume, funding_rate, created_at
        )
        SELECT id, symbol, market_type, interval, open_time, open, high, low, close, volume, funding_rate, created_at
        FROM market_data_old
        ORDER BY open_time ASC
        """
    )
    conn.execute("DROP TABLE market_data_old")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_data_id INTEGER NOT NULL UNIQUE,
            ma_20 REAL,
            ema_20 REAL,
            rsi_14 REAL,
            macd REAL,
            atr_14 REAL,
            bb_upper REAL,
            bb_lower REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (market_data_id) REFERENCES market_data(id)
        )
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    rebuild_indicators(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_data_time ON market_data(open_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_data_symbol_interval_time ON market_data(symbol, market_type, interval, open_time)")
    conn.commit()


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if database_backend() == "postgres":
        rows = conn.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    else:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_virtual_trade_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "virtual_trades")
    columns: dict[str, str] = {
        "account_type": "TEXT NOT NULL DEFAULT 'analyst'",
        "notional_usdt": "REAL",
        "leverage": "REAL",
        "margin": "REAL",
        "realized_pnl": "REAL NOT NULL DEFAULT 0",
        "unrealized_pnl": "REAL NOT NULL DEFAULT 0",
        "opened_equity": "REAL",
        "closed_equity": "REAL",
        "wallet_balance_after": "REAL",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE virtual_trades ADD COLUMN {name} {definition}")
    conn.execute("UPDATE virtual_trades SET account_type = 'analyst' WHERE analyst_id IS NOT NULL AND (account_type IS NULL OR account_type = '')")
    conn.execute("UPDATE virtual_trades SET account_type = 'unassigned' WHERE analyst_id IS NULL AND (account_type IS NULL OR account_type = '' OR account_type = 'analyst')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_account_type_status ON virtual_trades(account_type, status)")
    conn.commit()


def ensure_virtual_account_snapshot_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "virtual_account_snapshots")
    columns: dict[str, str] = {
        "analyst_id": "INTEGER",
        "snapshot_type": "TEXT NOT NULL DEFAULT 'aggregate'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE virtual_account_snapshots ADD COLUMN {name} {definition}")
    conn.execute("UPDATE virtual_account_snapshots SET snapshot_type = 'analyst' WHERE analyst_id IS NOT NULL AND snapshot_type = 'aggregate'")
    conn.execute("UPDATE virtual_account_snapshots SET snapshot_type = 'unassigned' WHERE analyst_id IS NULL AND snapshot_type = 'analyst'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_virtual_account_snapshots_analyst_time ON virtual_account_snapshots(analyst_id, snapshot_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_virtual_account_snapshots_type_time ON virtual_account_snapshots(snapshot_type, snapshot_time)")
    conn.commit()


def ensure_analyst_metric_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "analysts")
    columns: dict[str, str] = {
        "hard_win_rate": "REAL NOT NULL DEFAULT 0",
        "weighted_win_rate": "REAL NOT NULL DEFAULT 0",
        "direction_accuracy": "REAL NOT NULL DEFAULT 0",
        "target_accuracy": "REAL NOT NULL DEFAULT 0",
        "modification_rate": "REAL NOT NULL DEFAULT 0",
        "intraday_win_rate": "REAL NOT NULL DEFAULT 0",
        "short_win_rate": "REAL NOT NULL DEFAULT 0",
        "medium_win_rate": "REAL NOT NULL DEFAULT 0",
        "long_win_rate": "REAL NOT NULL DEFAULT 0",
        "average_prediction_score": "REAL NOT NULL DEFAULT 0",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE analysts ADD COLUMN {name} {definition}")
    conn.commit()


def ensure_verification_result_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "verification_results")
    columns: dict[str, str] = {
        "price_change_pct": "REAL NOT NULL DEFAULT 0",
        "closest_price": "REAL",
        "target_distance_pct": "REAL",
        "quality_label": "TEXT NOT NULL DEFAULT 'unknown'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE verification_results ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_results_quality ON verification_results(quality_label, created_at)")
    conn.commit()


def ensure_prediction_version_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "prediction_versions")
    columns: dict[str, str] = {
        "old_horizon": "TEXT",
        "old_confidence": "TEXT",
        "new_direction": "TEXT",
        "new_target_price": "REAL",
        "new_horizon": "TEXT",
        "new_confidence": "TEXT",
        "change_type": "TEXT NOT NULL DEFAULT 'direction_reversal'",
        "change_severity": "REAL NOT NULL DEFAULT 1",
        "payload": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE prediction_versions ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prediction_versions_change_type ON prediction_versions(change_type, created_at)")
    conn.commit()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analysts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                source TEXT,
                total_score REAL NOT NULL DEFAULT 50,
                direction_win_rate REAL NOT NULL DEFAULT 0,
                target_hit_rate REAL NOT NULL DEFAULT 0,
                stability_score REAL NOT NULL DEFAULT 100,
                virtual_roi REAL NOT NULL DEFAULT 0,
                hard_win_rate REAL NOT NULL DEFAULT 0,
                weighted_win_rate REAL NOT NULL DEFAULT 0,
                direction_accuracy REAL NOT NULL DEFAULT 0,
                target_accuracy REAL NOT NULL DEFAULT 0,
                modification_rate REAL NOT NULL DEFAULT 0,
                intraday_win_rate REAL NOT NULL DEFAULT 0,
                short_win_rate REAL NOT NULL DEFAULT 0,
                medium_win_rate REAL NOT NULL DEFAULT 0,
                long_win_rate REAL NOT NULL DEFAULT 0,
                average_prediction_score REAL NOT NULL DEFAULT 0,
                latest_opinion TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_opinions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                source_url TEXT,
                published_at TEXT NOT NULL,
                parsed_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (analyst_id) REFERENCES analysts(id)
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst_id INTEGER NOT NULL,
                raw_opinion_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                target_price REAL,
                horizon TEXT NOT NULL,
                current_price REAL NOT NULL,
                verification_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                is_modified INTEGER NOT NULL DEFAULT 0,
                is_success INTEGER,
                confidence TEXT NOT NULL DEFAULT 'medium',
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (analyst_id) REFERENCES analysts(id),
                FOREIGN KEY (raw_opinion_id) REFERENCES raw_opinions(id)
            );

            CREATE TABLE IF NOT EXISTS prediction_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                old_direction TEXT NOT NULL,
                old_target_price REAL,
                old_horizon TEXT,
                old_confidence TEXT,
                new_prediction_id INTEGER NOT NULL,
                new_direction TEXT,
                new_target_price REAL,
                new_horizon TEXT,
                new_confidence TEXT,
                change_type TEXT NOT NULL DEFAULT 'direction_reversal',
                change_severity REAL NOT NULL DEFAULT 1,
                reason TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id),
                FOREIGN KEY (new_prediction_id) REFERENCES predictions(id)
            );

            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                funding_rate REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, market_type, interval, open_time)
            );

            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_data_id INTEGER NOT NULL UNIQUE,
                ma_20 REAL,
                ema_20 REAL,
                rsi_14 REAL,
                macd REAL,
                atr_14 REAL,
                bb_upper REAL,
                bb_lower REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (market_data_id) REFERENCES market_data(id)
            );

            CREATE TABLE IF NOT EXISTS virtual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_run_id INTEGER,
                prediction_id INTEGER,
                analyst_id INTEGER,
                account_type TEXT NOT NULL DEFAULT 'analyst',
                action TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                notional_usdt REAL,
                leverage REAL,
                margin REAL,
                fee REAL NOT NULL DEFAULT 0,
                funding_fee REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                pnl REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                reason TEXT NOT NULL,
                opened_equity REAL,
                closed_equity REAL,
                wallet_balance_after REAL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id),
                FOREIGN KEY (analyst_id) REFERENCES analysts(id)
            );

            CREATE TABLE IF NOT EXISTS virtual_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst_id INTEGER,
                snapshot_type TEXT NOT NULL DEFAULT 'aggregate',
                snapshot_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL,
                interval TEXT NOT NULL,
                wallet_balance REAL NOT NULL,
                equity REAL NOT NULL,
                initial_balance REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                fee_paid REAL NOT NULL,
                funding_fee REAL NOT NULL,
                roi REAL NOT NULL,
                drawdown REAL NOT NULL,
                max_equity REAL NOT NULL,
                open_trade_id INTEGER,
                position_side TEXT,
                position_size REAL NOT NULL DEFAULT 0,
                notional_usdt REAL NOT NULL DEFAULT 0,
                margin REAL NOT NULL DEFAULT 0,
                leverage REAL NOT NULL DEFAULT 1,
                entry_price REAL,
                mark_price REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (analyst_id) REFERENCES analysts(id),
                FOREIGN KEY (open_trade_id) REFERENCES virtual_trades(id)
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                market_summary TEXT NOT NULL,
                opinion_summary TEXT NOT NULL,
                decision TEXT NOT NULL,
                risk TEXT NOT NULL,
                should_execute INTEGER NOT NULL,
                input_snapshot TEXT NOT NULL,
                output_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_node_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_run_id INTEGER,
                graph_name TEXT NOT NULL,
                node_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_snapshot TEXT NOT NULL,
                output_snapshot TEXT NOT NULL,
                error_message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS human_review_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_opinion_id INTEGER,
                graph_run_id INTEGER,
                review_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payload TEXT NOT NULL,
                suggested_question TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS opinion_review_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_item_id INTEGER NOT NULL,
                analyst_name TEXT NOT NULL,
                source_url TEXT,
                published_at TEXT,
                current_price REAL NOT NULL,
                draft_payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (review_item_id) REFERENCES human_review_items(id)
            );

            CREATE TABLE IF NOT EXISTS verification_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                plain_language_summary TEXT NOT NULL,
                failure_reason TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verification_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                base_price REAL NOT NULL,
                latest_price REAL NOT NULL,
                highest_price REAL NOT NULL,
                lowest_price REAL NOT NULL,
                actual_direction TEXT NOT NULL,
                direction_score REAL NOT NULL,
                target_hit INTEGER NOT NULL,
                target_score REAL NOT NULL,
                time_score REAL NOT NULL,
                modified_penalty REAL NOT NULL,
                final_score REAL NOT NULL,
                price_change_pct REAL NOT NULL DEFAULT 0,
                closest_price REAL,
                target_distance_pct REAL,
                quality_label TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL,
                verification_window_start TEXT NOT NULL,
                verification_window_end TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                title TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL,
                description TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
            CREATE INDEX IF NOT EXISTS idx_predictions_analyst ON predictions(analyst_id);
            CREATE INDEX IF NOT EXISTS idx_market_data_time ON market_data(open_time);
            CREATE INDEX IF NOT EXISTS idx_market_data_symbol_interval_time ON market_data(symbol, market_type, interval, open_time);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON virtual_trades(status);
            CREATE INDEX IF NOT EXISTS idx_virtual_account_snapshots_time ON virtual_account_snapshots(snapshot_time);
            CREATE INDEX IF NOT EXISTS idx_agent_node_runs_agent ON agent_node_runs(agent_run_id);
            CREATE INDEX IF NOT EXISTS idx_agent_node_runs_graph ON agent_node_runs(graph_name);
            CREATE INDEX IF NOT EXISTS idx_human_review_items_status ON human_review_items(status);
            CREATE INDEX IF NOT EXISTS idx_opinion_review_drafts_review_item ON opinion_review_drafts(review_item_id);
            CREATE INDEX IF NOT EXISTS idx_verification_reports_prediction ON verification_reports(prediction_id);
            CREATE INDEX IF NOT EXISTS idx_verification_results_prediction ON verification_results(prediction_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task ON scheduled_task_runs(task_name, started_at);
            """
        )
        migrate_market_data_unique_constraint(conn)
        ensure_virtual_trade_columns(conn)
        ensure_virtual_account_snapshot_columns(conn)
        ensure_analyst_metric_columns(conn)
        ensure_verification_result_columns(conn)
        ensure_prediction_version_columns(conn)
        seed_default_settings(conn)
        conn.commit()
        seed_market_data(conn)


def seed_market_data(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT COUNT(*) AS count FROM market_data").fetchone()["count"]
    if exists:
        return

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=96)
    rows: list[tuple[Any, ...]] = []
    price = 68200.0
    for index in range(97):
        timestamp = start + timedelta(hours=index)
        trend = index * 35
        wave = math.sin(index / 4) * 520 + math.cos(index / 9) * 260
        close_price = price + trend + wave
        open_price = close_price - math.sin(index / 3) * 120
        high_price = max(open_price, close_price) + 160 + abs(math.sin(index)) * 130
        low_price = min(open_price, close_price) - 160 - abs(math.cos(index)) * 110
        volume = 860 + abs(math.sin(index / 5)) * 420 + index * 2
        funding_rate = math.sin(index / 12) * 0.00012
        rows.append(
            (
                "BTCUSDT",
                "perpetual",
                "1h",
                timestamp.isoformat(),
                round(open_price, 2),
                round(high_price, 2),
                round(low_price, 2),
                round(close_price, 2),
                round(volume, 2),
                round(funding_rate, 8),
                utc_now(),
            )
        )

    conn.executemany(
        """
        INSERT INTO market_data (
            symbol, market_type, interval, open_time, open, high, low, close, volume, funding_rate, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    rebuild_indicators(conn)


def clear_demo_data(conn: sqlite3.Connection, include_market: bool = False) -> None:
    conn.execute("DELETE FROM agent_node_runs")
    conn.execute("DELETE FROM opinion_review_drafts")
    conn.execute("DELETE FROM human_review_items")
    conn.execute("DELETE FROM verification_results")
    conn.execute("DELETE FROM verification_reports")
    conn.execute("DELETE FROM agent_reports")
    conn.execute("DELETE FROM virtual_account_snapshots")
    conn.execute("DELETE FROM virtual_trades")
    conn.execute("DELETE FROM prediction_versions")
    conn.execute("DELETE FROM agent_runs")
    conn.execute("DELETE FROM predictions")
    conn.execute("DELETE FROM raw_opinions")
    conn.execute("DELETE FROM analysts")
    if include_market:
        conn.execute("DELETE FROM indicators")
        conn.execute("DELETE FROM market_data")
        conn.execute("DELETE FROM scheduled_task_runs")
    conn.commit()


def fetch_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 500,
    end_time_ms: int | None = None,
    market_type: str = "spot",
) -> list[list[Any]]:
    params: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)}
    if end_time_ms is not None:
        params["endTime"] = int(end_time_ms)
    query = urllib.parse.urlencode(params)
    base_url = BINANCE_FUTURES_KLINES_URL if market_type == "perpetual" else BINANCE_KLINES_URL
    request = urllib.request.Request(
        f"{base_url}?{query}",
        headers={"User-Agent": "btc-agent-mvp/0.1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("unexpected Binance kline response")
    return data


def fetch_binance_klines_for_days(symbol: str = "BTCUSDT", interval: str = "1h", days: int = 30, market_type: str = "spot") -> list[list[Any]]:
    if days < 1:
        raise ValueError("days must be greater than 0")
    start_time_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_time_ms: int | None = None
    deduped: dict[int, list[Any]] = {}
    while True:
        batch = fetch_binance_klines(symbol=symbol, interval=interval, limit=1000, end_time_ms=end_time_ms, market_type=market_type)
        if not batch:
            break
        for item in batch:
            open_time_ms = int(item[0])
            if open_time_ms >= start_time_ms:
                deduped[open_time_ms] = item
        oldest_open_time_ms = int(batch[0][0])
        if oldest_open_time_ms <= start_time_ms or len(batch) < 1000:
            break
        end_time_ms = oldest_open_time_ms - 1
    return [deduped[key] for key in sorted(deduped)]


def interval_seconds(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers:
        raise ValueError(f"unsupported interval: {interval}")
    return value * multipliers[unit]


def fetch_gateio_klines_for_days(symbol: str = "BTCUSDT", interval: str = "1h", days: int = 30) -> list[list[Any]]:
    if days < 1:
        raise ValueError("days must be greater than 0")
    pair = symbol.upper().replace("USDT", "_USDT")
    step = interval_seconds(interval)
    end = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    start = max(start, end - step * 9000)
    deduped: dict[int, list[Any]] = {}
    current = start
    while current < end:
        chunk_end = min(end, current + step * 900)
        query = urllib.parse.urlencode({"currency_pair": pair, "interval": interval, "from": current, "to": chunk_end})
        request = urllib.request.Request(
            f"{GATEIO_SPOT_CANDLESTICKS_URL}?{query}",
            headers={"User-Agent": "btc-agent-mvp/0.1"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, list):
            raise RuntimeError("unexpected Gate.io candlestick response")
        for item in data:
            if not isinstance(item, list) or len(item) < 6:
                continue
            open_time_ms = int(item[0]) * 1000
            deduped[open_time_ms] = [
                open_time_ms,
                item[5],
                item[3],
                item[4],
                item[2],
                item[6] if len(item) > 6 else item[1],
            ]
        current = chunk_end
    return [deduped[key] for key in sorted(deduped)]


def fetch_latest_binance_funding_rate(symbol: str = "BTCUSDT") -> float:
    query = urllib.parse.urlencode({"symbol": symbol.upper(), "limit": 1})
    request = urllib.request.Request(
        f"{BINANCE_FUNDING_RATE_URL}?{query}",
        headers={"User-Agent": "btc-agent-mvp/0.1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list) or not data:
        return 0.0
    return float(data[-1].get("fundingRate") or 0)


def sync_real_market_data(
    conn: sqlite3.Connection,
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 500,
    replace: bool = True,
    market_type: str = "spot",
    days: int | None = None,
) -> dict[str, Any]:
    klines = fetch_gateio_klines_for_days(symbol, interval, days or 1)
    if days is None:
        klines = klines[-limit:]
    funding_rate = 0.0
    if replace:
        conn.execute(
            """
            DELETE FROM indicators
            WHERE market_data_id IN (
                SELECT id FROM market_data WHERE symbol = ? AND market_type = ? AND interval = ?
            )
            """,
            (symbol.upper(), market_type, interval),
        )
        conn.execute(
            "DELETE FROM market_data WHERE symbol = ? AND market_type = ? AND interval = ?",
            (symbol.upper(), market_type, interval),
        )
    rows: list[tuple[Any, ...]] = []
    now = utc_now()
    for item in klines:
        open_time = datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc).replace(microsecond=0)
        rows.append(
            (
                symbol.upper(),
                market_type,
                interval,
                open_time.isoformat(),
                float(item[1]),
                float(item[2]),
                float(item[3]),
                round(float(item[4]), 2),
                round(float(item[5]), 2),
                round(funding_rate, 8),
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO market_data (
            symbol, market_type, interval, open_time, open, high, low, close, volume, funding_rate, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, market_type, interval, open_time) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            funding_rate = excluded.funding_rate,
            created_at = excluded.created_at
        """,
        rows,
    )
    conn.commit()
    rebuild_indicators(conn, symbol=symbol, market_type=market_type, interval=interval)
    latest = conn.execute(
        """
        SELECT * FROM market_data
        WHERE symbol = ? AND market_type = ? AND interval = ?
        ORDER BY open_time DESC LIMIT 1
        """,
        (symbol.upper(), market_type, interval),
    ).fetchone()
    return {
        "synced_count": len(rows),
        "symbol": symbol.upper(),
        "market_type": market_type,
        "interval": interval,
        "days": days,
        "latest_price": latest["close"] if latest else None,
        "latest_open_time": latest["open_time"] if latest else None,
    }


def rebuild_indicators(conn: sqlite3.Connection, symbol: str | None = None, market_type: str | None = None, interval: str | None = None) -> None:
    params: list[Any] = []
    where_parts: list[str] = []
    if symbol:
        where_parts.append("symbol = ?")
        params.append(symbol.upper())
    if market_type:
        where_parts.append("market_type = ?")
        params.append(market_type)
    if interval:
        where_parts.append("interval = ?")
        params.append(interval)
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    groups = conn.execute(
        f"""
        SELECT DISTINCT symbol, market_type, interval
        FROM market_data
        {where}
        ORDER BY symbol, market_type, interval
        """,
        params,
    ).fetchall()
    for group in groups:
        rebuild_indicator_group(conn, group["symbol"], group["market_type"], group["interval"])
    conn.commit()


def rebuild_indicator_group(conn: sqlite3.Connection, symbol: str, market_type: str, interval: str) -> None:
    market_rows = conn.execute(
        """
        SELECT * FROM market_data
        WHERE symbol = ? AND market_type = ? AND interval = ?
        ORDER BY open_time ASC
        """,
        (symbol, market_type, interval),
    ).fetchall()
    if not market_rows:
        return
    closes = [float(row["close"]) for row in market_rows]
    highs = [float(row["high"]) for row in market_rows]
    lows = [float(row["low"]) for row in market_rows]
    ema_12: float | None = None
    ema_26: float | None = None
    ema_20: float | None = None

    for index, row in enumerate(market_rows):
        close_price = closes[index]
        ema_12 = close_price if ema_12 is None else close_price * (2 / 13) + ema_12 * (1 - 2 / 13)
        ema_26 = close_price if ema_26 is None else close_price * (2 / 27) + ema_26 * (1 - 2 / 27)
        ema_20 = close_price if ema_20 is None else close_price * (2 / 21) + ema_20 * (1 - 2 / 21)
        ma_20 = sum(closes[max(0, index - 19) : index + 1]) / min(index + 1, 20)
        window = closes[max(0, index - 19) : index + 1]
        mean = sum(window) / len(window)
        variance = sum((value - mean) ** 2 for value in window) / len(window)
        deviation = math.sqrt(variance)
        gains = []
        losses = []
        for cursor in range(max(1, index - 13), index + 1):
            change = closes[cursor] - closes[cursor - 1]
            gains.append(max(change, 0))
            losses.append(abs(min(change, 0)))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        true_ranges = []
        for cursor in range(max(1, index - 13), index + 1):
            previous_close = closes[cursor - 1]
            true_ranges.append(
                max(
                    highs[cursor] - lows[cursor],
                    abs(highs[cursor] - previous_close),
                    abs(lows[cursor] - previous_close),
                )
            )
        atr = sum(true_ranges) / len(true_ranges) if true_ranges else highs[index] - lows[index]
        conn.execute(
            """
            INSERT OR REPLACE INTO indicators (
                market_data_id, ma_20, ema_20, rsi_14, macd, atr_14, bb_upper, bb_lower, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                round(ma_20, 4),
                round(ema_20 or close_price, 4),
                round(rsi, 4),
                round((ema_12 or close_price) - (ema_26 or close_price), 4),
                round(atr, 4),
                round(mean + deviation * 2, 4),
                round(mean - deviation * 2, 4),
                utc_now(),
            ),
        )


def sync_market_intervals(
    conn: sqlite3.Connection,
    intervals: list[str],
    symbol: str = "BTCUSDT",
    limit: int = 500,
    replace: bool = False,
    market_type: str = "spot",
    days: int | None = None,
) -> dict[str, Any]:
    results = []
    for item in intervals:
        clean_interval = str(item).strip()
        if not clean_interval:
            continue
        results.append(
            sync_real_market_data(
                conn,
                symbol=symbol,
                interval=clean_interval,
                limit=limit,
                replace=replace,
                market_type=market_type,
                days=days,
            )
        )
    return {"symbol": symbol.upper(), "market_type": market_type, "intervals": intervals, "days": days, "results": results}


def sync_market_history(
    conn: sqlite3.Connection,
    intervals: list[str],
    symbol: str = "BTCUSDT",
    days: int = 30,
    replace: bool = False,
    market_type: str = "spot",
) -> dict[str, Any]:
    return sync_market_intervals(
        conn,
        intervals,
        symbol=symbol,
        limit=1000,
        replace=replace,
        market_type=market_type,
        days=days,
    )
