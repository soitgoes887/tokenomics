# Tokenomics — Deep Research Report

## Executive Summary

Tokenomics is a **quantitative equity trading system** that combines fundamental financial analysis with LLM-powered sentiment analysis to manage a long-only US equities portfolio. The system runs as a set of Kubernetes CronJobs that periodically score ~1,500 US stocks on fundamental quality metrics, compute optimal portfolio weights, and execute rebalancing trades via the Alpaca brokerage API.

The project is in active development on branch `feat/v3.1`, evolving from a v2 three-factor scoring model (ROE/Debt/Growth) toward a v3 four-factor cross-sectional model (Value/Quality/Momentum/LowVol). Both scoring models run simultaneously on separate Alpaca paper trading accounts for A/B comparison.

---

## 1. System Architecture

### 1.1 High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     MONTHLY (1st, 01:00 UTC)                    │
│  universe_job ──► Finnhub API ──► Redis                         │
│  (fetch top 1,500 US stocks by market cap + sectors)            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     WEEKLY (Monday, 02:00/03:00 UTC)            │
│  refresh_job ──► Finnhub API ──► Scorer ──► Redis               │
│  (fetch financials, calculate composite scores per profile)     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     WEEKLY (Monday, 15:00/16:00 UTC)            │
│  RebalancingEngine ──► Redis scores ──► Target weights          │
│      ──► Alpaca API ──► Execute SELL then BUY orders            │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Map

| Layer | Module | Responsibility |
|-------|--------|----------------|
| **Entry Points** | `__main__.py` | CLI entry, config loading, engine bootstrap |
| | `fundamentals/universe_job.py` | Monthly universe refresh CronJob |
| | `fundamentals/refresh_job.py` | Weekly fundamentals scoring CronJob |
| **Configuration** | `config.py` | Pydantic models for YAML + .env config |
| | `config/settings.yaml` | All tunable strategy parameters |
| | `logging_config.py` | Three-stream structured logging setup |
| **Scoring** | `fundamentals/scorer.py` | v2 FundamentalsScorer (3-factor) |
| | `fundamentals/composite_scorer.py` | v3 CompositeScorer (4-factor cross-sectional) |
| | `fundamentals/scorer_registry.py` | Dynamic scorer registration/lookup |
| **Data** | `fundamentals/finnhub.py` | Finnhub API client (financials + universe) |
| | `fundamentals/store.py` | Redis storage layer for scores and universe |
| | `models.py` | Pydantic domain models (50+ financial fields) |
| **Portfolio** | `rebalancing/engine.py` | Orchestrator: scores → weights → trades |
| | `rebalancing/portfolio.py` | Target weight computation with caps |
| | `rebalancing/trader.py` | Trade generation with thresholds |
| **Trading** | `trading/broker.py` | Alpaca order execution (fractional shares) |
| | `trading/base.py` | Abstract broker interface |
| **Analysis** | `analysis/sentiment.py` | Gemini LLM sentiment analysis |
| | `analysis/perplexity.py` | Perplexity Sonar sentiment analysis |
| | `analysis/base.py` | Abstract LLM provider interface |
| **Providers** | `providers.py` | Factory pattern for LLM + broker instantiation |

---

## 2. The Three CronJobs

### 2.1 Universe Job (`universe_job.py`)

**Schedule:** 1st of every month at 01:00 UTC
**Duration:** ~3-5 hours (rate-limited API calls)
**Purpose:** Build the investable stock universe

**Process:**
1. Fetches all US common stock symbols from Finnhub (~8,000 symbols)
2. Filters to major exchanges only (NYSE: XNYS, NASDAQ: XNAS, AMEX: XASE, plus BATS, IEX, ARCX, XCIS)
3. Excludes special instruments: warrants (`.W`), units (`.U`), rights (`.R`), preferred shares (`-P`, `-A`), fractional RE, closed-end funds
4. For each valid symbol, fetches market cap and sector via two Finnhub API calls (rate-limited to 1/sec)
5. Sorts by market cap descending, keeps top 1,500
6. Saves to Redis: symbol list, market cap sorted set, sector hash map

