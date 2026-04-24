# NewsIntelligence

Standalone day-level news intelligence system for `NQ` research and live decision support.

## Included

- SQLite-backed storage for labeled trading days, events, macro events, and pilot samples
- Seed builder for turning account result CSVs into `GOOD` / `NEUTRAL` / `BAD` day labels
- Provider-agnostic event ingestion layer
- Initial providers for:
  - Alpaca news
  - Federal Reserve RSS
  - Federal Register
  - Wikipedia Current Events
  - GDELT
  - Fed calendar macro events
- Review script for inspecting a pilot sample from the database

## Quick Start

```bash
pip install -r requirements.txt
python3 scripts/build_day_intelligence_seed.py --file-25k "/path/to/25k Big.csv" --file-150k "/path/to/150k Big.csv"
python3 scripts/ingest_day_intelligence.py --max-records 10
python3 scripts/review_pilot_day_sample.py --headlines-per-day 3
```

## Notes

- The SQLite database is created at `data/day_intelligence.db`
- Alpaca ingestion requires `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`
- GDELT is supported but should be treated as supplemental because it is noisy and rate-limited
