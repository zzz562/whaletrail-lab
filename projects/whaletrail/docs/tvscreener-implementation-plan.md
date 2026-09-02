# tvscreener Watchlist Integration Plan

Date: 2026-08-11

## Goal

Integrate TradingView/tvscreener-style data into the existing WhaleTrail paper trading project as a low-cost supplemental data source for hourly/daily tracking.

The target workflow is a fixed watchlist, not high-frequency trading and not large-scale universe screening.

## Scope

Implement these five items:

1. Add `TVScreenerSource.get_quotes()` into `paper-live.py` so each tick also fetches and displays the configured watchlist snapshot.
2. Use TradingView/tvscreener snapshots as a fallback when the existing yfinance path cannot provide latest data.
3. Persist watchlist quote snapshots into SQLite for local history.
4. Generate hourly/daily Markdown reports for the watchlist.
5. Document MCP usage as an optional AI-analysis layer rather than the primary data collection path.

## Design

```text
config/watchlist.yaml
  ↓
whaletrail.data.watchlist.load_watchlist()
  ↓
TVScreenerSource.get_quotes()
  ↓
Repository.save_quote_snapshots()
  ↓
results/watchlist_report.md + paper-live console/Telegram summaries
```

The integration keeps three responsibilities separate:

- **Collection:** `TVScreenerSource` calls TradingView scanner endpoints.
- **Persistence:** `Repository` writes normalised snapshots to SQLite.
- **Presentation:** scripts print tables and generate Markdown reports.

## Data source rules

- Stocks, ETFs, and indexes use the TradingView `global` scanner endpoint.
- Futures use the TradingView `futures` scanner endpoint.
- `SP:SPX` is context only (`tradable: false`). Use `AMEX:SPY` for tradable S&P 500 paper trading.
- Local Chinese names come from `config/watchlist.yaml`, because TradingView often returns English descriptions for A shares.

## Deliverables

- `config/watchlist.yaml` — fixed watchlist with Yahoo and TradingView symbols.
- `docs/tvscreener-integration-runbook.md` — operational runbook.
- `docs/tvscreener-implementation-plan.md` — this implementation plan.
- `whaletrail/data/tvscreener_source.py` — TradingView scanner provider.
- `whaletrail/data/watchlist.py` — watchlist loader helpers.
- `whaletrail/storage/schema.py` and `repository.py` — quote snapshot persistence.
- `scripts/fetch-tvscreener-watchlist.py` — ad hoc fetch/save/report script.
- `scripts/watchlist-report.py` — Markdown report generator from saved snapshots.
- `scripts/paper-live.py` — paper-live integration.

## Verification

- Install dependencies with `python3 -m pip install -r requirements.txt`.
- Compile modules with `python3 -m compileall whaletrail scripts`.
- Fetch live watchlist data with `python3 scripts/fetch-tvscreener-watchlist.py --save-db --report results/watchlist_report.md`.
- Generate a report from SQLite with `python3 scripts/watchlist-report.py`.
- Run a single paper-live tick with `python3 scripts/paper-live.py tick`.

## MCP note

MCP is optional. It is useful when an AI assistant needs to ask exploratory questions such as “which watchlist item is strongest today?” or “find more A-share gold-related symbols.” It should not replace the deterministic scheduled collector that writes local SQLite history.
