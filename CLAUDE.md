# CLAUDE.md

Project context for Claude Code.

## What is this project?

Tokenomics is an algorithmic trading system that uses LLM-powered news sentiment analysis to trade US equities and crypto. It's Phase 1 (satellite strategy) of a dual-strategy system described in `dual-strategy-guide.md`. Phase 2 (fundamental analysis of SEC filings) is not yet implemented.

## Project layout

- `src/tokenomics/` — main application package
- `config/settings.yaml` — all tunable strategy parameters
- `tests/unit/` — 66 unit tests with mocked external APIs
- `infrastructure/` — Pulumi Python project for K8s deployment
- `.github/workflows/ci.yaml` — CI/CD: test → build → deploy → release
- `Dockerfile` — multi-stage, multi-arch (amd64 + arm64)
- `dual-strategy-guide.md` — original planning document for both strategies

## Key modules

- `engine.py` — main async event loop, orchestrates all components, runs as continuous daemon
- `news/fetcher.py` — polls Alpaca News API, deduplicates articles via in-memory set
- `analysis/sentiment.py` — sends articles to Gemini 2.5 Flash-Lite, parses structured JSON response
- `trading/signals.py` — converts sentiment results into BUY/SELL signals based on conviction threshold
- `trading/broker.py` — Alpaca order execution; uses GTC for crypto, DAY for equities
- `portfolio/manager.py` — tracks positions, checks stop-loss/take-profit/max-hold exits, persists state to `data/state.json`
- `portfolio/risk.py` — enforces daily/monthly loss limits, position size bounds
- `config.py` — Pydantic models loading from YAML + .env
- `models.py` — domain models: NewsArticle, SentimentResult, TradeSignal, Position

## How to run

```bash
source .venv/bin/activate
PYTHONPATH=src python -m tokenomics
```

API keys must be in `.env` (see `.env.example`). Config is in `config/settings.yaml`.

## How to test

```bash
source .venv/bin/activate
PYTHONPATH=src pytest tests/unit/ -v
```

All external APIs (Alpaca, Gemini) are mocked in tests. No API keys needed to run tests.

## Important patterns

- **Imports:** All imports use `from tokenomics.xxx import ...` — the package is under `src/tokenomics/`, so `PYTHONPATH=src` is required.
- **Config:** Secrets come from `.env` via `pydantic-settings`. Strategy params come from `config/settings.yaml` via PyYAML + Pydantic validation. Never hardcode parameters.
- **Logging:** Three separate structured log streams via structlog — `tokenomics.log` (app events), `trades.log` (order audit trail), `decisions.log` (every LLM analysis and signal decision). All log events use dot-notation keys like `tokenomics.starting`, `signal.skipped`, `trade.opened`.
- **State:** Position state persists to `data/state.json` via atomic write (write to .tmp, rename). Restored on startup with broker reconciliation.
- **Crypto vs equities:** Broker detects crypto symbols (ending in USD/USDT/BTC/ETH) and uses `time_in_force=GTC` instead of `DAY`.
- **Gemini SDK:** Uses `google-genai` (not the deprecated `google-generativeai`). Structured JSON output via `response_mime_type: application/json`.
- **Long-only:** Phase 1 only generates BUY signals for new positions. SELL signals only fire to close existing positions on bearish reversal.

## Infrastructure

- **Docker:** Multi-stage build, non-root user, `python:3.14-slim` base
- **CI/CD:** GitHub Actions pipeline: test → build multi-arch image → push to Docker Hub (`anicu/tokenomics`) → Pulumi deploy to K8s → GitHub release
- **K8s:** Pulumi creates namespace, Secret (API keys), ConfigMap (settings.yaml), Deployment (1 replica). Image tag configurable via Pulumi config or `IMAGE_TAG` env var.
- **Pulumi secrets:** API keys stored as encrypted Pulumi secrets in `Pulumi.dev.yaml`, set via `pulumi config set --secret`.

## Things to know

- The `Alpaca NewsSet` response structure is `response.data["news"]` (list of News objects), not `response.news`.
- `min_conviction` in settings.yaml is currently set to 20 for testing (default should be 70 for production).
- `market_hours_only` is currently `false` for testing (should be `true` for production).
- `exclude_contentless` is `false` because many Alpaca articles have empty summaries but valid headlines.
- Phase 2 (long-term fundamental analysis using SEC filings + Gemini Pro) is planned but not implemented. See `dual-strategy-guide.md` for the full design.
