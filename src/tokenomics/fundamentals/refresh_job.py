"""Fundamentals refresh cronjob entry point.

This script is designed to run as a K8s CronJob (weekly) to:
1. Fetch the list of US common stock symbols from Finnhub
2. Fetch basic financials for each company
3. Calculate composite fundamental scores
4. Store everything in Redis for use by trading profiles

Usage:
    PYTHONPATH=src python -m tokenomics.fundamentals.refresh_job

Environment variables:
    FINNHUB_API_KEY: Finnhub API key (required)
    REDIS_HOST: Redis host (default: localhost)
    REDIS_PORT: Redis port (default: 6379)
    REDIS_PASSWORD: Redis password (optional)
    FUNDAMENTALS_LIMIT: Max companies to process (default: 1250)
    FUNDAMENTALS_BATCH_SIZE: Batch size for Redis writes (default: 50)
"""

import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import structlog
from pydantic_settings import BaseSettings

from tokenomics.fundamentals import (
    FinancialsFetchError,
    FinnhubFinancialsProvider,
    FundamentalsScorer,
    FundamentalsStore,
)
from tokenomics.fundamentals.scorer import FundamentalsScore
from tokenomics.models import BasicFinancials


class FundamentalsSecrets(BaseSettings):
    """Minimal secrets for fundamentals job - only requires Finnhub API key."""

    finnhub_api_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def setup_cronjob_logging() -> None:
    """Configure simple console logging for cronjob."""
    # Shared structlog processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Console formatter for human-readable output
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=shared_processors,
    )

    # Root logger - console only for cronjob
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Silence noisy third-party loggers
    for noisy_logger in ["urllib3", "httpcore", "httpx"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)


# Configure logging at import time
setup_cronjob_logging()
logger = structlog.get_logger(__name__)


@dataclass
class CompanyResult:
    """Result for a single company analysis."""

    symbol: str
    name: str
    score: float
    roe: Optional[float]
    debt_to_equity: Optional[float]
    revenue_growth: Optional[float]
    eps_growth: Optional[float]
    status: str  # "success", "failed", "no_data"


def format_pct(value: Optional[float]) -> str:
    """Format a percentage value for display."""
    if value is None:
        return "N/A"
    return f"{value:>7.2f}%"


def format_ratio(value: Optional[float]) -> str:
    """Format a ratio value for display."""
    if value is None:
        return "N/A"
    return f"{value:>7.2f}"


def format_score(value: float) -> str:
    """Format a score value for display."""
    return f"{value:>6.1f}"


def print_summary_table(results: list[CompanyResult]) -> None:
    """Print a formatted table of all company results to stdout.

    This will be visible in kubectl logs for the cronjob.
    """
    # Sort by score descending
    sorted_results = sorted(results, key=lambda x: x.score, reverse=True)

    # Table header
    header = (
        f"{'Rank':<6} "
        f"{'Symbol':<8} "
        f"{'Company Name':<35} "
        f"{'Score':>8} "
        f"{'ROE':>10} "
        f"{'D/E Ratio':>10} "
        f"{'Rev Grth':>10} "
        f"{'EPS Grth':>10} "
        f"{'Status':<10}"
    )
    separator = "=" * len(header)

    print("\n")
    print(separator)
    print("FUNDAMENTALS ANALYSIS SUMMARY")
    print(separator)
    print(header)
    print("-" * len(header))

    for rank, result in enumerate(sorted_results, 1):
        # Truncate company name if too long
        name = result.name[:33] + ".." if len(result.name) > 35 else result.name

        row = (
            f"{rank:<6} "
            f"{result.symbol:<8} "
            f"{name:<35} "
            f"{format_score(result.score):>8} "
            f"{format_pct(result.roe):>10} "
            f"{format_ratio(result.debt_to_equity):>10} "
            f"{format_pct(result.revenue_growth):>10} "
            f"{format_pct(result.eps_growth):>10} "
            f"{result.status:<10}"
        )
        print(row)

    print(separator)

    # Summary statistics
    successful = [r for r in results if r.status == "success"]
    cached = [r for r in results if r.status == "cached"]
    failed = [r for r in results if r.status == "failed"]
    no_data = [r for r in results if r.status == "no_data"]

    # Include both successful and cached in score stats
    with_scores = [r for r in results if r.status in ("success", "cached", "no_data")]

    if with_scores:
        scores = [r.score for r in with_scores if r.score > 0]
        avg_score = sum(scores) / len(scores) if scores else 0
        max_score = max(scores) if scores else 0
        min_score = min(scores) if scores else 0

        print(f"\nSTATISTICS:")
        print(f"  Total Processed:    {len(results)}")
        print(f"  From Cache:         {len(cached)}")
        print(f"  Fresh API Calls:    {len(successful)}")
        print(f"  No Data:            {len(no_data)}")
        print(f"  Failed:             {len(failed)}")
        print(f"  Average Score:      {avg_score:.1f}")
        print(f"  Max Score:          {max_score:.1f}")
        print(f"  Min Score:          {min_score:.1f}")

        # Top 10 summary
        print(f"\nTOP 10 COMPANIES BY SCORE:")
        for i, r in enumerate(sorted_results[:10], 1):
            print(f"  {i:>2}. {r.symbol:<6} - {r.score:.1f} ({r.name[:40]})")

        # Bottom 10 summary
        print(f"\nBOTTOM 10 COMPANIES BY SCORE:")
        for i, r in enumerate(sorted_results[-10:], len(sorted_results) - 9):
            print(f"  {i:>2}. {r.symbol:<6} - {r.score:.1f} ({r.name[:40]})")

    print(separator)
    print("\n")


