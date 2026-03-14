"""Backtesting job — K8s-runnable entry point.

Loads scores from Redis for one or more profiles, fetches historical OHLCV
from Alpaca, runs backtesting.py per symbol, and outputs a comparison table.

⚠ STATIC signal mode: current Redis scores define the portfolio composition
  for the entire backtest window.  This identifies how the current holdings
  would have performed historically, not how the strategy would have performed
  had it been running at the time.  See signal_generator.py for details.

Usage:
    PYTHONPATH=src python -m tokenomics.backtesting.backtest_job

Environment variables:
    BACKTEST_PROFILES       Comma-separated profile names
                            (default: tokenomics_v2_base,tokenomics_v3_composite)
    BACKTEST_START          ISO start date (default: 3 years ago)
    BACKTEST_END            ISO end date (default: today)
    BACKTEST_TOP_N          How many top stocks to include per profile (default: 100)
    BACKTEST_SYMBOLS_LIMIT  Max symbols to fetch per profile for speed (default: 50)
    ALPACA_API_KEY          Required — Alpaca market data API key
    ALPACA_SECRET_KEY       Required
    REDIS_HOST/PORT/PW      Redis connection
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import redis
import structlog
from pydantic_settings import BaseSettings

from tokenomics.backtesting.data_loader import OHLCVLoader
from tokenomics.backtesting.runner import run_profile
from tokenomics.backtesting.signal_generator import build_signals, build_trading_calendar
from tokenomics.fundamentals.store import FundamentalsStore


class BacktestSecrets(BaseSettings):
    alpaca_api_key: str
    alpaca_secret_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def _setup_logging() -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=shared,
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for noisy in ["urllib3", "httpcore", "httpx"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    h = logging.StreamHandler()
    h.setFormatter(formatter)
    root.addHandler(h)


_setup_logging()
logger = structlog.get_logger(__name__)


def _print_per_symbol_table(symbol_results: list[dict], profile_name: str) -> None:
    if not symbol_results:
        print("  (no results)")
        return

    # Sort by return descending
    rows = sorted(symbol_results, key=lambda r: r["return_pct"], reverse=True)
    hdr = (
        f"  {'Symbol':<8} {'Return%':>8} {'CAGR%':>8} {'Sharpe':>8} "
        f"{'MaxDD%':>9} {'Trades':>7} {'Win%':>7}"
    )
    sep = "  " + "-" * (len(hdr) - 2)
    print(f"\n  {profile_name} — per-symbol results ({len(rows)} stocks)")
    print(hdr)
    print(sep)
    for r in rows[:30]:
        print(
            f"  {r['symbol']:<8} {r['return_pct']:>8.1f} {r['return_ann_pct']:>8.1f}"
            f" {r['sharpe']:>8.3f} {r['max_drawdown_pct']:>9.1f}"
            f" {r['trades']:>7} {r['win_rate_pct']:>7.1f}"
        )
    if len(rows) > 30:
        print(f"  ... and {len(rows) - 30} more")


def _print_comparison_table(
    results: dict[str, dict],
    start: datetime,
    end: datetime,
) -> None:
    print("\n")
    print("=" * 80)
    print("BACKTEST COMPARISON SUMMARY")
    print(f"  Period:  {start.date()} → {end.date()}")
    print(
        "  NOTE:  Static signal mode — current portfolio composition replayed historically."
    )
    print("=" * 80)
    hdr = (
        f"  {'Profile':<34} {'Syms':>5} {'Ret%':>7} {'CAGR%':>7} "
        f"{'Vol%':>7} {'Sharpe':>8} {'MaxDD%':>8}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for profile, stats in results.items():
        if not stats:
            print(f"  {profile:<34} — no data —")
            continue
        print(
            f"  {profile:<34} {stats['symbols']:>5} {stats['total_return_pct']:>7.1f}"
            f" {stats['cagr_pct']:>7.1f} {stats['volatility_ann_pct']:>7.1f}"
            f" {stats['sharpe']:>8.3f} {stats['max_drawdown_pct']:>8.1f}"
        )
    print("=" * 80)
    print()


def _save_to_redis(results: dict, run_id: str) -> None:
    """Persist summary to Redis so it can be retrieved programmatically."""
    try:
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            socket_connect_timeout=10,
        )
        key = f"backtest:results:{run_id}"
        r.set(key, json.dumps(results, default=str), ex=30 * 24 * 3600)  # 30-day TTL
        r.close()
        logger.info("backtest_job.results_saved", key=key)
    except Exception as e:
        logger.warning("backtest_job.redis_save_failed", error=str(e))


def main() -> int:
    start_time = datetime.now(timezone.utc)

    print("=" * 80)
    print("TOKENOMICS BACKTESTING JOB")
    print("=" * 80)
    print(f"Start Time: {start_time.isoformat()}")
    print()

    try:
        secrets = BacktestSecrets()

        # Parse config from env vars
        profiles_env = os.getenv(
            "BACKTEST_PROFILES", "tokenomics_v2_base,tokenomics_v3_composite"
        )
        profiles = [p.strip() for p in profiles_env.split(",") if p.strip()]

        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        default_start = today - timedelta(days=3 * 365)

        start_str = os.getenv("BACKTEST_START", default_start.strftime("%Y-%m-%d"))
        end_str = os.getenv("BACKTEST_END", today.strftime("%Y-%m-%d"))
        start = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc)

        top_n = int(os.getenv("BACKTEST_TOP_N", "100"))
        symbols_limit = int(os.getenv("BACKTEST_SYMBOLS_LIMIT", "50"))

        print("Configuration:")
        print(f"  Profiles:       {', '.join(profiles)}")
        print(f"  Period:         {start.date()} → {end.date()}")
        print(f"  Top-N stocks:   {top_n}")
        print(f"  Symbols limit:  {symbols_limit}")
        print()

        loader = OHLCVLoader(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
        )
        calendar = build_trading_calendar(pd.Timestamp(start), pd.Timestamp(end))

        comparison: dict[str, dict] = {}
        run_id = start_time.strftime("%Y%m%d_%H%M%S")

        # --- Per-profile backtest ---
        for profile_name in profiles:
            print(f"Running profile: {profile_name}")
            print("-" * 60)

            # Determine Redis namespace from profile name pattern
            # e.g. tokenomics_v2_base → fundamentals:v2_base
            namespace_map = {
                "tokenomics_v2_base": "fundamentals:v2_base",
                "tokenomics_v3_composite": "fundamentals:v3_composite",
                "tokenomics_v4_regime": "fundamentals:v4_regime",
            }
            namespace = namespace_map.get(
                profile_name, f"fundamentals:{profile_name}"
            )

            store = FundamentalsStore(namespace=namespace)
            raw_scores = store.get_top_scores(limit=symbols_limit)
            store.close()

            if not raw_scores:
                print(f"  No scores in Redis for {profile_name} — skipping")
                comparison[profile_name] = {}
                continue

            print(f"  Loaded {len(raw_scores)} scores from Redis (namespace={namespace})")

            symbols = [s for s, _ in raw_scores]
            signals = build_signals(raw_scores, top_n=min(top_n, len(raw_scores)), trading_calendar=calendar)

            print(f"  Fetching OHLCV for {len(symbols)} symbols from Alpaca...")
            ohlcv = loader.load(symbols, start, end)
            print(f"  Got data for {len(ohlcv)} symbols")
            print()

            per_sym, portfolio = run_profile(ohlcv, signals)

            _print_per_symbol_table(per_sym, profile_name)
            comparison[profile_name] = portfolio

            logger.info(
                "backtest_job.profile_done",
                profile=profile_name,
                symbols=len(per_sym),
                portfolio_return=portfolio.get("total_return_pct"),
            )

        # --- Comparison table ---
        _print_comparison_table(comparison, start, end)

        # --- Save to Redis ---
        _save_to_redis(
            {
                "run_id": run_id,
                "start": str(start.date()),
                "end": str(end.date()),
                "profiles": comparison,
            },
            run_id,
        )

        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        print(f"Job completed in {duration:.0f}s")
        logger.info("backtest_job.completed", duration_seconds=round(duration, 1))
        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        logger.error("backtest_job.fatal_error", error=str(e), exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