**Redis Keys Written:**
- `fundamentals:universe` → Hash with `symbols` (JSON list), `updated_at`, `count`
- `fundamentals:universe:marketcap` → Sorted set (symbol → market cap)
- `fundamentals:universe:sectors` → Hash (symbol → sector string)
- TTL: 45 days

### 2.2 Fundamentals Refresh Job (`refresh_job.py`)

**Schedule:** Weekly on Monday — v2 at 02:00 UTC, v3 at 03:00 UTC (staggered)
**Duration:** ~16 hours (1,500 symbols × Finnhub rate limits)
**Purpose:** Calculate quality scores for every stock in the universe

**Process:**
1. Loads universe from Redis (populated by universe_job)
2. Checks Redis cache for each symbol — skips if data is <7 days old
3. Fetches fresh financials from Finnhub `/stock/metric?metric=all` endpoint
4. Runs configured scorer (v2 `FundamentalsScorer` or v3 `CompositeScorer`)
5. Saves scores to Redis in batches of 50
6. Prints summary table with statistics

**Profile-Aware:** Each scoring profile gets its own Redis namespace:
- v2: `fundamentals:v2_base:scores`, `fundamentals:v2_base:{SYMBOL}`
- v3: `fundamentals:v3_composite:scores`, `fundamentals:v3_composite:{SYMBOL}`

**Universe data is shared** across all profiles (no namespace prefix on universe keys).

### 2.3 Rebalancing Engine (`rebalancing/engine.py`)

**Schedule:** Weekly on Monday — v2 at 15:00 UTC, v3 at 16:00 UTC
**Duration:** ~5 minutes
**Purpose:** Execute trades to match target portfolio weights

**Process:**
1. Check market hours (early exit if `market_hours_only=true` and market is closed)
2. Load top scores from Redis (loads 2× `top_n_stocks` to allow filtering)
3. Compute target weights using `compute_target_weights()`
4. Fetch current positions from Alpaca
5. Calculate current weight per symbol: `market_value / total_portfolio_value`
6. Generate trade list via `generate_trades()` — filters by deviation threshold and min trade size
7. Execute all SELL orders first (to free up capital)
8. Execute all BUY orders
9. Log summary with turnover and trade counts

---

## 3. Scoring Models

### 3.1 v2 — FundamentalsScorer (3-Factor)

A simple weighted-average model using three fundamental metrics:

| Factor | Weight | Metric | Range | Direction |
|--------|--------|--------|-------|-----------|
| ROE | 40% | Return on Equity | -20% to +40% | Higher = better |
| Debt | 30% | Debt-to-Equity Ratio | 0 to 3.0 | Lower = better (inverted) |
| Growth | 30% | Revenue/EPS Growth avg | -30% to +50% | Higher = better |

**Normalization:** Linear scaling to 0-100 with clamping at boundaries.

**Missing Data Handling:**
- Requires at least 2 of 3 components for `has_sufficient_data = True`
- Available components are re-weighted proportionally
- Completely missing data → neutral score of 50.0

**Special Case:** Debt-to-equity of exactly 0 is treated as missing data (returns neutral 50.0 for that component), since it's ambiguous whether it indicates zero debt or missing data.

### 3.2 v3 — CompositeScorer (4-Factor Cross-Sectional)

A more sophisticated cross-sectional ranking model. Unlike v2, which scores each stock independently, v3 ranks stocks relative to the entire universe.

| Factor | Default Weight | Metrics Used |
|--------|---------------|-------------|
| Value | 30% (configurable) | Earnings yield (1/PE), Free cash flow yield (1/P-CF), Book/Price (1/PB) |
| Quality | 40% (configurable) | ROE, ROIC, Gross margin, Leverage score (1/(1+D/E)) |
| Momentum | 20% (configurable) | 52-week price return |
| Low Volatility | 10% (configurable) | Inverse beta, Inverse 52-week price range |

