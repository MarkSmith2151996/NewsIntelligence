#!/usr/bin/env python3
"""Review seeded pilot day samples from the day intelligence database."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "day_intelligence.db"


def fetch_rows(conn: sqlite3.Connection, query: str, params: tuple = ()):
    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()


def print_overall_summary(conn: sqlite3.Connection, sample_group: str) -> None:
    rows = fetch_rows(conn, """
        SELECT p.label, COUNT(*) AS day_count, COALESCE(SUM(e.event_count), 0) AS event_count
        FROM pilot_samples p
        LEFT JOIN (SELECT date, COUNT(*) AS event_count FROM day_events GROUP BY date) e ON e.date = p.date
        WHERE p.sample_group = ?
        GROUP BY p.label
        ORDER BY p.label
    """, (sample_group,))
    print("Pilot Summary")
    for label, day_count, event_count in rows:
        print(f"  {label:<7} days={day_count:<2} events={event_count}")


def print_event_type_summary(conn: sqlite3.Connection, sample_group: str) -> None:
    rows = fetch_rows(conn, """
        SELECT p.label, e.event_type, COUNT(*) AS event_count
        FROM pilot_samples p
        JOIN day_events e ON e.date = p.date
        WHERE p.sample_group = ?
        GROUP BY p.label, e.event_type
        ORDER BY p.label, event_count DESC, e.event_type
    """, (sample_group,))
    print("\nEvent Types By Label")
    current_label = None
    for label, event_type, event_count in rows:
        if label != current_label:
            current_label = label
            print(f"  {label}")
        print(f"    {event_type:<14} {event_count}")


def print_provider_summary(conn: sqlite3.Connection, sample_group: str) -> None:
    rows = fetch_rows(conn, """
        SELECT p.label, e.provider, COUNT(*) AS event_count
        FROM pilot_samples p
        JOIN day_events e ON e.date = p.date
        WHERE p.sample_group = ?
        GROUP BY p.label, e.provider
        ORDER BY p.label, event_count DESC, e.provider
    """, (sample_group,))
    print("\nProviders By Label")
    current_label = None
    for label, provider, event_count in rows:
        if label != current_label:
            current_label = label
            print(f"  {label}")
        print(f"    {provider:<20} {event_count}")


def print_macro_summary(conn: sqlite3.Connection, sample_group: str) -> None:
    rows = fetch_rows(conn, """
        SELECT p.label, COUNT(m.id) AS macro_count
        FROM pilot_samples p
        LEFT JOIN macro_events m ON m.date = p.date
        WHERE p.sample_group = ?
        GROUP BY p.label
        ORDER BY p.label
    """, (sample_group,))
    print("\nMacro Events By Label")
    for label, macro_count in rows:
        print(f"  {label:<7} macro_events={macro_count}")


def print_day_details(conn: sqlite3.Connection, sample_group: str, strategy_id: str, headlines_per_day: int) -> None:
    day_rows = fetch_rows(conn, """
        SELECT p.date, p.label, dl.label_metric_value,
               td25.gross_pnl, td25.net_pnl, td25.trade_count,
               td150.net_pnl,
               COALESCE(ec.event_count, 0) AS event_count
        FROM pilot_samples p
        LEFT JOIN day_labels dl ON dl.date = p.date AND dl.strategy_id = ?
        LEFT JOIN trading_days td25 ON td25.date = p.date AND td25.account = '25K' AND td25.strategy_id = ?
        LEFT JOIN trading_days td150 ON td150.date = p.date AND td150.account = '150K' AND td150.strategy_id = ?
        LEFT JOIN (SELECT date, COUNT(*) AS event_count FROM day_events GROUP BY date) ec ON ec.date = p.date
        WHERE p.sample_group = ?
        ORDER BY p.date
    """, (strategy_id, strategy_id, strategy_id, sample_group))
    print("\nDay Details")
    for date, label, metric_value, gross_25k, net_25k, trade_count, net_150k, event_count in day_rows:
        metric_text = f"{metric_value:.2f}" if metric_value is not None else "n/a"
        gross_25k_text = f"{gross_25k:.2f}" if gross_25k is not None else "n/a"
        net_25k_text = f"{net_25k:.2f}" if net_25k is not None else "n/a"
        net_150k_text = f"{net_150k:.2f}" if net_150k is not None else "n/a"
        print(f"\n{date}  {label}  metric={metric_text}  25K_gross={gross_25k_text}  25K_net={net_25k_text}  150K_net={net_150k_text}  trades={trade_count}  events={event_count}")
        headlines = fetch_rows(conn, """
            SELECT provider, event_type, importance, COALESCE(bucket, session_bucket), title
            FROM day_events
            WHERE date = ?
            ORDER BY importance DESC, timestamp ASC, id ASC
            LIMIT ?
        """, (date, headlines_per_day))
        for provider, event_type, importance, session_bucket, title in headlines:
            print(f"  - [{provider}/{event_type}/{session_bucket}/{importance:.2f}] {title}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review pilot day sample from day intelligence DB")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--sample-group", default="pilot_10")
    parser.add_argument("--strategy-id", default="legacy_default")
    parser.add_argument("--headlines-per-day", type=int, default=5)
    args = parser.parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database file does not exist: {db_path}")
    conn = sqlite3.connect(db_path)
    print(f"Database: {db_path}")
    print(f"Sample group: {args.sample_group}")
    print(f"Strategy: {args.strategy_id}")
    print_overall_summary(conn, args.sample_group)
    print_event_type_summary(conn, args.sample_group)
    print_provider_summary(conn, args.sample_group)
    print_macro_summary(conn, args.sample_group)
    print_day_details(conn, args.sample_group, args.strategy_id, args.headlines_per_day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
