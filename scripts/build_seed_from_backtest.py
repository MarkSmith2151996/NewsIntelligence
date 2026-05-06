#!/usr/bin/env python3
"""Build labeled day seeds from a generic backtest trade CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from news_intelligence.day_memory import DayIntelligenceMemory


EASTERN_TZ = ZoneInfo("America/New_York")
REQUIRED_COLUMNS = {"entry_time", "net_pnl"}
OPTIONAL_COLUMNS = {
    "exit_time",
    "exit_reason",
    "entry_price",
    "exit_price",
    "planned_stop_price",
    "planned_target_price",
    "gross_pnl",
}


def _parse_et(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.isna().any():
        bad_count = int(parsed.isna().sum())
        raise ValueError(f"Failed to parse {bad_count} timestamps from entry_time")

    if parsed.dt.tz is None:
        return parsed.dt.tz_localize(EASTERN_TZ)

    return parsed.dt.tz_convert(EASTERN_TZ)


def load_backtest_rows(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    missing = sorted(REQUIRED_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"Backtest CSV is missing required columns: {', '.join(missing)}")

    present_optional = sorted(OPTIONAL_COLUMNS.intersection(df.columns))
    keep_columns = sorted(REQUIRED_COLUMNS.union(set(present_optional)))
    df = df[keep_columns].copy()
    df["entry_time"] = _parse_et(df["entry_time"])
    df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce")
    if df["net_pnl"].isna().any():
        bad_count = int(df["net_pnl"].isna().sum())
        raise ValueError(f"Backtest CSV has {bad_count} rows with invalid net_pnl values")

    if "gross_pnl" in df.columns:
        df["gross_pnl"] = pd.to_numeric(df["gross_pnl"], errors="coerce")
    else:
        df["gross_pnl"] = df["net_pnl"]

    df = df.sort_values("entry_time").reset_index(drop=True)
    df["date"] = df["entry_time"].dt.strftime("%Y-%m-%d")
    return df


def build_trading_day_rows(df: pd.DataFrame, strategy_id: str, source_file: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for date, group in df.groupby("date", sort=True):
        net_cumulative = group["net_pnl"].cumsum()
        running_peak = net_cumulative.cummax().where(net_cumulative.cummax() > 0, 0.0)
        max_intraday_drawdown = float((running_peak - net_cumulative).max())
        wins = int((group["net_pnl"] > 0).sum())
        losses = int((group["net_pnl"] <= 0).sum())
        total_net = float(group["net_pnl"].sum())
        rows.append({
            "strategy_id": strategy_id,
            "date": date,
            "account": "BACKTEST",
            "contracts": 1,
            "trade_count": int(len(group)),
            "wins": wins,
            "losses": losses,
            "gross_pnl": float(group["gross_pnl"].sum()),
            "net_pnl": total_net,
            "avg_win": float(group.loc[group["net_pnl"] > 0, "net_pnl"].mean()) if wins else 0.0,
            "avg_loss": float(group.loc[group["net_pnl"] <= 0, "net_pnl"].mean()) if losses else 0.0,
            "win_rate": float((wins / len(group)) * 100.0) if len(group) else 0.0,
            "max_single_loss": float(group["net_pnl"].min()),
            "max_single_win": float(group["net_pnl"].max()),
            "max_intraday_drawdown": max_intraday_drawdown,
            "killswitch_triggered": 0,
            "day_result": "WIN" if total_net > 0 else "LOSS" if total_net < 0 else "FLAT",
            "source_file": source_file,
            "source_type": "generic_backtest_csv",
        })
    return rows


def build_label_rows(
    trading_day_rows: list[dict[str, object]],
    strategy_id: str,
    good_threshold: float,
    bad_threshold: float,
) -> list[dict[str, object]]:
    label_df = pd.DataFrame(trading_day_rows)[["date", "net_pnl", "source_file"]].copy()
    label_df = label_df.sort_values(["net_pnl", "date"]).reset_index(drop=True)
    label_df["metric_percentile"] = (label_df.index + 1) / len(label_df)
    quantile_low = float(label_df["net_pnl"].quantile(bad_threshold))
    quantile_high = float(label_df["net_pnl"].quantile(good_threshold))

    def assign_label(percentile: float) -> str:
        if percentile <= bad_threshold:
            return "BAD"
        if percentile >= good_threshold:
            return "GOOD"
        return "NEUTRAL"

    label_df["label"] = label_df["metric_percentile"].apply(assign_label)
    return [{
        "strategy_id": strategy_id,
        "date": row.date,
        "label": row.label,
        "label_metric": "daily_net_pnl",
        "label_metric_value": float(row.net_pnl),
        "metric_percentile": float(row.metric_percentile),
        "quantile_low": quantile_low,
        "quantile_high": quantile_high,
        "label_version": f"percentile_{bad_threshold:.2f}_{good_threshold:.2f}",
        "source_account": "BACKTEST",
        "source_file": row.source_file,
    } for row in label_df.itertuples(index=False)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a labeled day dataset from a generic backtest CSV")
    parser.add_argument("--db-path", help="Optional SQLite database path override")
    parser.add_argument("--backtest-csv", required=True, help="Path to a backtest CSV with entry_time and net_pnl")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier for the labeled dataset")
    parser.add_argument("--label-method", default="percentile", choices=["percentile"], help="Day labeling method")
    parser.add_argument("--good-threshold", type=float, default=0.67, help="Percentile at or above which a day is GOOD")
    parser.add_argument("--bad-threshold", type=float, default=0.33, help="Percentile at or below which a day is BAD")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 <= args.bad_threshold < args.good_threshold <= 1.0:
        raise ValueError("Thresholds must satisfy 0.0 <= bad_threshold < good_threshold <= 1.0")

    backtest_csv = Path(args.backtest_csv)
    df = load_backtest_rows(backtest_csv)
    trading_day_rows = build_trading_day_rows(df, strategy_id=args.strategy_id, source_file=backtest_csv.name)
    label_rows = build_label_rows(
        trading_day_rows,
        strategy_id=args.strategy_id,
        good_threshold=args.good_threshold,
        bad_threshold=args.bad_threshold,
    )

    memory = DayIntelligenceMemory(db_path=args.db_path)
    memory.clear_strategy_seed_data(args.strategy_id)
    memory.replace_trading_days(trading_day_rows)
    memory.replace_day_labels(label_rows)

    label_counts = pd.Series([row["label"] for row in label_rows]).value_counts().to_dict()
    print(f"Database: {memory.db_path}")
    print(f"Strategy: {args.strategy_id}")
    print(f"Imported trading days: {len(trading_day_rows)}")
    print(f"Imported trades: {len(df)}")
    print(f"Label counts: {label_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