**Algorithm:**
1. Build pandas DataFrame with all metrics for all stocks
2. Z-score each metric within its factor group
3. Average z-scores within each factor to get factor composite
4. Percentile-rank each factor score (optionally within-sector)
5. Weighted sum of four factor percentile ranks → raw composite
6. Final percentile rank of the composite → score 1-100

**Sector-Neutral Ranking:**
- If sector data is available and ≥10 symbols have sectors:
  - Sectors with ≥5 stocks (MIN_SECTOR_SIZE): rank within sector
  - Smaller sectors: fall back to global ranking
- This prevents large sectors (e.g., Tech) from dominating the portfolio

**Requirements:**
- All 4 sub-scores must be non-NaN for a composite score
- If any factor is entirely missing → `has_sufficient_data = False`, score = 50.0
- Single-symbol scoring returns neutral 50.0 (needs full universe context)

### 3.3 Scorer Registry Pattern

Scorers are registered at module load time and looked up by name:

```python
register_scorer("FundamentalsScorer", FundamentalsScorer)  # in scorer.py
register_scorer("CompositeScorer", CompositeScorer)        # in composite_scorer.py (via decorator)
```

The `create_scorer(name, **kwargs)` factory instantiates the correct scorer class with profile-specific keyword arguments (e.g., custom factor weights for v3).

---

## 4. Portfolio Construction

### 4.1 Weight Computation (`rebalancing/portfolio.py`)

The `compute_target_weights()` function converts scores to portfolio weights:

1. **Filter:** Remove stocks below `min_score` (default: 50.0)
2. **Select:** Take top `top_n_stocks` (default: 100) by score
3. **Weight:**
   - `"score"` mode: weight ∝ score (higher-scored stocks get more weight)
   - `"equal"` mode: 1/N equal weighting
4. **Normalize:** Scale weights to sum to 1.0
5. **Position Cap:** Clip any weight above `max_position_pct` (default: 5%)
6. **Re-normalize** after capping (excess redistributed proportionally)
7. **Sector Cap:** If sectors provided, clip any sector's total weight above `max_sector_pct` (default: 25%)
   - Within a capped sector, stocks are trimmed by within-sector percentile rank
   - Iterative process (max 5 passes) since capping one sector may push another over
8. **Final normalization** to ensure weights sum to 1.0

**Output:** `TargetPortfolio` dataclass with `weights` dict, `total_weight`, `stock_count`.

### 4.2 Trade Generation (`rebalancing/trader.py`)

The `generate_trades()` function compares target vs. current weights:

1. Union all symbols from target and current holdings
2. For each symbol, compute `delta = target_weight - current_weight`
3. Convert to USD: `delta_usd = delta * portfolio_value`
4. **Skip conditions:**
   - No price available for the symbol
   - Relative deviation < `rebalance_threshold_pct` (default: 20%) — avoids excessive turnover
   - Absolute trade size < `min_trade_usd` (default: $100) — avoids dust trades
5. Create `Trade` objects with descriptive reason strings
6. Separate into `sells` and `buys` lists

**Output:** `TradeList` with `sells`, `buys`, `skipped_count`, `total_turnover_usd`.

### 4.3 Trade Execution

Execution in `RebalancingEngine.run()` follows a strict order:
1. **SELL orders first** — frees up cash for buys
2. **BUY orders second** — uses freed capital

All trades use **notional (dollar-amount) market orders** via `submit_buy_order_notional()` / `submit_sell_order_notional()`. This enables fractional share support.

**Fractional Share Fallback:** If a symbol is not fractionable on Alpaca, the broker automatically falls back to whole-share orders by computing `floor(notional / price)`.

---

## 5. Configuration System

### 5.1 Layered Configuration

