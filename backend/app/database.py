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

# 后端基础路径与默认 SQLite 数据文件位置。
BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bit_agent.sqlite3"

# 外部行情数据源地址，分别用于现货、合约 K 线和资金费率。
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
GATEIO_SPOT_CANDLESTICKS_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    # 本地开发时自动加载项目根目录的 .env。
    load_dotenv(PROJECT_ROOT / ".env")

try:
    # psycopg 为可选依赖；配置 DATABASE_URL 后才会使用 PostgreSQL。
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

# 系统默认配置，初始化数据库和重置设置时会写入 settings 表。
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
    "market.symbol": ("BTCUSDT", "str", "默认行情交易对"),
    "market.intervals": (["1m", "5m", "15m", "1h", "4h", "1d"], "json", "默认同步行情周期列表"),
    "market.default_interval": ("1h", "str", "默认展示行情周期"),
    # Gate MCP 数据源配置
    "gate_mcp.base_url": ("https://api.gatemcp.ai", "str", "Gate MCP 服务基础地址"),
    "gate_mcp.enabled": (False, "bool", "是否启用 Gate MCP 数据源"),
    "gate_mcp.timeout_seconds": (30, "int", "Gate MCP 请求超时秒数"),
    "scheduler.gate_btc_sync_minutes": (5, "int", "Gate BTC 合约数据同步间隔分钟数"),
    "scheduler.gate_news_sync_minutes": (30, "int", "Gate 资讯同步间隔分钟数"),
    "scheduler.gate_square_sync_minutes": (60, "int", "Gate Square 热门同步间隔分钟数"),
    "scheduler.sentiment_build_minutes": (15, "int", "市场情绪快照构建间隔分钟数"),
    "scheduler.memory_compact_hours": (6, "int", "市场记忆压缩间隔小时数"),
    "scheduler.gate_info_sync_minutes": (30, "int", "Gate Info 技术面同步间隔分钟数"),
    "scheduler.gate_square_user_sync_minutes": (15, "int", "Gate Square 指定用户观点同步间隔分钟数"),
    "scheduler.nasdaq_sync_minutes": (30, "int", "纳斯达克指数同步间隔分钟数"),
    "nasdaq.symbols": (["IXIC", "NDX", "QQQ", "NQ"], "json", "纳斯达克相关指数/ETF/期货符号列表"),
    "square.followed_users": ([], "json", "Gate 广场关注用户列表 [{source_user_id, display_name}]"),
    "news.keywords": (["BTC", "Bitcoin", "Nasdaq", "FOMC", "CPI", "Fed"], "json", "新闻采集关键词列表"),
}

# PostgreSQL 包装层需要对这些表的 INSERT 自动追加 RETURNING id。
ID_TABLES = {
    "analysts",
    "raw_opinions",
    "predictions",
    "prediction_versions",
    "market_data",
    "indicators",
    "agent_runs",
    "agent_node_runs",
    "human_review_items",
    "opinion_review_drafts",
    "verification_reports",
    "verification_results",
    "agent_reports",
    "scheduled_task_runs",
    "gate_mcp_raw_records",
    "btc_contract_metrics",
    "nasdaq_market_data",
    "gate_square_posts",
    "market_sentiment_snapshots",
    "market_memories",
    "analyst_source_accounts",
}


def utc_now() -> str:
    # 统一使用 UTC ISO 时间，避免前后端和调度器时区不一致。
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime:
    # 将字符串时间解析为 UTC datetime，空值默认当前时间。
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PostgresCursor:
    # 让 PostgreSQL cursor 暴露与 sqlite cursor 类似的 fetch 和 lastrowid 接口。
    def __init__(self, cursor: Any, lastrowid: int | None = None) -> None:
        self.cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self) -> dict[str, Any] | None:
        return self.cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.cursor.fetchall())


class PostgresConnection:
    # 轻量包装 PostgreSQL 连接，使上层业务代码可复用 SQLite 风格 SQL。
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
    # 同时支持 DATABASE_URL 和 POSTGRES_DSN 两种环境变量。
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN")


def database_backend() -> str:
    # 根据是否配置 PostgreSQL DSN 判断当前数据库后端。
    return "postgres" if database_url() else "sqlite"


def convert_placeholders(query: str) -> str:
    # 将 SQLite 的 ? 占位符转换成 psycopg 使用的 %s。
    return re.sub(r"\?", "%s", query)


