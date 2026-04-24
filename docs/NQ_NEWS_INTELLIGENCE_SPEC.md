# NQ News Intelligence System

Standalone SQL-backed research and live-decision support system for `NQ`.

## Core Model

Each trading day can store:

- strategy performance
- `GOOD` / `NEUTRAL` / `BAD` label
- raw provider-normalized events
- structured macro events
- derived day features
- later Claude analysis and similarity links

## Current Tables

- `trading_days`
- `day_labels`
- `pilot_samples`
- `day_events`
- `macro_events`
- `day_features`
- `day_analysis`
- `day_similarity`

## Current Providers

- Alpaca market news
- Federal Reserve RSS
- Federal Register
- Wikipedia Current Events
- GDELT
- Fed calendar macro events

## Current Workflow

1. Build seed database from account result CSVs
2. Label days using 25K gross-per-contract
3. Create reproducible pilot sample
4. Ingest provider data for pilot dates
5. Review provider quality and day differences

## Notes

- GDELT is supported but noisy
- Alpaca is intended to be the primary market-news provider
- Macro and policy/legal providers are first-class citizens, not just headline enrichments
