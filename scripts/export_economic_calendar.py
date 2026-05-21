#!/usr/bin/env python3
"""Export a unified macro event calendar for NewsIntelligence and ToldYouSo."""

from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from news_intelligence.day_memory import DayIntelligenceMemory
from news_intelligence.day_models import MacroEvent
from news_intelligence.day_providers import FedCalendarMacroProvider, FredMacroProvider, _stable_event_id

START_DATE = "2019-01-01"
END_DATE = "2026-05-01"
NEWS_OUTPUT = REPO_ROOT / "data" / "economic_calendar.csv"
TOLDYOUSO_OUTPUT = Path("/home/dev/projects/ToldYouSo/lean/data/economic_calendar.csv")
CSV_COLUMNS = ["date", "event_type", "event_name", "category", "scheduled_time", "importance", "source"]
MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}
FOMC_FALLBACK_DATES = {
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
}


def load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def third_friday(year: int, month: int) -> date:
    cursor = date(year, month, 1)
    while cursor.weekday() != 4:
        cursor += timedelta(days=1)
    return cursor + timedelta(days=14)


def build_opex_events(start_date: str = START_DATE, end_date: str = END_DATE) -> list[MacroEvent]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    events = []
    for year in range(start.year, end.year + 1):
        for month in range(1, 13):
            opex_date = third_friday(year, month)
            if opex_date < start or opex_date > end:
                continue
            is_quarterly = month in {3, 6, 9, 12}
            event_name = "Quarterly Options Expiration" if is_quarterly else "Monthly Options Expiration"
            events.append(MacroEvent(
                event_id=_stable_event_id("opex", opex_date.isoformat(), "quarterly" if is_quarterly else "monthly"),
                date=opex_date.isoformat(),
                timestamp=f"{opex_date.isoformat()}T09:30:00",
                provider="computed",
                event_name=event_name,
                category="MARKET_STRUCTURE",
                scheduled_time="09:30:00",
                importance=0.8 if is_quarterly else 0.6,
                known_before_open=True,
                raw_payload={"event_type": "QUARTERLY_OPEX" if is_quarterly else "OPEX"},
            ))
    return events


def _normalize_fomc_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " "))


def _extract_fomc_dates_from_page(text: str, start_date: str = START_DATE, end_date: str = END_DATE) -> set[str]:
    normalized = _normalize_fomc_text(text)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = {value for value in FOMC_FALLBACK_DATES if start_date <= value <= end_date}
    for year_match in re.finditer(r"(20\d{2}) FOMC Meetings", normalized):
        year = int(year_match.group(1))
        section_end = re.search(r"20\d{2} FOMC Meetings", normalized[year_match.end():])
        section = normalized[year_match.end(): year_match.end() + section_end.start()] if section_end else normalized[year_match.end():]
        for month_name, month in MONTHS.items():
            pattern = (
                rf"\b{month_name}\b\s+(\d{{1,2}})(?:-(\d{{1,2}}))?\*?"
                rf"(?=\s+(?:Statement:|Press Conference|Projection Materials|Minutes:)"
                rf"|\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b)"
            )
            for month_match in re.finditer(pattern, section):
                day_text = month_match.group(2) or month_match.group(1)
                day = int(day_text)
                event_year = year
                if month_match.group(2) and day < int(month_match.group(1)):
                    event_month = month + 1
                    if event_month == 13:
                        event_month = 1
                        event_year += 1
                else:
                    event_month = month
                try:
                    event_date = date(event_year, event_month, day)
                except ValueError:
                    continue
                if start <= event_date <= end:
                    dates.add(event_date.isoformat())
    return dates


def fetch_fomc_events(start_date: str = START_DATE, end_date: str = END_DATE) -> list[MacroEvent]:
    provider = FedCalendarMacroProvider()
    response = provider._get(provider.CALENDAR_URL, timeout=45)
    events = []
    for event_date in sorted(_extract_fomc_dates_from_page(response.text, start_date, end_date)):
        events.append(MacroEvent(
            event_id=_stable_event_id("fedcal", event_date, "fomc_rate_decision"),
            date=event_date,
            timestamp=f"{event_date}T14:00:00",
            provider=provider.name,
            event_name="FOMC Rate Decision",
            category="MONETARY_POLICY",
            scheduled_time="14:00:00",
            importance=1.0,
            known_before_open=True,
            raw_payload={"event_type": "FOMC", "source_url": provider.CALENDAR_URL},
        ))
    return events


def event_type(event: MacroEvent) -> str:
    if event.raw_payload.get("event_type"):
        return str(event.raw_payload["event_type"])
    if event.provider == "fed_calendar":
        return "FOMC"
    if event.provider == "computed":
        return "OPEX"
    return event.category


def calendar_row(event: MacroEvent) -> dict[str, object]:
    return {
        "date": event.date,
        "event_type": event_type(event),
        "event_name": event.event_name,
        "category": event.category,
        "scheduled_time": event.scheduled_time,
        "importance": f"{event.importance:.1f}",
        "source": event.provider,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not path.parent.exists():
        raise FileNotFoundError(f"Output directory does not exist: {path.parent}")
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def missing_months(rows: list[dict[str, object]], event_type_name: str) -> list[str]:
    by_month = {str(row["date"])[:7] for row in rows if row["event_type"] == event_type_name}
    start = date(2019, 1, 1)
    end = date(2026, 5, 1)
    gaps = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        month_key = cursor.strftime("%Y-%m")
        if month_key not in by_month:
            gaps.append(month_key)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return gaps


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    by_type = Counter(str(row["event_type"]) for row in rows)
    by_year_type: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_year_type[str(row["date"])[:4]][str(row["event_type"])] += 1
    return {
        "row_count": len(rows),
        "date_range": (rows[0]["date"], rows[-1]["date"]) if rows else ("", ""),
        "by_type": dict(sorted(by_type.items())),
        "by_year_type": {year: dict(sorted(counter.items())) for year, counter in sorted(by_year_type.items())},
        "missing_cpi_months": missing_months(rows, "CPI"),
        "missing_nfp_months": missing_months(rows, "NFP"),
    }


def main() -> int:
    load_dotenv()
    memory = DayIntelligenceMemory()
    fred_events = FredMacroProvider().fetch_release_events(START_DATE, END_DATE)
    fomc_events = fetch_fomc_events(START_DATE, END_DATE)
    opex_events = build_opex_events(START_DATE, END_DATE)
    stored_events = sorted(fred_events + fomc_events + opex_events, key=lambda event: (event.date, event.scheduled_time, event.provider, event.event_name))
    memory.replace_macro_events(stored_events)
    export_events = [event for event in stored_events if event_type(event) != "FEDFUNDS"]
    rows = [calendar_row(event) for event in export_events]
    write_csv(NEWS_OUTPUT, rows)
    write_csv(TOLDYOUSO_OUTPUT, rows)
    summary = summarize(rows)
    print(f"Row count: {summary['row_count']}")
    print(f"Date range: {summary['date_range'][0]} through {summary['date_range'][1]}")
    print("Events by type:")
    for name, count in summary["by_type"].items():
        print(f"  {name}: {count}")
    print(f"Missing CPI months: {', '.join(summary['missing_cpi_months']) or 'none'}")
    print(f"Missing NFP months: {', '.join(summary['missing_nfp_months']) or 'none'}")
    print(f"Omitted FEDFUNDS daily H.15 rows from CSV: {len(stored_events) - len(export_events)}")
    print(f"NewsIntelligence output: {NEWS_OUTPUT}")
    print(f"ToldYouSo output: {TOLDYOUSO_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
