"""Universe refresh cronjob entry point.

This script is designed to run as a K8s CronJob (monthly) to:
1. Fetch all US common stock symbols from Finnhub
2. Get market cap for each company
3. Sort by market cap and save top N to Redis
4. The weekly fundamentals job then uses this list

Usage:
    PYTHONPATH=src python -m tokenomics.fundamentals.universe_job

Environment variables:
    FINNHUB_API_KEY: Finnhub API key (required)
    REDIS_HOST: Redis host (default: localhost)
    REDIS_PORT: Redis port (default: 6379)
    REDIS_PASSWORD: Redis password (optional)
    UNIVERSE_SIZE: Number of top companies to keep (default: 1500)
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import finnhub
import structlog
from pydantic_settings import BaseSettings

from tokenomics.fundamentals.finnhub import FinnhubFinancialsProvider
from tokenomics.fundamentals.store import FundamentalsStore


class UniverseSecrets(BaseSettings):
    """Minimal secrets for universe job - only requires Finnhub API key."""

    finnhub_api_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def setup_cronjob_logging() -> None:
    """Configure simple console logging for cronjob."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=shared_processors,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for noisy_logger in ["urllib3", "httpcore", "httpx"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)


setup_cronjob_logging()
logger = structlog.get_logger(__name__)


def main() -> int:
    """Run the universe refresh job.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    start_time = datetime.now(timezone.utc)

    print("=" * 80)
    print("TOKENOMICS UNIVERSE REFRESH JOB")
    print("=" * 80)
    print(f"Start Time: {start_time.isoformat()}")
    print()

    logger.info(
        "universe_job.starting",
        timestamp=start_time.isoformat(),
    )

    try:
        # Load configuration
        secrets = UniverseSecrets()
        if not secrets.finnhub_api_key:
            print("ERROR: FINNHUB_API_KEY environment variable not set")
            logger.error("universe_job.missing_finnhub_api_key")
            return 1

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = os.getenv("REDIS_PORT", "6379")
        redis_password = os.getenv("REDIS_PASSWORD")
        universe_size = int(os.getenv("UNIVERSE_SIZE", "1500"))

        print(f"Configuration:")
        print(f"  Redis Host:     {redis_host}")
        print(f"  Redis Port:     {redis_port}")
        print(f"  Redis Password: {'***' if redis_password else 'NOT SET'}")
        print(f"  Universe Size:  {universe_size}")
        print()

        logger.info(
            "universe_job.config_loaded",
            redis_host=redis_host,
            universe_size=universe_size,
        )

        # Initialize components
        print("Initializing providers...")
        provider = FinnhubFinancialsProvider(secrets)
        client = finnhub.Client(api_key=secrets.finnhub_api_key)
        print("  Finnhub provider: OK")

        print("Connecting to Redis...")
        store = FundamentalsStore()
        print("  Redis connection: OK")
        print()

        # Step 1: Fetch all US symbols (no limit - we want all)
        print("Fetching all US stock symbols...")
        # Use a very high limit to get all common stocks
        all_symbols = provider.get_us_symbols(limit=50000)
        print(f"  Fetched {len(all_symbols)} common stock symbols")
        print()

        logger.info(
            "universe_job.symbols_fetched",
            count=len(all_symbols),
        )

        if not all_symbols:
            print("ERROR: No symbols returned from Finnhub")
            logger.error("universe_job.no_symbols")
            return 1

        # Step 2: Get market cap for each symbol
        # Rate limiting: 60 calls/minute = 1 call/second
        rate_limit_delay = 1.0

        print(f"Fetching market cap for {len(all_symbols)} symbols...")
        print(f"  Rate limit: {rate_limit_delay}s between calls")
        print(f"  Estimated time: {len(all_symbols) / 60:.0f} minutes")
        print("-" * 60)

        symbols_with_marketcap: list[tuple[str, float]] = []
        no_data_count = 0
        error_count = 0

        for i, company in enumerate(all_symbols):
            symbol = company.symbol
            progress_pct = ((i + 1) / len(all_symbols)) * 100

            try:
                response = client.company_basic_financials(symbol=symbol, metric="all")
                metric = response.get("metric", {})
                market_cap = metric.get("marketCapitalization")

                if market_cap and market_cap > 0:
                    symbols_with_marketcap.append((symbol, market_cap))
                    logger.debug(
                        "universe_job.marketcap_fetched",
                        symbol=symbol,
                        market_cap=market_cap,
                    )
                else:
                    no_data_count += 1
                    logger.debug(
                        "universe_job.no_marketcap",
                        symbol=symbol,
                    )

            except Exception as e:
                error_count += 1
                logger.warning(
                    "universe_job.fetch_error",
                    symbol=symbol,
                    error=str(e),
                )

            # Progress update every 100 symbols
            if (i + 1) % 100 == 0:
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                remaining = len(all_symbols) - i - 1
                eta_minutes = (remaining * rate_limit_delay) / 60
                print(
                    f"  [{progress_pct:5.1f}%] {i + 1}/{len(all_symbols)} - "
                    f"With data: {len(symbols_with_marketcap)}, "
                    f"No data: {no_data_count}, "
                    f"Errors: {error_count} | "
                    f"ETA: {eta_minutes:.0f}min"
                )

            # Rate limiting
            time.sleep(rate_limit_delay)

        print("-" * 60)
        print(f"Market cap fetch complete!")
        print(f"  Total with market cap: {len(symbols_with_marketcap)}")
        print(f"  No market cap data: {no_data_count}")
        print(f"  Errors: {error_count}")
        print()

        # Step 3: Sort by market cap and take top N
        print(f"Sorting by market cap and selecting top {universe_size}...")
        symbols_with_marketcap.sort(key=lambda x: x[1], reverse=True)
        top_symbols = symbols_with_marketcap[:universe_size]

        print(f"  Selected {len(top_symbols)} companies")
        print()

        # Step 4: Save to Redis
        print("Saving universe to Redis...")
        store.save_universe(top_symbols)
        print("  Universe saved!")
        print()

        # Summary
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        duration_minutes = duration / 60

        logger.info(
            "universe_job.completed",
            duration_seconds=round(duration, 2),
            total_symbols=len(all_symbols),
            with_marketcap=len(symbols_with_marketcap),
            universe_size=len(top_symbols),
            no_data=no_data_count,
            errors=error_count,
        )

        # Print top 20 companies
        print("=" * 80)
        print("TOP 20 COMPANIES BY MARKET CAP")
        print("=" * 80)
        print(f"{'Rank':<6} {'Symbol':<8} {'Market Cap (M$)':>15}")
        print("-" * 35)
        for i, (symbol, mcap) in enumerate(top_symbols[:20], 1):
            print(f"{i:<6} {symbol:<8} {mcap:>15,.0f}")

        print()
        print("=" * 80)
        print("JOB SUMMARY")
        print("=" * 80)
        print(f"  Start Time:          {start_time.isoformat()}")
        print(f"  End Time:            {end_time.isoformat()}")
        print(f"  Duration:            {duration_minutes:.1f} minutes ({duration:.0f} seconds)")
        print(f"  Total Symbols:       {len(all_symbols)}")
        print(f"  With Market Cap:     {len(symbols_with_marketcap)}")
        print(f"  Universe Size:       {len(top_symbols)}")
        print(f"  No Data:             {no_data_count}")
        print(f"  Errors:              {error_count}")
        print()
        print("Job completed successfully!")
        print("=" * 80)

        store.close()
        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        logger.error(
            "universe_job.fatal_error",
            error=str(e),
            exc_info=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
