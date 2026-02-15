# Tokenomics

Algorithmic trading system that uses fundamental analysis to manage a score-weighted portfolio of US equities on Alpaca.

## How It Works

The system runs as three Kubernetes CronJobs:

1. **Universe Job** (Monthly) — Fetches top 1,500 US stocks by market cap from Finnhub (NYSE/NASDAQ only, excludes OTC)
2. **Fundamentals Job** (Weekly) — Calculates composite quality scores (ROE, Debt, Growth)
3. **Rebalancing Engine** (Weekly) — Trades to match score-weighted target portfolio

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           TOKENOMICS TRADING SYSTEM                                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘

                              EXTERNAL APIs
    ┌─────────────────────────────────────────────────────────────────┐
    │                                                                 │
    │   ┌─────────────┐         ┌─────────────┐       ┌───────────┐  │
    │   │   FINNHUB   │         │   ALPACA    │       │  ALPACA   │  │
    │   │  (Data API) │         │ (Data API)  │       │ (Trading) │  │
    │   └──────┬──────┘         └──────┬──────┘       └─────┬─────┘  │
    │          │                       │                    │        │
    └──────────┼───────────────────────┼────────────────────┼────────┘
               │                       │                    │
               │ symbols               │ prices             │ orders
               │ market cap            │                    │
               │ financials            │                    │
               ▼                       ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                         │
│   ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐        │
│   │    UNIVERSE JOB     │    │  FUNDAMENTALS JOB   │    │  REBALANCING ENGINE │        │
│   │    (Monthly)        │    │     (Weekly)        │    │      (Weekly)       │        │
│   │                     │    │                     │    │                     │        │
│   │  Schedule:          │    │  Schedule:          │    │  Schedule:          │        │
│   │  1st of month 01:00 │    │  Monday 02:00 UTC   │    │  Monday 15:00 UTC   │        │
│   │                     │    │                     │    │  (30min after open) │        │
│   │  Duration: ~3 hrs   │    │  Duration: ~16 hrs  │    │  Duration: ~5 min   │        │
│   └──────────┬──────────┘    └──────────┬──────────┘    └──────────┬──────────┘        │
│              │                          │                          │                   │
│   KUBERNETES CRONJOBS                   │                          │                   │
│                                         │                          │                   │
└──────────────┼──────────────────────────┼──────────────────────────┼───────────────────┘
               │                          │                          │
               ▼                          ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                       REDIS                                             │
│                                                                                         │
│   ┌───────────────────────────────┐  ┌───────────────────────────────────────────────┐ │
│   │      fundamentals:universe    │  │              fundamentals:scores              │ │
│   │                               │  │                                               │ │
│   │  {                            │  │  Sorted Set (by score descending):            │ │
│   │    symbols: [AAPL, MSFT, ...] │  │                                               │ │
│   │    count: 1500                │  │    NVDA  ─────────────────────────────  92.5  │ │
│   │    updated_at: 2025-02-01     │  │    MSFT  ───────────────────────────    82.0  │ │
│   │  }                            │  │    META  ──────────────────────────     78.0  │ │
│   │                               │  │    GOOGL ─────────────────────────      74.9  │ │
│   │  TTL: 45 days                 │  │    AAPL  ────────────────────────       68.4  │ │
│   └───────────────────────────────┘  │    ...                                        │ │
│                                      │                                               │ │
│   ┌───────────────────────────────┐  │  TTL: 14 days                                 │ │
│   │  fundamentals:universe:mcap   │  └───────────────────────────────────────────────┘ │
│   │                               │                                                    │
│   │  Sorted Set (by market cap):  │  ┌───────────────────────────────────────────────┐ │
│   │    AAPL  ────────── 3,500,000 │  │           fundamentals:{SYMBOL}               │ │
│   │    MSFT  ────────── 3,200,000 │  │                                               │ │
│   │    NVDA  ────────── 2,800,000 │  │  fundamentals:NVDA = {                        │ │
│   │    ...                        │  │    symbol: "NVDA",                            │ │
│   │                               │  │    score: 92.5,                               │ │
│   │  TTL: 45 days                 │  │    score_details: {                           │ │
│   └───────────────────────────────┘  │      roe: 45.0,                               │ │
│                                      │      debt_to_equity: 0.4,                     │ │
│                                      │      revenue_growth: 40.0                     │ │
│                                      │    },                                         │ │
│                                      │    updated: "2025-02-10T00:00:00Z"            │ │
│                                      │  }                                            │ │
│                                      │  TTL: 14 days                                 │ │
│                                      └───────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
  1st of Month                    Every Monday
       │                               │
       ▼                               ▼
  ┌─────────┐                    ┌───────────┐                         ┌─────────────┐
  │ 01:00   │                    │  02:00    │                         │   15:00     │
  │ Universe│                    │  Fundmtls │                         │  Rebalancer │
  │  Job    │                    │   Job     │                         │   Engine    │
  └────┬────┘                    └─────┬─────┘                         └──────┬──────┘
       │                               │                                      │
       │  1. Fetch all US symbols      │  1. Read universe from Redis         │  1. Load scores
       │     from Finnhub (NYSE/NASDAQ)│  2. For each symbol:                 │  2. Compute weights
       │  2. Get market cap each       │     - Check cache (skip if fresh)    │  3. Get current holdings
       │  3. Sort by market cap        │     - Fetch financials               │  4. Generate trades
       │  4. Save top 1500             │     - Calculate score                │  5. Execute orders
       │                               │     - Save to Redis                  │
       ▼                               ▼                                      ▼
  ┌─────────┐                    ┌───────────┐                         ┌─────────────┐
  │  REDIS  │───────────────────▶│   REDIS   │────────────────────────▶│   ALPACA    │
  │ universe│                    │  scores   │                         │   BROKER    │
  └─────────┘                    └───────────┘                         └─────────────┘
