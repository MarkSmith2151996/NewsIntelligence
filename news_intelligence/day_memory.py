"""SQLite persistence for NQ day-level news intelligence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .day_models import DayEvent, MacroEvent


class DayIntelligenceMemory:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = Path(__file__).resolve().parents[1] / "data"
            base_dir.mkdir(exist_ok=True)
            db_path = str(base_dir / "day_intelligence.db")

        self.db_path = db_path
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,))
        return cursor.fetchone() is not None

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        return {str(row[1]) for row in cursor.fetchall()}

    def _create_trading_days_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trading_days (
                strategy_id TEXT NOT NULL,
                date TEXT NOT NULL,
                account TEXT NOT NULL,
                contracts INTEGER NOT NULL,
                trade_count INTEGER,
                wins INTEGER,
                losses INTEGER,
                gross_pnl REAL,
                net_pnl REAL,
                avg_win REAL,
                avg_loss REAL,
                win_rate REAL,
                max_single_loss REAL,
                max_single_win REAL,
                max_intraday_drawdown REAL,
                killswitch_triggered INTEGER DEFAULT 0,
                day_result TEXT,
                source_file TEXT,
                source_type TEXT,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (strategy_id, date, account)
            )
        """)

    def _create_day_labels_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS day_labels (
                strategy_id TEXT NOT NULL,
                date TEXT NOT NULL,
                label TEXT NOT NULL,
                label_metric TEXT NOT NULL,
                label_metric_value REAL NOT NULL,
                metric_percentile REAL,
                quantile_low REAL,
                quantile_high REAL,
                label_version TEXT NOT NULL,
                source_account TEXT NOT NULL,
                source_file TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (strategy_id, date)
            )
        """)

    def _migrate_trading_days_table(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "trading_days"):
            self._create_trading_days_table(conn)
            return

        columns = self._table_columns(conn, "trading_days")
        if "strategy_id" in columns:
            if "max_intraday_drawdown" not in columns:
                conn.execute("ALTER TABLE trading_days ADD COLUMN max_intraday_drawdown REAL")
            return

        conn.execute("ALTER TABLE trading_days RENAME TO trading_days_legacy")
        self._create_trading_days_table(conn)
        conn.execute("""
            INSERT INTO trading_days (
                strategy_id, date, account, contracts, trade_count, wins, losses,
                gross_pnl, net_pnl, avg_win, avg_loss, win_rate, max_single_loss,
                max_single_win, killswitch_triggered, day_result, source_file,
                source_type, imported_at
            )
            SELECT
                'legacy_default', date, account, contracts, trade_count, wins, losses,
                gross_pnl, net_pnl, avg_win, avg_loss, win_rate, max_single_loss,
                max_single_win, killswitch_triggered, day_result, source_file,
                source_type, imported_at
            FROM trading_days_legacy
        """)
        conn.execute("DROP TABLE trading_days_legacy")

    def _migrate_day_labels_table(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "day_labels"):
            self._create_day_labels_table(conn)
            return

        columns = self._table_columns(conn, "day_labels")
        if "strategy_id" in columns:
            return

        conn.execute("ALTER TABLE day_labels RENAME TO day_labels_legacy")
        self._create_day_labels_table(conn)
        conn.execute("""
            INSERT INTO day_labels (
                strategy_id, date, label, label_metric, label_metric_value,
                metric_percentile, quantile_low, quantile_high, label_version,
                source_account, source_file, created_at
            )
            SELECT
                'legacy_default', date, label, label_metric, label_metric_value,
                metric_percentile, quantile_low, quantile_high, label_version,
                source_account, source_file, created_at
            FROM day_labels_legacy
        """)
        conn.execute("DROP TABLE day_labels_legacy")

    def _ensure_day_events_bucket(self, conn: sqlite3.Connection) -> None:
        columns = self._table_columns(conn, "day_events")
        if "bucket" not in columns:
            conn.execute("ALTER TABLE day_events ADD COLUMN bucket TEXT")
            conn.execute("UPDATE day_events SET bucket = session_bucket WHERE bucket IS NULL")

    def _init_database(self) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            self._migrate_trading_days_table(conn)
            self._migrate_day_labels_table(conn)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pilot_samples (
                    date TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    sample_group TEXT NOT NULL,
                    sample_seed INTEGER NOT NULL,
                    sample_version TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE,
                    date TEXT NOT NULL,
                    timestamp TEXT,
                    source_family TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT,
                    summary TEXT,
                    raw_text TEXT,
                    url TEXT,
                    author TEXT,
                    event_type TEXT,
                    sentiment REAL,
                    importance REAL,
                    bucket TEXT,
                    session_bucket TEXT,
                    known_before_open INTEGER DEFAULT 0,
                    entities_json TEXT,
                    tags_json TEXT,
                    raw_payload_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_features (
                    date TEXT PRIMARY KEY,
                    feature_version TEXT NOT NULL,
                    headline_count INTEGER DEFAULT 0,
                    high_impact_count INTEGER DEFAULT 0,
                    is_macro_day INTEGER DEFAULT 0,
                    is_fomc INTEGER DEFAULT 0,
                    is_cpi INTEGER DEFAULT 0,
                    is_nfp INTEGER DEFAULT 0,
                    is_earnings_heavy INTEGER DEFAULT 0,
                    is_geopolitical INTEGER DEFAULT 0,
                    is_regulatory INTEGER DEFAULT 0,
                    is_court_decision INTEGER DEFAULT 0,
                    is_policy_day INTEGER DEFAULT 0,
                    theme_vector_json TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._ensure_day_events_bucket(conn)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS macro_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE,
                    date TEXT NOT NULL,
                    timestamp TEXT,
                    provider TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    scheduled_time TEXT,
                    actual_value TEXT,
                    prior_value TEXT,
                    revised_value TEXT,
                    importance REAL,
                    known_before_open INTEGER DEFAULT 0,
                    raw_payload_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    analysis_type TEXT NOT NULL,
                    model TEXT,
                    summary TEXT,
                    reasoning TEXT,
                    confidence REAL,
                    analysis_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_similarity (
                    date TEXT NOT NULL,
                    similar_date TEXT NOT NULL,
                    score REAL NOT NULL,
                    basis TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (date, similar_date, basis)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_days_account ON trading_days(account)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trading_days_strategy_date ON trading_days(strategy_id, date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_day_labels_label ON day_labels(label)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_day_labels_strategy_date ON day_labels(strategy_id, date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_day_events_date ON day_events(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_day_events_bucket ON day_events(bucket)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_macro_events_date ON macro_events(date)")

    def replace_trading_days(self, rows: Iterable[Mapping[str, object]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO trading_days (
                    strategy_id, date, account, contracts, trade_count, wins, losses,
                    gross_pnl, net_pnl, avg_win, avg_loss, win_rate,
                    max_single_loss, max_single_win, max_intraday_drawdown,
                    killswitch_triggered, day_result, source_file, source_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    row.get("strategy_id", "legacy_default"), row["date"], row["account"], row["contracts"], row.get("trade_count"), row.get("wins"), row.get("losses"),
                    row.get("gross_pnl"), row.get("net_pnl"), row.get("avg_win"), row.get("avg_loss"), row.get("win_rate"),
                    row.get("max_single_loss"), row.get("max_single_win"), row.get("max_intraday_drawdown"), row.get("killswitch_triggered", 0), row.get("day_result"),
                    row.get("source_file"), row.get("source_type")
                ) for row in rows],
            )

    def replace_day_labels(self, rows: Iterable[Mapping[str, object]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO day_labels (
                    strategy_id, date, label, label_metric, label_metric_value,
                    metric_percentile, quantile_low, quantile_high,
                    label_version, source_account, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    row.get("strategy_id", "legacy_default"), row["date"], row["label"], row["label_metric"], row["label_metric_value"], row.get("metric_percentile"),
                    row.get("quantile_low"), row.get("quantile_high"), row["label_version"], row["source_account"], row.get("source_file")
                ) for row in rows],
            )

    def replace_pilot_samples(self, rows: Iterable[Mapping[str, object]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO pilot_samples (
                    date, label, sample_group, sample_seed, sample_version, notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(
                    row["date"], row["label"], row["sample_group"], row["sample_seed"], row["sample_version"], row.get("notes")
                ) for row in rows],
            )

    def replace_day_events(self, events: Iterable[DayEvent]) -> int:
        rows = [event.to_db_dict() for event in events]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO day_events (
                    event_id, date, timestamp, source_family, provider,
                    title, summary, raw_text, url, author, event_type,
                    sentiment, importance, bucket, session_bucket, known_before_open,
                    entities_json, tags_json, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    row["event_id"], row["date"], row["timestamp"], row["source_family"], row["provider"], row["title"], row["summary"],
                    row["raw_text"], row["url"], row["author"], row["event_type"], row["sentiment"], row["importance"], row["bucket"], row["session_bucket"],
                    row["known_before_open"], row["entities_json"], row["tags_json"], row["raw_payload_json"]
                ) for row in rows],
            )
        return len(rows)

    def replace_macro_events(self, events: Iterable[MacroEvent]) -> int:
        rows = [event.to_db_dict() for event in events]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO macro_events (
                    event_id, date, timestamp, provider, event_name, category,
                    scheduled_time, actual_value, prior_value, revised_value,
                    importance, known_before_open, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(
                    row["event_id"], row["date"], row["timestamp"], row["provider"], row["event_name"], row["category"],
                    row["scheduled_time"], row["actual_value"], row["prior_value"], row["revised_value"], row["importance"],
                    row["known_before_open"], row["raw_payload_json"]
                ) for row in rows],
            )
        return len(rows)

    def get_pilot_sample_dates(self, strategy_id: Optional[str] = None) -> list[str]:
        with self._connect() as conn:
            cursor = conn.cursor()
            if strategy_id:
                cursor.execute(
                    """
                    SELECT p.date
                    FROM pilot_samples p
                    JOIN day_labels dl ON dl.date = p.date
                    WHERE dl.strategy_id = ?
                    ORDER BY p.date
                    """,
                    (strategy_id,),
                )
            else:
                cursor.execute("SELECT date FROM pilot_samples ORDER BY date")
            return [row[0] for row in cursor.fetchall()]

    def get_strategy_dates(self, strategy_id: str) -> list[str]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT date FROM day_labels WHERE strategy_id = ? ORDER BY date",
                (strategy_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def clear_strategy_seed_data(self, strategy_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM day_labels WHERE strategy_id = ?", (strategy_id,))
            cursor.execute("DELETE FROM trading_days WHERE strategy_id = ?", (strategy_id,))

    def clear_seed_data(self) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            for table in ("day_similarity", "day_analysis", "macro_events", "day_features", "day_events", "pilot_samples", "day_labels", "trading_days"):
                cursor.execute(f"DELETE FROM {table}")