| Source | Contents | Loaded By |
|--------|----------|-----------|
| `config/settings.yaml` | Strategy parameters, thresholds, weights | `load_config()` → Pydantic `AppConfig` |
| `.env` | API keys (Alpaca, Gemini, Finnhub) | `pydantic-settings` → `Secrets` |
| Environment variables | `SCORING_PROFILE`, `FUNDAMENTALS_LIMIT`, `UNIVERSE_SIZE` | Direct `os.environ` reads |
| `Pulumi.dev.yaml` | K8s deployment secrets (encrypted) | Pulumi at deploy time |

### 5.2 Multi-Profile System

The system supports running multiple scoring strategies simultaneously via **scoring profiles**:

```yaml
scoring_profiles:
  profiles:
    tokenomics_v2_base:
      scorer_class: FundamentalsScorer
      redis_namespace: "fundamentals:v2_base"
      alpaca_api_key_env: ALPACA_API_KEY
      alpaca_secret_key_env: ALPACA_SECRET_KEY

    tokenomics_v3_composite:
      scorer_class: CompositeScorer
      redis_namespace: "fundamentals:v3_composite"
      alpaca_api_key_env: ALPACA_API_KEY_V3
      alpaca_secret_key_env: ALPACA_SECRET_KEY_V3
      scorer_kwargs:
        value_weight: 0.20
        quality_weight: 0.30
        momentum_weight: 0.40
        lowvol_weight: 0.10
  default_profile: tokenomics_v2_base
```

**Profile Isolation:**
- Each profile uses a **separate Redis namespace** for scores (universe is shared)
- Each profile can use **separate Alpaca trading accounts** (different API keys)
- Each profile's CronJobs are **staggered by 1 hour** to avoid Finnhub rate limit conflicts
- The active profile is selected via `SCORING_PROFILE` environment variable or `default_profile` config

### 5.3 Current Settings

| Parameter | Value | Notes |
|-----------|-------|-------|
| `capital_usd` | 100,000 | Portfolio capital |
| `top_n_stocks` | 100 | Hold top 100 stocks |
| `weighting` | "score" | Score-proportional weights |
| `max_position_pct` | 5.0% | Max per-stock allocation |
| `max_sector_pct` | 25.0% | Max per-sector allocation |
| `min_score` | 50.0 | Minimum quality threshold |
| `rebalance_threshold_pct` | 20.0% | Deviation threshold to trigger trade |
| `min_trade_usd` | $100 | Minimum trade size |
| `paper` | true | Paper trading mode |
| `market_hours_only` | true | Only trade when market is open |
| `position_size_min/max` | $500 / $5,000 | Position bounds |
| `max_open_positions` | 100 | Portfolio width |

---

## 6. External API Integrations

### 6.1 Finnhub

**Used For:** Stock universe, financial metrics, market cap, sector data

**Endpoints:**
- `stock_symbols("US")` — All US stock symbols
- `company_basic_financials(symbol, "all")` — Comprehensive financial metrics
- `company_profile2(symbol=symbol)` — Company info including sector

**Rate Limiting:**
- Universe job: 1 request/second (2 calls per symbol × 1,500 symbols ≈ 50 min)
- Refresh job: 0.04s between requests (~25 req/sec for financials)

**Data Mapped:** 50+ financial metrics including PE, PB, ROE, ROIC, D/E, revenue growth, EPS growth, beta, 52-week high/low, gross margin, free cash flow, current ratio, and more. Each maps from Finnhub's camelCase aliases to snake_case Pydantic fields.

### 6.2 Alpaca

**Used For:** Paper/live trading, position management, market clock

**Capabilities:**
- Market orders (buy/sell) by notional amount or share quantity
- Fractional share support with whole-share fallback
- Position queries
- Account info (equity, cash, buying power)
- Market clock (is_open, next_open, next_close)

**Crypto Support:** Symbols ending in USD/USDT/BTC/ETH automatically use `time_in_force=GTC` instead of `DAY`. Currently the system is equity-focused but crypto infrastructure exists.