```

## Scoring Algorithm

```
                         ┌─────────────────────────────┐
                         │     BasicFinancials         │
                         │                             │
                         │  • ROE (Return on Equity)   │
                         │  • Debt/Equity Ratio        │
                         │  • Revenue Growth (TTM)     │
                         │  • EPS Growth (TTM)         │
                         └──────────────┬──────────────┘
                                        │
                                        ▼
         ┌──────────────────────────────────────────────────────────┐
         │                    FundamentalsScorer                    │
         │                                                          │
         │   ┌────────────┐   ┌────────────┐   ┌────────────┐      │
         │   │ ROE Score  │   │Debt Score  │   │Growth Score│      │
         │   │            │   │            │   │            │      │
         │   │ Range:     │   │ Range:     │   │ Range:     │      │
         │   │ -20% → 40% │   │ 0 → 3.0    │   │ -30% → 50% │      │
         │   │            │   │            │   │            │      │
         │   │ Higher =   │   │ Lower =    │   │ Higher =   │      │
         │   │ Better     │   │ Better     │   │ Better     │      │
         │   │            │   │ (inverted) │   │            │      │
         │   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘      │
         │         │                │                │              │
         │         │ × 40%          │ × 30%          │ × 30%        │
         │         │                │                │              │
         │         └────────────────┼────────────────┘              │
         │                          │                               │
         │                          ▼                               │
         │                 ┌─────────────────┐                      │
         │                 │ COMPOSITE SCORE │                      │
         │                 │     (0-100)     │                      │
         │                 └─────────────────┘                      │
         └──────────────────────────────────────────────────────────┘
```

**Weights:** ROE (40%) + Debt (30%) + Growth (30%)

| Metric | Range | Scoring |
|--------|-------|---------|
| ROE | -20% to +40% | Higher = better |
| Debt/Equity | 0 to 3.0 | Lower = better (inverted) |
| Revenue/EPS Growth | -30% to +50% | Higher = better |

## Rebalancing Algorithm

```
         ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
         │    REDIS     │         │    ALPACA    │         │    ALPACA    │
         │   (scores)   │         │  (holdings)  │         │   (prices)   │
         └──────┬───────┘         └──────┬───────┘         └──────┬───────┘
                │                        │                        │
                │ top 100 scores         │ current positions      │ latest prices
                ▼                        ▼                        ▼
         ┌─────────────────────────────────────────────────────────────────┐
         │                    compute_target_weights()                     │
         │                                                                 │
         │   1. Filter by min_score (50)                                   │
         │   2. Take top_n (100)                                           │
         │   3. Score-weight: weight = score / sum(all_scores)             │
         │   4. Cap at max_position_pct (5%)                               │
         │   5. Re-normalize to 100%                                       │
         └─────────────────────────────────┬───────────────────────────────┘
                                           │
                                           ▼
         ┌─────────────────────────────────────────────────────────────────┐
         │                      generate_trades()                          │
         │                                                                 │
         │   For each symbol:                                              │
         │     delta = target_weight - current_weight                      │
         │                                                                 │
         │     Skip if:                                                    │
         │       • |deviation| < 20% of target (threshold)                 │
         │       • |trade_usd| < $100 (min trade)                          │
         │                                                                 │
         │     Generate:                                                   │
         │       • SELL if delta < 0 (or target = 0)                       │
         │       • BUY if delta > 0                                        │
         └─────────────────────────────────┬───────────────────────────────┘
                                           │
                                           ▼
         ┌─────────────────────────────────────────────────────────────────┐
         │                      execute_trades()                           │
         │                                                                 │
         │   1. SELLS first (free up capital)                              │
         │   2. BUYS second                                                │
         └─────────────────────────────────────────────────────────────────┘