def insert_target_table(query: str) -> str | None:
    # 从 INSERT 语句中提取目标表名，用于判断是否需要返回自增 id。
    match = re.search(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", query, re.IGNORECASE)
    return match.group(1).lower() if match else None


def append_returning_id(query: str) -> str:
    # PostgreSQL 需要 RETURNING id 才能模拟 sqlite 的 lastrowid。
    stripped = query.strip()
    upper = stripped.upper()
    if not upper.startswith("INSERT") or "RETURNING" in upper:
        return query
    table = insert_target_table(query)
    if table not in ID_TABLES:
        return query
    return f"{query.rstrip().rstrip(';')} RETURNING id"


def convert_sql_for_postgres(query: str, returning_id: bool = False) -> str:
    # 将项目中常见的 SQLite 写法转换为 PostgreSQL 可执行 SQL。
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
    # 初始化表结构时转换少量 SQLite 专有类型。
    converted = script
    converted = converted.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    converted = converted.replace("REAL", "DOUBLE PRECISION")
    return converted


def connect() -> Any:
    # 优先连接 PostgreSQL；未配置时使用本地 SQLite 数据库。
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
    # 将数据库行对象转换为普通字典，方便 JSON 序列化。
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    # 批量转换查询结果。
    return [row_to_dict(row) or {} for row in rows]


def setting_value_to_text(value: Any, value_type: str) -> str:
    # settings 表统一存储字符串，读取时再按 value_type 还原类型。
    if value_type == "json":
        return json.dumps(value, ensure_ascii=False)
    if value_type == "bool":
        return "true" if bool(value) else "false"
    return str(value)


def seed_default_settings(conn: sqlite3.Connection) -> None:
    # 首次初始化或新增配置项时写入默认设置。
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
    # 兼容旧版 SQLite 表结构，将 open_time 唯一约束升级为复合唯一约束。
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
    # 跨 SQLite/PostgreSQL 获取表字段集合，供增量迁移使用。
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


def ensure_analyst_metric_columns(conn: sqlite3.Connection) -> None:
    # 为分析师评分体系补齐扩展指标字段。
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
    # 为预测验证结果补齐价格偏离和质量标签字段。
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
    # 为观点改口记录补齐新旧方向、周期和严重度字段。
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


def migrate_agent_runs_analysis_schema(conn: sqlite3.Connection) -> None:
    legacy_execute_column = "should" + "_execute"
    if legacy_execute_column not in table_columns(conn, "agent_runs"):
        return
    if database_backend() == "postgres":
        conn.execute(f"ALTER TABLE agent_runs DROP COLUMN IF EXISTS {legacy_execute_column}")
        conn.commit()
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE agent_runs RENAME TO agent_runs_old")
    conn.execute(
        """
        CREATE TABLE agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL,
            market_summary TEXT NOT NULL,
            opinion_summary TEXT NOT NULL,
            decision TEXT NOT NULL,
            risk TEXT NOT NULL,
            input_snapshot TEXT NOT NULL,
            output_snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_runs (
            id, trigger, market_summary, opinion_summary, decision, risk,
            input_snapshot, output_snapshot, created_at
        )
        SELECT id, trigger, market_summary, opinion_summary, decision, risk,
               input_snapshot, output_snapshot, created_at
        FROM agent_runs_old
        """
    )
    conn.execute("DROP TABLE agent_runs_old")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def ensure_market_memories_phase_a_columns(conn: sqlite3.Connection) -> None:
    # A4: market_memories 追加 sentiment / expectation / decay_policy / evidence_refs / source_types
    existing = table_columns(conn, "market_memories")
    columns: dict[str, str] = {
        "sentiment": "TEXT",
        "expectation": "TEXT",
        "decay_policy": "TEXT NOT NULL DEFAULT 'default'",
        "evidence_refs": "TEXT NOT NULL DEFAULT '[]'",
        "source_types": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE market_memories ADD COLUMN {name} {definition}")
    conn.commit()


def ensure_sentiment_snapshot_phase_a_columns(conn: sqlite3.Connection) -> None:
    # A5: market_sentiment_snapshots 追加 target / source_window / confidence / dominant_topics / crowd_positioning / evidence_refs
    existing = table_columns(conn, "market_sentiment_snapshots")
    columns: dict[str, str] = {
        "target": "TEXT NOT NULL DEFAULT 'BTC'",
        "source_window": "TEXT",
        "confidence": "TEXT",
        "dominant_topics": "TEXT NOT NULL DEFAULT '[]'",
        "crowd_positioning": "TEXT",
        "evidence_refs": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE market_sentiment_snapshots ADD COLUMN {name} {definition}")
    conn.commit()


def ensure_gate_square_posts_phase_a_columns(conn: sqlite3.Connection) -> None:
    # A6: gate_square_posts 追加 author_id / hot_score / repost_count / is_followed_user / is_hot_post
    existing = table_columns(conn, "gate_square_posts")
    columns: dict[str, str] = {
        "author_id": "TEXT",
        "hot_score": "REAL DEFAULT 0",
        "repost_count": "INTEGER DEFAULT 0",
        "is_followed_user": "INTEGER NOT NULL DEFAULT 0",
        "is_hot_post": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE gate_square_posts ADD COLUMN {name} {definition}")
    conn.commit()


def ensure_nasdaq_market_data_phase_b_columns(conn: sqlite3.Connection) -> None:
    existing = table_columns(conn, "nasdaq_market_data")
    columns: dict[str, str] = {
        "open": "REAL",
        "high": "REAL",
        "low": "REAL",
        "close": "REAL",
        "volume": "REAL",
        "market_session": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE nasdaq_market_data ADD COLUMN {name} {definition}")
    conn.commit()


def init_db() -> None:
    # 创建所有业务表、索引，并执行必要的兼容迁移和种子数据写入。
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

            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                market_summary TEXT NOT NULL,
                opinion_summary TEXT NOT NULL,
                decision TEXT NOT NULL,
                risk TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_agent_node_runs_agent ON agent_node_runs(agent_run_id);
            CREATE INDEX IF NOT EXISTS idx_agent_node_runs_graph ON agent_node_runs(graph_name);
            CREATE INDEX IF NOT EXISTS idx_human_review_items_status ON human_review_items(status);
            CREATE INDEX IF NOT EXISTS idx_opinion_review_drafts_review_item ON opinion_review_drafts(review_item_id);
            CREATE INDEX IF NOT EXISTS idx_verification_reports_prediction ON verification_reports(prediction_id);
            CREATE INDEX IF NOT EXISTS idx_verification_results_prediction ON verification_results(prediction_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task ON scheduled_task_runs(task_name, started_at);

            CREATE TABLE IF NOT EXISTS gate_mcp_raw_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                request_payload TEXT NOT NULL DEFAULT '{}',
                response_payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                error_message TEXT,
                latency_ms INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS btc_contract_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                last_price REAL NOT NULL,
                mark_price REAL,
                index_price REAL,
                funding_rate REAL,
                funding_rate_indicative REAL,
                volume_24h REAL,
                open_interest REAL,
                high_24h REAL,
                low_24h REAL,
                change_pct_24h REAL,
                source TEXT NOT NULL DEFAULT 'gate_mcp',
                fetched_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, fetched_at)
            );

            CREATE TABLE IF NOT EXISTS nasdaq_market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                change_pct REAL,
                source TEXT NOT NULL,
                market_session TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                fetched_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, fetched_at)
            );

            CREATE TABLE IF NOT EXISTS gate_square_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL UNIQUE,
                author TEXT,
                content TEXT NOT NULL,
                publish_time TEXT,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                sentiment TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT 'gate_square',
                fetched_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_sentiment_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
                bull_ratio REAL,
                bear_ratio REAL,
                fear_greed_index REAL,
                funding_rate REAL,
                open_interest_change_pct REAL,
                news_sentiment_score REAL,
                square_sentiment_score REAL,
                overall_sentiment TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                snapshot_time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(symbol, snapshot_time)
            );

            CREATE TABLE IF NOT EXISTS market_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                valid_from TEXT,
                valid_until TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'system',
                payload TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_gate_mcp_raw_endpoint ON gate_mcp_raw_records(endpoint, tool_name, created_at);
            CREATE INDEX IF NOT EXISTS idx_btc_contract_metrics_symbol ON btc_contract_metrics(symbol, fetched_at);
            CREATE INDEX IF NOT EXISTS idx_nasdaq_market_data_symbol ON nasdaq_market_data(symbol, fetched_at);
            CREATE INDEX IF NOT EXISTS idx_gate_square_posts_time ON gate_square_posts(publish_time);
            CREATE INDEX IF NOT EXISTS idx_sentiment_snapshots_symbol ON market_sentiment_snapshots(symbol, snapshot_time);
            CREATE INDEX IF NOT EXISTS idx_market_memories_active ON market_memories(is_active, memory_type, symbol);

            CREATE TABLE IF NOT EXISTS analyst_source_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyst_id INTEGER,
                source_platform TEXT NOT NULL DEFAULT 'gate_square',
                source_user_id TEXT NOT NULL,
                display_name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(source_platform, source_user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_analyst_source_platform ON analyst_source_accounts(source_platform, enabled);
            """
        )
        migrate_market_data_unique_constraint(conn)
        ensure_analyst_metric_columns(conn)
        ensure_verification_result_columns(conn)
        ensure_prediction_version_columns(conn)
        migrate_agent_runs_analysis_schema(conn)
        ensure_market_memories_phase_a_columns(conn)
        ensure_sentiment_snapshot_phase_a_columns(conn)
        ensure_gate_square_posts_phase_a_columns(conn)
        ensure_nasdaq_market_data_phase_b_columns(conn)
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