### 6.3 Google Gemini (Sentiment Analysis)

**Used For:** LLM-powered news sentiment classification

**Model:** Gemini 2.5 Flash-Lite (configurable)
**Output:** Structured JSON with sentiment, conviction, time horizon, reasoning, key factors
**SDK:** `google-genai` (not the deprecated `google-generativeai`)

This component is part of the original Phase 1 "satellite strategy" and appears to be less central to the current v3 rebalancing workflow, which is fundamentals-driven.

### 6.4 Perplexity Sonar (Alternative Sentiment)

**Used For:** Alternative LLM sentiment analysis via OpenAI-compatible API

Mirrors the Gemini provider's interface exactly. Selected via `providers.llm` config.

### 6.5 Redis

**Used For:** Persistent storage of scores, universe, sectors, market caps

**Schema:**
- `fundamentals:{namespace}:{SYMBOL}` — Hash with raw_metrics, score, score_details, updated
- `fundamentals:{namespace}:scores` — Sorted set for leaderboard queries
- `fundamentals:universe` — Hash with universe metadata (shared across profiles)
- `fundamentals:universe:marketcap` — Sorted set (shared)
- `fundamentals:universe:sectors` — Hash (shared)

**Connection:** Reads from `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` environment variables. In K8s, connects to `redis.redis.svc.cluster.local:6379`.

---

## 7. Domain Models

All models use Pydantic `BaseModel` for validation and serialization.

### 7.1 Core Trading Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `NewsArticle` | id, headline, summary, content, symbols, source, url, created_at, fetched_at | Normalized news article from Alpaca |
| `SentimentResult` | article_id, headline, symbol, sentiment, conviction, time_horizon, reasoning, key_factors, analyzed_at | LLM analysis output |
| `TradeSignal` | signal_id, article_id, symbol, action, conviction, sentiment, position_size_usd, reasoning, generated_at | Trading decision |
| `Position` | symbol, alpaca_order_id, entry_price, quantity, position_size_usd, entry_date, stop_loss_price, take_profit_price, max_hold_date, status, exit_price, exit_date, pnl_usd, pnl_pct | Tracked position lifecycle |
| `PortfolioSnapshot` | timestamp, total_equity_usd, cash_usd, open_positions, unrealized_pnl_usd, realized_pnl_today_usd, daily_return_pct | Point-in-time state |

### 7.2 Fundamental Data Models

| Model | Fields | Purpose |
|-------|--------|---------|
| `BasicFinancials` | 50+ Optional fields with Finnhub aliases covering valuation, profitability, growth, financial health, per-share, dividends, market data, time series | Raw Finnhub financial data |
| `MetricDataPoint` | period, value | Single time series observation |
| `FundamentalsScore` | symbol, composite_score, roe_score, debt_score, growth_score, value_score, quality_score, momentum_score, lowvol_score, has_sufficient_data | Scoring output (v2+v3 compatible) |

### 7.3 Enums

| Enum | Values | Used In |
|------|--------|---------|
| `Sentiment` | BULLISH, NEUTRAL, BEARISH | SentimentResult |
| `TimeHorizon` | SHORT (1-5d), MEDIUM (1-4w), LONG (1-3mo) | SentimentResult |
| `TradeAction` | BUY, SELL, HOLD | TradeSignal |
| `TradeSide` | buy, sell | Trade (rebalancing) |

---

## 8. Infrastructure & Deployment

### 8.1 Docker

Multi-stage build targeting `python:3.14-slim`:

1. **Builder stage:** Creates venv, installs dependencies from `requirements.txt`
2. **Runtime stage:** Copies venv, source code, and config. Runs as non-root `appuser`

Supports multi-arch builds: `linux/amd64` and `linux/arm64`.

**Image:** `anicu/tokenomics` on Docker Hub.

### 8.2 Kubernetes (via Pulumi)

Pulumi Python IaC creates the following in a `tokenomics` namespace:

| Resource | Details |
|----------|---------|
| **Namespace** | `tokenomics` |
| **Secret** | `tokenomics-secrets` — All API keys |
| **Secret** | Redis password (copied from `redis` namespace) |
| **ConfigMap** | `rebalancer-config` — `settings.yaml` mounted read-only |
| **CronJob** | `universe-refresh` — Monthly, 1st at 01:00 UTC |
| **CronJob** | `{profile}-fundamentals` — Weekly Monday (v2: 02:00, v3: 03:00) |
| **CronJob** | `{profile}-rebalancer` — Weekly Monday (v2: 15:00, v3: 16:00) |

**Resource Limits:**
- Fundamentals/Universe jobs: 100m-500m CPU, 256-512Mi memory
- Rebalancer: 100m-500m CPU, 128-256Mi memory

**Job Policies:**
- Concurrency: `Forbid` (no parallel runs)
- History: Keep 3 successful + 3 failed
- TTL after finished: 24 hours (48 hours for universe job)
- Backoff: 2-3 retries

### 8.3 CI/CD (GitHub Actions)

Four-stage pipeline triggered on push to `main`:

```
test → build-and-push → deploy → release
```

1. **test:** `pytest tests/unit/ -v` on Python 3.14
2. **build-and-push:** Multi-arch Docker build, push to Docker Hub with `YYYYMMDD-<hash>` tag
3. **deploy:** `pulumi up` to K8s dev stack
4. **release:** Create GitHub release with image tag

Only runs on changes to: `src/`, `tests/`, `config/`, `infrastructure/`, `Dockerfile`, `requirements.txt`, `.github/workflows/`.

---

## 9. Logging System

Three separate structured log streams via `structlog`:

| Stream | File | Content |
|--------|------|---------|
| **App** | `logs/tokenomics.log` | Application events, startup, errors |
| **Trades** | `logs/trades.log` | Order audit trail (every buy/sell) |
| **Decisions** | `logs/decisions.log` | Every LLM analysis and signal decision |

**Format:** JSON in files, colored console output for terminal.

**Key Events (dot-notation):**
- `tokenomics.starting`, `rebalancer.starting`, `rebalancer.complete`
- `trade.buy`, `trade.sell`, `trade.skipped`
- `decision.sentiment`, `signal.generated`, `signal.skipped`

**Rotation:** 10MB per file, 5 backup files.

**Third-party silencing:** urllib3, httpcore, httpx, google_genai loggers are suppressed.

---

## 10. Test Suite

**66 unit tests** across 10 files, all with mocked external APIs:

| File | Module Tested | Tests |
|------|--------------|-------|
| `test_broker.py` | AlpacaBrokerProvider | 9 |
| `test_composite_scorer.py` | CompositeScorer (v3) | 11 |
| `test_config.py` | Config loading/validation | 14 |
| `test_models.py` | Domain models | 12 |
| `test_perplexity.py` | PerplexityLLMProvider | 5 |
| `test_providers.py` | Provider factory | 4 |
| `test_scorer_registry.py` | Scorer registry | 7 |
| `test_sentiment.py` | GeminiLLMProvider | 6 |
| `test_store_namespace.py` | FundamentalsStore | 15 (8 namespace + 7 sector) |

**Mocking Strategy:**
- All external APIs fully mocked (Alpaca, Gemini, OpenAI, Finnhub, Redis)
- No API keys required to run tests
- Shared fixtures in `conftest.py` for config, secrets, sample data

**Run command:**
```bash
PYTHONPATH=src pytest tests/unit/ -v
```

**Notable Test Coverage:**
- v3 composite scorer: NaN handling, sector-neutral ranking, custom weights, edge cases (zero PE, negative D/E)
- Config: Pydantic validation constraints, YAML loading, multi-profile resolution, env var precedence
- Broker: Order lifecycle, fractional fallback, API key precedence
- Store: Redis namespace isolation, sector storage, universe operations