```

**Example (100-stock portfolio, showing 5):**

| Stock | Score | Weight | Target $ (of $100k) |
|-------|-------|--------|---------------------|
| NVDA | 92.5 | 1.32% | $1,320 |
| MSFT | 82.0 | 1.17% | $1,170 |
| GOOGL | 74.9 | 1.07% | $1,070 |
| AAPL | 68.4 | 0.98% | $980 |
| AMZN | 58.7 | 0.84% | $840 |

## Project Structure

```
src/tokenomics/
├── __main__.py              # Entry point
├── config.py                # Pydantic config from YAML + .env
├── models.py                # Domain models
├── rebalancing/
│   ├── engine.py            # Main rebalancing orchestrator
│   ├── portfolio.py         # Target weight computation
│   └── trader.py            # Trade generation
├── fundamentals/
│   ├── universe_job.py      # Monthly market cap ranking
│   ├── refresh_job.py       # Weekly score calculation
│   ├── scorer.py            # Composite score algorithm
│   ├── finnhub.py           # Finnhub API client
│   └── store.py             # Redis storage
├── trading/
│   ├── broker.py            # Alpaca order execution
│   └── base.py              # BrokerProvider interface
└── analysis/
    ├── sentiment.py         # Gemini LLM provider (for future use)
    └── perplexity.py        # Perplexity LLM provider (for future use)

config/settings.yaml         # All strategy parameters
infrastructure/              # Pulumi K8s deployment
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
#   FINNHUB_API_KEY=...
#   REDIS_HOST=...
#   REDIS_PASSWORD=...
```

### 3. Run jobs manually

```bash
# Universe job (monthly)
PYTHONPATH=src python -m tokenomics.fundamentals.universe_job

# Fundamentals job (weekly)
PYTHONPATH=src python -m tokenomics.fundamentals.refresh_job

# Rebalancer (weekly)
PYTHONPATH=src python -m tokenomics
```

## Configuration

All parameters are in `config/settings.yaml`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rebalancing.top_n_stocks` | 100 | Number of stocks in portfolio |
| `rebalancing.weighting` | "score" | Weight method: "score" or "equal" |
| `rebalancing.max_position_pct` | 5.0 | Max weight per stock (%) |
| `rebalancing.min_score` | 50.0 | Min score to include |
| `rebalancing.rebalance_threshold_pct` | 20.0 | Min deviation to trade (%) |
| `trading.paper` | true | Paper vs live trading |
| `trading.market_hours_only` | true | Only trade during US market hours |

## CronJob Schedule

| Job | Schedule | Time (UTC) | Duration |
|-----|----------|------------|----------|
| Universe | `0 1 1 * *` | 1st of month, 01:00 | ~3 hours |
| Fundamentals | `0 2 * * 1` | Monday, 02:00 | ~16 hours |
| Rebalancer | `0 15 * * 1` | Monday, 15:00 | ~5 minutes |

## Testing

```bash
PYTHONPATH=src pytest tests/unit/ -v
```

## Kubernetes Deployment

The Pulumi project in `infrastructure/` creates:

- **Namespace:** `tokenomics`
- **Secrets:** API keys (Alpaca, Finnhub, Redis)
- **ConfigMap:** `settings.yaml`
- **CronJobs:** universe-refresh, fundamentals-refresh, rebalancer

### Deploy

```bash
cd infrastructure
pulumi stack select dev
pulumi config set --secret tokenomics:alpaca_api_key <key>
pulumi config set --secret tokenomics:alpaca_secret_key <key>
pulumi config set --secret tokenomics:finnhub_api_key <key>
pulumi up
```

### Monitor

```bash
# Check cronjob status
kubectl -n tokenomics get cronjobs

# View rebalancer logs
kubectl -n tokenomics logs -f job/rebalancer-<id>

# Trigger manual run
kubectl -n tokenomics create job --from=cronjob/rebalancer rebalancer-manual
```

## Tech Stack

- **Python 3.14**
- **Alpaca** — trading API (commission-free)
- **Finnhub** — fundamental data API
- **Redis** — score and universe storage
- **Pydantic** — config validation + domain models
- **structlog** — structured JSON logging
- **Pulumi** — Kubernetes infrastructure as code
- **Docker** — multi-arch container (amd64 + arm64)
