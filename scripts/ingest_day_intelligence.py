#!/usr/bin/env python3
"""Unified ingestion for day-level intelligence providers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from news_intelligence.day_memory import DayIntelligenceMemory
from news_intelligence.day_providers import DEFAULT_FREE_PROVIDERS, DayEventProvider, MacroEventProvider, build_provider


def resolve_dates(memory: DayIntelligenceMemory, args: argparse.Namespace) -> list[str]:
    if args.dates:
        return args.dates
    dates = memory.get_pilot_sample_dates()
    if args.limit_days:
        return dates[: args.limit_days]
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest day intelligence from configured providers")
    parser.add_argument("--db-path", help="Optional SQLite database path override")
    parser.add_argument("--providers", default=",".join(DEFAULT_FREE_PROVIDERS), help="Comma-separated providers")
    parser.add_argument("--max-records", type=int, default=10, help="Max records per provider per day where supported")
    parser.add_argument("--dates", nargs="+", help="Specific ISO dates to ingest")
    parser.add_argument("--limit-days", type=int, help="Limit number of pilot dates when --dates is omitted")
    args = parser.parse_args()

    memory = DayIntelligenceMemory(db_path=args.db_path)
    dates = resolve_dates(memory, args)
    providers = [build_provider(name, max_records=args.max_records) for name in args.providers.split(",") if name.strip()]
    print("Providers:")
    for provider in providers:
        print(f"  {provider.name}")
    print("Dates:")
    for date in dates:
        print(f"  {date}")
    total_day_events = 0
    total_macro_events = 0
    failures = []
    for provider in providers:
        for date in dates:
            try:
                if isinstance(provider, DayEventProvider):
                    events = provider.fetch_day(date)
                    total_day_events += memory.replace_day_events(events)
                    print(f"{provider.name} {date}: stored {len(events)} day events")
                elif isinstance(provider, MacroEventProvider):
                    macro_events = provider.fetch_day_macro(date)
                    total_macro_events += memory.replace_macro_events(macro_events)
                    print(f"{provider.name} {date}: stored {len(macro_events)} macro events")
            except Exception as exc:
                failures.append((provider.name, date, str(exc)))
                print(f"{provider.name} {date}: FAILED - {exc}")
    print(f"Total day events stored:   {total_day_events}")
    print(f"Total macro events stored: {total_macro_events}")
    if failures:
        print("Failures:")
        for provider_name, date, message in failures:
            print(f"  {provider_name} {date}: {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