---

## 11. Design Patterns & Technical Decisions

### 11.1 Abstract Base Classes

Both the LLM provider and broker use ABC interfaces:
- `LLMProvider` → `GeminiLLMProvider`, `PerplexityLLMProvider`
- `BrokerProvider` → `AlpacaBrokerProvider`

Enables swapping providers via config (`providers.llm`, `providers.broker`) without code changes.

### 11.2 Registry Pattern

Scorers use a global registry with decorator-based registration:
```python
@register_scorer("CompositeScorer", CompositeScorer)
```
The `fundamentals/__init__.py` imports `composite_scorer` to trigger registration at module load time.

### 11.3 Factory Pattern

`providers.py` maps string names to module:class paths and uses `importlib` for lazy loading:
```python
LLM_PROVIDERS = {"gemini-flash": "sentiment:GeminiLLMProvider"}
```

### 11.4 Retry with Exponential Backoff

All external API calls use `tenacity` retry decorators:
- Finnhub: 3 attempts, 2s base delay
- Alpaca orders: 3 attempts, exponential backoff
- Gemini/Perplexity: 3 attempts, 2-30s range

### 11.5 Pydantic Everywhere

Configuration (`AppConfig`), domain models, and API responses all use Pydantic v2:
- Type validation at boundaries
- Field constraints (ge, le, gt, lt)
- JSON serialization/deserialization
- Alias support for Finnhub camelCase → Python snake_case

### 11.6 Namespace Isolation

Multi-profile support uses Redis key namespacing. Each profile prefixes its keys differently while sharing universe data. This enables running v2 and v3 scorers against the same universe but with independent score storage and trading accounts.

---

## 12. Key Findings & Observations

### 12.1 Evolution from Sentiment to Fundamentals

The codebase shows a clear evolution path:
1. **Phase 1 (original):** Real-time news sentiment analysis → immediate trading signals → position management with stop-loss/take-profit. This is the system described in `CLAUDE.md`.
2. **Current state:** Weekly fundamentals-based scoring → portfolio rebalancing. The sentiment analysis modules (`analysis/sentiment.py`, `analysis/perplexity.py`) still exist and are tested, but the active entry point (`__main__.py`) now runs the `RebalancingEngine`, not the original `Engine`.

Several Phase 1 modules referenced in `CLAUDE.md` no longer exist or have been replaced:
- `engine.py` → replaced by `rebalancing/engine.py`
- `news/fetcher.py` → still exists but not called by the current workflow
- `trading/signals.py` → not found (signal generation now happens via scoring + rebalancing)
- `portfolio/manager.py` → not found (position management now handled by Alpaca directly)
- `portfolio/risk.py` → not found (risk limits enforced through config caps)

### 12.2 Dual-Strategy Design

The original `dual-strategy-guide.md` is referenced in `CLAUDE.md` but does not exist on disk, suggesting the project has moved past the planning phase and into direct implementation of the fundamentals strategy.

### 12.3 Long-Only Constraint

The system is strictly long-only:
- No short selling capability
- SELL orders only fire to reduce existing positions (rebalancing down) or exit positions entirely
- No inverse ETF or options support

### 12.4 A/B Testing Infrastructure

The multi-profile system is well-designed for comparing scoring strategies:
- v2 (3-factor) vs v3 (4-factor) run on separate Alpaca accounts
- Same universe, same rebalancing logic, different scores
- Results can be compared by examining the two accounts' P&L

### 12.5 Rate Limiting Considerations

The system is heavily constrained by Finnhub's API rate limits:
- Universe job: ~50 minutes for 1,500 symbols (2 calls/symbol at 1/sec)
- Fundamentals refresh: ~16 hours for 1,500 symbols
- 7-day cache freshness reduces redundant API calls

### 12.6 Sector-Neutral Scoring

