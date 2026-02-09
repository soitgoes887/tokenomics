# Tokenomics

Algorithmic trading system that uses LLM-powered news sentiment analysis to trade US equities and crypto on Alpaca.

## How It Works

The system runs as a continuous daemon that:

1. Polls **Alpaca News API** for financial news every 30 seconds
2. Sends each article to **Google Gemini 2.5 Flash-Lite** for sentiment analysis
3. Generates trade signals when conviction is above threshold (default: 70%)
4. Executes trades on **Alpaca paper trading** (or live)
5. Manages positions with stop-loss (2.5%), take-profit (6%), and 13-week max hold

## Project Structure

```
src/tokenomics/
├── __main__.py              # Entry point: python -m tokenomics
├── config.py                # Pydantic config from YAML + .env
├── models.py                # Domain models (NewsArticle, SentimentResult, TradeSignal, Position)
├── logging_config.py        # Structured logging (app, trades, decisions)
├── engine.py                # Main async event loop
├── news/fetcher.py          # Alpaca News polling + deduplication
├── analysis/sentiment.py    # Gemini sentiment analysis
├── trading/broker.py        # Alpaca order execution (equities + crypto)
├── trading/signals.py       # Conviction filtering + position sizing
├── portfolio/manager.py     # Position lifecycle + state persistence
└── portfolio/risk.py        # Daily/monthly loss limits

config/settings.yaml         # All strategy parameters
infrastructure/              # Pulumi K8s deployment
.github/workflows/ci.yaml   # CI/CD pipeline
```

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env with your keys:
#   ALPACA_API_KEY=...
#   ALPACA_SECRET_KEY=...
#   GEMINI_API_KEY=...
```

### 3. Verify connectivity

```bash
PYTHONPATH=src python scripts/check_connectivity.py
```

### 4. Run

```bash
PYTHONPATH=src python -m tokenomics
```

### 5. Monitor

```bash
# All logs
tail -f logs/*.log

# Pretty-print trade log
tail -f logs/trades.log | jq .

# Decision log (every LLM analysis)
tail -f logs/decisions.log | jq .
```

Stop with `Ctrl+C` — graceful shutdown persists state to `data/state.json`.

## Configuration

All parameters are in `config/settings.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `strategy.capital_usd` | 10000 | Paper trading capital |
| `strategy.position_size_min_usd` | 500 | Min position size |
| `strategy.position_size_max_usd` | 1000 | Max position size |
| `strategy.max_open_positions` | 10 | Max concurrent positions |
| `sentiment.min_conviction` | 70 | Min conviction to trade (0-100) |
| `risk.stop_loss_pct` | 0.025 | Stop loss percentage |
| `risk.take_profit_pct` | 0.06 | Take profit percentage |
| `risk.max_hold_trading_days` | 65 | Max hold period (13 weeks) |
| `news.poll_interval_seconds` | 30 | News polling interval |
| `trading.paper` | true | Paper vs live trading |
| `trading.market_hours_only` | true | Only trade during US market hours |

## Testing

```bash
PYTHONPATH=src pytest tests/unit/ -v
```

66 unit tests covering config validation, models, news fetching, sentiment analysis, signal generation, risk management, broker execution, and position management.

## CI/CD Pipeline

The GitHub Actions pipeline (`.github/workflows/ci.yaml`) runs on every push to `main`:

1. **test** — runs pytest
2. **build-and-push** — builds multi-arch Docker image (amd64 + arm64), pushes to Docker Hub as `anicu/tokenomics:<YYYYMMDD-shorthash>`
3. **deploy** — runs `pulumi up` to update the Kubernetes deployment
4. **release** — creates a GitHub release with the image tag

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |
| `PULUMI_ACCESS_TOKEN` | Pulumi Cloud access token |
| `PULUMI_CONFIG_PASSPHRASE` | Pulumi stack encryption passphrase |
| `KUBECONFIG_B64` | Base64-encoded kubeconfig for the K8s cluster |

## Kubernetes Deployment

The Pulumi project in `infrastructure/` creates:

- **Namespace:** `tokenomics`
- **Secret:** API keys (Alpaca + Gemini)
- **ConfigMap:** `settings.yaml`
- **Deployment:** 1 replica with resource limits

### Manual deployment

```bash
cd infrastructure
pip install -r requirements.txt
pulumi stack select dev
pulumi config set --secret tokenomics:alpaca_api_key <key>
pulumi config set --secret tokenomics:alpaca_secret_key <key>
pulumi config set --secret tokenomics:gemini_api_key <key>
pulumi up
```

### Check pod status

```bash
kubectl -n tokenomics get pods
kubectl -n tokenomics logs -f deployment/tokenomics
```

## Architecture

```
Alpaca News API  →  NewsFetcher (poll + dedup)
                        │
                  list[NewsArticle]
                        │
                SentimentAnalyzer (Gemini Flash-Lite)
                        │
                 list[SentimentResult]
                        │
                SignalGenerator (conviction >= 70, capacity check)
                        │
                  TradeSignal | None
                        │
                RiskManager (daily/monthly loss limits)
                        │
                AlpacaBroker.submit_buy_order()
                        │
                PositionManager → state.json

Each tick also:
  PositionManager.check_exits(current_prices)
    → close on stop_loss | take_profit | max_hold
```

## Tech Stack

- **Python 3.14** with asyncio
- **Alpaca** — trading + news API (commission-free)
- **Google Gemini 2.5 Flash-Lite** — sentiment analysis
- **Pydantic** — config validation + domain models
- **structlog** — structured JSON logging
- **Pulumi** — Kubernetes infrastructure as code
- **Docker** — multi-arch container (amd64 + arm64)
