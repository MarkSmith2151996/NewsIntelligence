#!/usr/bin/env python3
"""Build the seeded NQ day-intelligence database and pilot sample."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from news_intelligence.day_memory import DayIntelligenceMemory


LABEL_VERSION = "gross_per_contract_v1"
SAMPLE_VERSION = "pilot10_v1"
DEFAULT_SAMPLE_COUNTS = {"BAD": 4, "GOOD": 4, "NEUTRAL": 2}
DEFAULT_STRATEGY_ID = "legacy_default"


def load_25k_daily(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df[df["account"] == "25K"].copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df["source_file"] = csv_path.name
    df["source_type"] = "daily_performance"
    return df


def load_big_trade_detail(csv_path: Path, account: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    summaries = df[df["trade_num"] == "DAILY_SUMMARY"].copy()
    trades = df[df["trade_num"] != "DAILY_SUMMARY"].copy()
    summaries["date"] = pd.to_datetime(summaries["date"])
    trades["date"] = pd.to_datetime(trades["date"])
    trades["pnl"] = pd.to_numeric(trades["pnl"], errors="coerce")
    summaries["pnl"] = pd.to_numeric(summaries["pnl"], errors="coerce")
    contracts_value = int(pd.to_numeric(trades["contracts"], errors="coerce").dropna().mode().iloc[0])
    trade_stats = (
        trades.groupby("date", sort=True)
        .agg(
            wins=("pnl", lambda s: int((s > 0).sum())),
            losses=("pnl", lambda s: int((s <= 0).sum())),
            avg_win=("pnl", lambda s: float(s[s > 0].mean()) if (s > 0).any() else 0.0),
            avg_loss=("pnl", lambda s: float(s[s <= 0].mean()) if (s <= 0).any() else 0.0),
            max_single_loss=("pnl", "min"),
            max_single_win=("pnl", "max"),
        )
        .reset_index()
    )
    summaries["trade_count"] = summaries["contracts"].astype(str).str.extract(r"trades=(\d+)").astype(int)
    summaries = summaries.rename(columns={"pnl": "net_pnl"})
    summaries["gross_pnl"] = summaries["net_pnl"]
    summaries["contracts"] = contracts_value
    summaries["killswitch_triggered"] = 0
    summaries["day_result"] = summaries["net_pnl"].gt(0).map({True: "WIN", False: "LOSS"})
    summaries["source_file"] = csv_path.name
    summaries["source_type"] = "trade_detail_with_daily_summary"
    summaries["account"] = account
    merged = summaries.merge(trade_stats, on="date", how="left")
    merged["win_rate"] = merged["wins"] / merged["trade_count"] * 100.0
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    return merged


def load_account_seed(csv_path: Path, account: str) -> pd.DataFrame:
    header = pd.read_csv(csv_path, nrows=1)
    columns = set(header.columns)
    if {"trade_num", "pnl", "cumulative_total_pnl"}.issubset(columns):
        return load_big_trade_detail(csv_path, account)
    if {"account", "gross_pnl", "net_pnl"}.issubset(columns):
        if account != "25K":
            raise ValueError(f"Daily performance format is only supported directly for 25K source: {csv_path}")
        return load_25k_daily(csv_path)
    raise ValueError(f"Unsupported seed CSV format for {csv_path}")


def build_label_rows(df_25k: pd.DataFrame, strategy_id: str) -> list[dict[str, object]]:
    label_df = df_25k[["date", "contracts", "gross_pnl", "source_file"]].copy()
    label_df["label_metric_value"] = label_df["gross_pnl"] / label_df["contracts"]
    label_df = label_df.sort_values("label_metric_value").reset_index(drop=True)
    label_df["metric_percentile"] = (label_df.index + 1) / len(label_df)
    q25 = float(label_df["label_metric_value"].quantile(0.25))
    q75 = float(label_df["label_metric_value"].quantile(0.75))

    def assign_label(value: float) -> str:
        if value <= q25:
            return "BAD"
        if value >= q75:
            return "GOOD"
        return "NEUTRAL"

    label_df["label"] = label_df["label_metric_value"].apply(assign_label)
    return [{
        "strategy_id": strategy_id,
        "date": row.date,
        "label": row.label,
        "label_metric": "gross_pnl_per_contract",
        "label_metric_value": float(row.label_metric_value),
        "metric_percentile": float(row.metric_percentile),
        "quantile_low": q25,
        "quantile_high": q75,
        "label_version": LABEL_VERSION,
        "source_account": "25K",
        "source_file": row.source_file,
    } for row in label_df.itertuples(index=False)]


def build_pilot_rows(label_rows: list[dict[str, object]], seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    label_to_dates = {"BAD": [], "GOOD": [], "NEUTRAL": []}
    for row in label_rows:
        label_to_dates[str(row["label"])].append(str(row["date"]))
    pilot_rows = []
    for label, count in DEFAULT_SAMPLE_COUNTS.items():
        dates = list(label_to_dates[label])
        sample_size = min(count, len(dates))
        if sample_size == 0:
            continue
        for date in rng.sample(dates, sample_size):
            pilot_rows.append({
                "date": date,
                "label": label,
                "sample_group": "pilot_10",
                "sample_seed": seed,
                "sample_version": SAMPLE_VERSION,
                "notes": "Seed sample for first manual/news-ingestion review",
            })
    pilot_rows.sort(key=lambda row: row["date"])
    return pilot_rows


def dataframe_to_records(df: pd.DataFrame, strategy_id: str) -> list[dict[str, object]]:
    columns = ["date", "account", "contracts", "trade_count", "wins", "losses", "gross_pnl", "net_pnl", "avg_win", "avg_loss", "win_rate", "max_single_loss", "max_single_win", "killswitch_triggered", "day_result", "source_file", "source_type"]
    records = df[columns].to_dict("records")
    for record in records:
        record["strategy_id"] = strategy_id
        record["max_intraday_drawdown"] = None
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Build seeded NQ day-intelligence database")
    parser.add_argument("--db-path", help="Optional SQLite database path override")
    parser.add_argument("--file-25k", required=True, help="Path to the 25K seed CSV")
    parser.add_argument("--file-150k", required=True, help="Path to the 150K seed CSV")
    parser.add_argument("--sample-seed", type=int, default=42)
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID, help="Strategy identifier for the seeded dataset")
    args = parser.parse_args()

    daily_25k = load_account_seed(Path(args.file_25k), account="25K")
    daily_150k = load_account_seed(Path(args.file_150k), account="150K")
    common_dates = sorted(set(daily_25k["date"]).intersection(set(daily_150k["date"])))
    if not common_dates:
        raise ValueError("No shared dates found between the 25K and 150K seed files")
    daily_25k = daily_25k[daily_25k["date"].isin(common_dates)].copy()
    daily_150k = daily_150k[daily_150k["date"].isin(common_dates)].copy()
    memory = DayIntelligenceMemory(db_path=args.db_path)
    memory.clear_seed_data()
    memory.replace_trading_days(dataframe_to_records(daily_25k, strategy_id=args.strategy_id))
    memory.replace_trading_days(dataframe_to_records(daily_150k, strategy_id=args.strategy_id))
    label_rows = build_label_rows(daily_25k, strategy_id=args.strategy_id)
    memory.replace_day_labels(label_rows)
    pilot_rows = build_pilot_rows(label_rows, seed=args.sample_seed)
    memory.replace_pilot_samples(pilot_rows)
    label_counts = pd.Series([row["label"] for row in label_rows]).value_counts().to_dict()
    print(f"Database: {memory.db_path}")
    print(f"Shared trading dates imported: {len(common_dates)}")
    print(f"Trading day rows imported: {len(daily_25k) + len(daily_150k)}")
    print(f"Label counts: {label_counts}")
    print("Pilot sample dates:")
    for row in pilot_rows:
        print(f"  {row['date']}  {row['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