The v3 CompositeScorer's sector-neutral ranking is a notable sophistication:
- Prevents tech-heavy portfolios common in simple momentum strategies
- Combined with `max_sector_pct: 25%` cap in portfolio construction for double protection
- Falls back to global ranking for small sectors (<5 stocks) to avoid unreliable within-sector percentiles

### 12.7 Fractional Share Handling

The broker handles the Alpaca API's fractional share limitations gracefully:
- Attempts notional (dollar-amount) orders first
- Falls back to whole-share orders if the symbol is not fractionable
- Calculates `floor(notional / price)` for whole shares

### 12.8 No Integration or E2E Tests

All 66 tests are unit tests with mocked APIs. There are no integration tests that verify:
- Redis read/write correctness with a real Redis instance
- End-to-end flow from scoring to trade execution
- Alpaca API response handling with real (paper) responses

### 12.9 Dependencies

The project uses modern Python tooling:
- Python 3.14 (bleeding edge)
- Pydantic v2 (not v1)
- `google-genai` (not deprecated `google-generativeai`)
- `structlog` for JSON logging
- `tenacity` for retry logic
- `redis` client (not `redis-py`)
- `pandas` + `numpy` for cross-sectional scoring
- No `pyproject.toml` — uses `requirements.txt` directly

---

## 13. File Inventory

### Source Code (`src/tokenomics/`)

```
src/tokenomics/
├── __init__.py                         # Package docstring
├── __main__.py                         # CLI entry point
├── config.py                           # Pydantic config models (12 classes)
├── models.py                           # Domain models (8 classes, 3 enums)
├── providers.py                        # Provider factory (LLM + broker)
├── logging_config.py                   # Three-stream structured logging
├── analysis/
│   ├── __init__.py
│   ├── base.py                         # LLMProvider ABC
│   ├── sentiment.py                    # GeminiLLMProvider
│   └── perplexity.py                   # PerplexityLLMProvider
├── fundamentals/
│   ├── __init__.py                     # Package exports + auto-register
│   ├── base.py                         # FinancialsProvider ABC + exceptions
│   ├── finnhub.py                      # FinnhubFinancialsProvider
│   ├── scorer.py                       # v2 FundamentalsScorer + BaseScorer
│   ├── composite_scorer.py             # v3 CompositeScorer (cross-sectional)
│   ├── scorer_registry.py              # Dynamic scorer registry
│   ├── store.py                        # Redis storage layer
│   ├── refresh_job.py                  # Weekly scoring CronJob
│   └── universe_job.py                 # Monthly universe CronJob
├── rebalancing/
│   ├── __init__.py                     # Package exports
│   ├── engine.py                       # RebalancingEngine orchestrator
│   ├── portfolio.py                    # Target weight computation
│   └── trader.py                       # Trade generation
└── trading/
    ├── __init__.py
    ├── base.py                         # BrokerProvider ABC
    └── broker.py                       # AlpacaBrokerProvider
```

### Tests

```
tests/
├── conftest.py                         # Shared fixtures
└── unit/
    ├── test_broker.py                  # 9 tests
    ├── test_composite_scorer.py        # 11 tests
    ├── test_config.py                  # 14 tests
    ├── test_models.py                  # 12 tests
    ├── test_perplexity.py              # 5 tests
    ├── test_providers.py               # 4 tests
    ├── test_scorer_registry.py         # 7 tests
    ├── test_sentiment.py               # 6 tests
    └── test_store_namespace.py         # 15 tests
```

### Configuration & Infrastructure

```
config/settings.yaml                    # Strategy parameters
.env.example                            # API key template
requirements.txt                        # Python dependencies
Dockerfile                              # Multi-stage, multi-arch
.github/workflows/ci.yaml              # CI/CD pipeline
infrastructure/
├── __main__.py                         # Pulumi K8s resources
├── Pulumi.yaml                         # Pulumi project metadata
├── Pulumi.dev.yaml                     # Dev stack config (encrypted secrets)
└── requirements.txt                    # Pulumi dependencies
```