def main() -> int:
    """Run the fundamentals refresh job.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    start_time = datetime.now(timezone.utc)

    print("=" * 80)
    print("TOKENOMICS FUNDAMENTALS REFRESH JOB")
    print("=" * 80)
    print(f"Start Time: {start_time.isoformat()}")
    print()

    logger.info(
        "fundamentals_job.starting",
        timestamp=start_time.isoformat(),
    )

    try:
        # Load configuration - only need Finnhub API key for this job
        secrets = FundamentalsSecrets()
        if not secrets.finnhub_api_key:
            print("ERROR: FINNHUB_API_KEY environment variable not set")
            logger.error("fundamentals_job.missing_finnhub_api_key")
            return 1

        # Check Redis configuration
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = os.getenv("REDIS_PORT", "6379")
        redis_password = os.getenv("REDIS_PASSWORD")

        print(f"Configuration:")
        print(f"  Redis Host:     {redis_host}")
        print(f"  Redis Port:     {redis_port}")
        print(f"  Redis Password: {'***' if redis_password else 'NOT SET'}")
        print(f"  Finnhub API:    {'***' + secrets.finnhub_api_key[-4:] if secrets.finnhub_api_key else 'NOT SET'}")
        print()

        if not redis_password:
            print("WARNING: REDIS_PASSWORD not set - connection may fail if Redis requires auth")
            logger.warning("fundamentals_job.redis_password_not_set")

        limit = int(os.getenv("FUNDAMENTALS_LIMIT", "1250"))
        batch_size = int(os.getenv("FUNDAMENTALS_BATCH_SIZE", "50"))

        print(f"  Companies Limit:   {limit}")
        print(f"  Batch Size:        {batch_size}")
        print()

        logger.info(
            "fundamentals_job.config_loaded",
            redis_host=redis_host,
            redis_port=redis_port,
            redis_password_set=bool(redis_password),
            limit=limit,
            batch_size=batch_size,
        )

        # Initialize components
        print("Initializing providers...")
        provider = FinnhubFinancialsProvider(secrets)
        scorer = FundamentalsScorer()
        print("  Finnhub provider: OK")
        print("  Scorer: OK")

        print("Connecting to Redis...")
        store = FundamentalsStore()
        print("  Redis connection: OK")
        print()

        logger.info("fundamentals_job.providers_initialized")

        # Step 1: Fetch US symbols
        print(f"Fetching US stock symbols (limit: {limit})...")
        symbols = provider.get_us_symbols(limit=limit)
        print(f"  Fetched {len(symbols)} symbols")
        print()

        logger.info(
            "fundamentals_job.symbols_fetched",
            count=len(symbols),
        )

        if not symbols:
            print("ERROR: No symbols returned from Finnhub")
            logger.error("fundamentals_job.no_symbols")
            return 1

        # Create a mapping of symbol to company name
        symbol_names = {s.symbol: s.description for s in symbols}

        # Step 2: Process each company
        # Rate limiting: 60 calls/minute = 1 call/second
        # Retry: up to 3 attempts with 2 second delay between retries
        # Cache: skip API call if data is < 7 days old
        rate_limit_delay = 1.0  # 1 second between calls (60/min)
        retry_delay = 2.0  # 2 seconds between retries
        max_retries = 3
        cache_max_age_days = 7

        print(f"Processing {len(symbols)} companies...")
        print(f"  Rate limit: {rate_limit_delay}s between calls ({int(60/rate_limit_delay)}/min)")
        print(f"  Retries: {max_retries} attempts, {retry_delay}s delay")
        print(f"  Cache: skip if data < {cache_max_age_days} days old")
        print("-" * 60)

        results: list[CompanyResult] = []
        batch: list[tuple[BasicFinancials, FundamentalsScore]] = []
        success_count = 0
        failed_count = 0
        no_data_count = 0
        retry_count = 0
        cached_count = 0

        for i, company in enumerate(symbols):
            symbol = company.symbol
            progress_pct = ((i + 1) / len(symbols)) * 100

            # Check cache first - skip API call if data is fresh
            cached = store.get_cached_result(symbol)
            if cached and cached.get("score_details"):
                cached_count += 1
                details = cached["score_details"]

                result = CompanyResult(
                    symbol=symbol,
                    name=company.description,
                    score=cached["score"],
                    roe=details.get("roe"),
                    debt_to_equity=details.get("debt_to_equity"),
                    revenue_growth=details.get("revenue_growth"),
                    eps_growth=details.get("eps_growth"),
                    status="cached",
                )
                results.append(result)

                logger.debug(
                    "fundamentals_job.cache_hit",
                    symbol=symbol,
                    score=cached["score"],
                    age_days=cached.get("age_days"),
                )

                # No rate limit needed for cache hits
                continue

            # Retry loop for each company
            financials = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    # Fetch financials
                    financials = provider.get_basic_financials(symbol)
                    break  # Success, exit retry loop

                except FinancialsFetchError as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        retry_count += 1
                        logger.warning(
                            "fundamentals_job.retry",
                            symbol=symbol,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            error=str(e),
                        )
                        print(f"    Retry {attempt + 1}/{max_retries} for {symbol}: {e}")
                        time.sleep(retry_delay)
                    else:
                        logger.warning(
                            "fundamentals_job.max_retries_exceeded",
                            symbol=symbol,
                            error=str(e),
                        )

                except Exception as e:
                    last_error = e
                    logger.error(
                        "fundamentals_job.unexpected_error",
                        symbol=symbol,
                        error=str(e),
                        exc_info=True,
                    )
                    break  # Don't retry unexpected errors

            if financials is not None:
                # Success - calculate score
                score = scorer.calculate_score(financials)

                # Add to batch for Redis
                batch.append((financials, score))

                # Track result
                result = CompanyResult(
                    symbol=symbol,
                    name=company.description,
                    score=score.composite_score,
                    roe=score.roe,
                    debt_to_equity=score.debt_to_equity,
                    revenue_growth=score.revenue_growth,
                    eps_growth=score.eps_growth,
                    status="success" if score.has_sufficient_data else "no_data",
                )
                results.append(result)

                if score.has_sufficient_data:
                    success_count += 1
                else:
                    no_data_count += 1

                # Log every company with score
                logger.info(
                    "fundamentals_job.company_processed",
                    symbol=symbol,
                    score=score.composite_score,
                    roe=score.roe,
                    debt_ratio=score.debt_to_equity,
                    growth=score.revenue_growth,
                    has_data=score.has_sufficient_data,
                )
            else:
                # Failed after all retries
                failed_count += 1
                results.append(
                    CompanyResult(
                        symbol=symbol,
                        name=company.description,
                        score=0.0,
                        roe=None,
                        debt_to_equity=None,
                        revenue_growth=None,
                        eps_growth=None,
                        status="failed",
                    )
                )
                logger.warning(
                    "fundamentals_job.company_failed",
                    symbol=symbol,
                    error=str(last_error) if last_error else "Unknown error",
                )

            # Print progress every 50 companies
            if (i + 1) % 50 == 0:
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                api_calls = success_count + failed_count + no_data_count
                # ETA based on remaining non-cached companies (estimate)
                remaining = len(symbols) - i - 1
                eta_minutes = remaining / 60 if api_calls > 0 else remaining
                print(
                    f"  [{progress_pct:5.1f}%] Processed {i + 1}/{len(symbols)} - "
                    f"Cached: {cached_count}, Success: {success_count}, Failed: {failed_count}, "
                    f"No Data: {no_data_count}, Retries: {retry_count} | ETA: {eta_minutes:.0f}min"
                )

            # Save batch to Redis when full
            if len(batch) >= batch_size:
                store.save_batch(batch)
                logger.debug(
                    "fundamentals_job.batch_saved",
                    batch_size=len(batch),
                )
                batch = []

            # Rate limiting: 1 request per second (60/min limit)
            time.sleep(rate_limit_delay)

        # Save any remaining items in batch
        if batch:
            store.save_batch(batch)
            logger.info(
                "fundamentals_job.final_batch_saved",
                batch_size=len(batch),
            )

        print("-" * 60)
        print(f"Processing complete!")
        print()

        # Final summary
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        duration_minutes = duration / 60

        # Get top 10 from Redis to verify storage
        top_companies = store.get_top_scores(10)
        total_in_store = store.get_total_count()

        logger.info(
            "fundamentals_job.completed",
            duration_seconds=round(duration, 2),
            total_symbols=len(symbols),
            cached=cached_count,
            success=success_count,
            failed=failed_count,
            no_data=no_data_count,
            retries=retry_count,
            total_in_store=total_in_store,
            top_10=[(s, round(score, 1)) for s, score in top_companies],
        )

        # Print the summary table
        print_summary_table(results)

        # Final job summary
        print("JOB SUMMARY")
        print("=" * 60)
        print(f"  Start Time:         {start_time.isoformat()}")
        print(f"  End Time:           {end_time.isoformat()}")
        print(f"  Duration:           {duration_minutes:.1f} minutes ({duration:.0f} seconds)")
        print(f"  Companies in Redis: {total_in_store}")
        print()
        print("Job completed successfully!")
        print("=" * 60)

        store.close()
        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        logger.error(
            "fundamentals_job.fatal_error",
            error=str(e),
            exc_info=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
