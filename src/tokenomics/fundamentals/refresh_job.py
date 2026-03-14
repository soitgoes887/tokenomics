"""Fundamentals refresh cronjob entry point.

This script is designed to run as a K8s CronJob (weekly) to:
1. Read the stock universe from Redis (set by monthly universe job)
2. Fetch basic financials for each company
3. Calculate composite fundamental scores
4. Store everything in Redis for use by trading profiles

Requires the universe to exist in Redis (populated by universe_job).

Usage:
    PYTHONPATH=src python -m tokenomics.fundamentals.refresh_job

Environment variables:
    FINNHUB_API_KEY: Finnhub API key (required)
    REDIS_HOST: Redis host (default: localhost)
    REDIS_PORT: Redis port (default: 6379)
    REDIS_PASSWORD: Redis password (optional)
    FUNDAMENTALS_LIMIT: Max companies to process (default: 1000)
    FUNDAMENTALS_BATCH_SIZE: Batch size for Redis writes (default: 50)
"""

import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from pydantic_settings import BaseSettings

from tokenomics.config import PostFiltersConfig, load_config, resolve_profile
from tokenomics.fundamentals import (
    FinancialsFetchError,
    FinnhubFinancialsProvider,
    FundamentalsStore,
    NoFinancialsDataError,
    create_scorer,
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


def load_exclusion_list(file_path: Optional[str]) -> set[str]:
    """Load excluded symbols from a text file (one symbol per line).

    Returns an empty set if no file is configured or the file doesn't exist.
    """
    if not file_path:
        return set()

    path = Path(file_path)
    if not path.is_file():
        logger.warning(
            "fundamentals_job.exclusion_list_not_found",
            path=str(path),
        )
        return set()

    symbols = set()
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            symbols.add(stripped.upper())

    logger.info(
        "fundamentals_job.exclusion_list_loaded",
        path=str(path),
        count=len(symbols),
    )
    return symbols


import re

# Share-class suffixes: GOOGL→GOOG, BF.B→BF, BIO.B→BIO, BRK.A→BRK, etc.
_SHARE_CLASS_RE = re.compile(
    r"^(?P<issuer>.+?)(?:\.[A-Z]|[A-Z])$"
)

# Known multi-class issuers where the longer ticker is a share class.
# Maps (base, variant) pairs, e.g. GOOG/GOOGL both → "GOOG".
_KNOWN_SHARE_CLASSES: dict[str, str] = {
    # Alphabet
    "GOOG": "GOOG",
    "GOOGL": "GOOG",
    # Berkshire Hathaway
    "BRK.A": "BRK",
    "BRK.B": "BRK",
    # Brown-Forman
    "BF.A": "BF",
    "BF.B": "BF",
    # Bio-Rad
    "BIO": "BIO",
    "BIO.B": "BIO",
    # Fox Corp
    "FOX": "FOX",
    "FOXA": "FOX",
    # News Corp
    "NWS": "NWS",
    "NWSA": "NWS",
    # Heico
    "HEI": "HEI",
    "HEI.A": "HEI",
    # Lennar
    "LEN": "LEN",
    "LEN.B": "LEN",
    # Under Armour
    "UA": "UA",
    "UAA": "UA",
    # Zillow
    "Z": "Z",
    "ZG": "Z",
    # Discovery / Warner Bros Discovery
    "WBD": "WBD",
    "WBDA": "WBD",
    # Moog
    "MOG.A": "MOG",
    "MOG.B": "MOG",
}


def _issuer_key(symbol: str) -> str:
    """Map a ticker to its canonical issuer key for deduplication.

    Uses the known share-class table first, then falls back to stripping
    dot-suffixes (e.g. XYZ.B → XYZ).
    """
    if symbol in _KNOWN_SHARE_CLASSES:
        return _KNOWN_SHARE_CLASSES[symbol]

    # Fallback: strip ".A", ".B", etc.
    if "." in symbol:
        base = symbol.rsplit(".", 1)[0]
        return base

    return symbol


def apply_post_filters(
    batch: list[tuple],
    scores_by_symbol: dict[str, "FundamentalsScore"],
    filters: PostFiltersConfig,
) -> tuple[list[tuple], dict[str, str]]:
    """Apply post-scoring filters to the batch before saving to Redis.

    Args:
        batch: List of (BasicFinancials, FundamentalsScore) tuples
        scores_by_symbol: Map of symbol → FundamentalsScore for all scored stocks
                          (including cached) to support deduplication
        filters: PostFiltersConfig with thresholds

    Returns:
        Tuple of (filtered_batch, removed_reasons) where removed_reasons maps
        symbol → reason string for every removed stock
    """
    removed: dict[str, str] = {}

    if not batch:
        return batch, removed

    # 1. Quality filter
    if filters.min_quality is not None:
        kept = []
        for financials, score in batch:
            if score.quality_score is not None and score.quality_score < filters.min_quality:
                removed[score.symbol] = f"quality_score {score.quality_score:.1f} < {filters.min_quality}"
            else:
                kept.append((financials, score))
        batch = kept

    # 2. Speculative filter (LowVol < X AND Value < Y)
    if filters.speculative_lowvol is not None and filters.speculative_value is not None:
        kept = []
        for financials, score in batch:
            is_speculative = (
                score.lowvol_score is not None
                and score.value_score is not None
                and score.lowvol_score < filters.speculative_lowvol
                and score.value_score < filters.speculative_value
            )
            if is_speculative:
                removed[score.symbol] = (
                    f"speculative: lowvol {score.lowvol_score:.1f} < {filters.speculative_lowvol} "
                    f"AND value {score.value_score:.1f} < {filters.speculative_value}"
                )
            else:
                kept.append((financials, score))
        batch = kept

    # 3. Deduplicate share classes
    if filters.deduplicate_share_classes:
        # Build issuer → best (financials, score) map using ALL scores (batch + cached)
        # so we can detect duplicates even across cache boundaries
        all_symbols_in_batch = {score.symbol for _, score in batch}

        issuer_best: dict[str, tuple[str, float]] = {}  # issuer_key → (symbol, score)
        for symbol, fscore in scores_by_symbol.items():
            key = _issuer_key(symbol)
            if key not in issuer_best or fscore.composite_score > issuer_best[key][1]:
                issuer_best[key] = (symbol, fscore.composite_score)

        kept = []
        for financials, score in batch:
            key = _issuer_key(score.symbol)
            best_symbol, best_score = issuer_best[key]
            if best_symbol != score.symbol:
                removed[score.symbol] = (
                    f"duplicate share class: keeping {best_symbol} ({best_score:.1f}) "
                    f"over {score.symbol} ({score.composite_score:.1f})"
                )
            else:
                kept.append((financials, score))
        batch = kept

    return batch, removed


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
    previous_score: Optional[float] = None  # Score before this update

    # v3 composite sub-scores
    value_score: Optional[float] = None
    quality_score: Optional[float] = None
    momentum_score: Optional[float] = None
    lowvol_score: Optional[float] = None

    # Sector (from universe job)
    sector: Optional[str] = None


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


def print_summary_table(results: list[CompanyResult], scorer_kwargs: dict | None = None, sector_neutral: bool = False) -> None:
    """Print a formatted table of all company results to stdout.

    This will be visible in kubectl logs for the cronjob.
    Detects v3 composite sub-scores and shows the appropriate columns.
    """
    # Sort by score descending
    sorted_results = sorted(results, key=lambda x: x.score, reverse=True)

    # Detect if this is a v3 run (any result has composite sub-scores)
    is_v3 = any(r.value_score is not None for r in results)

    if is_v3:
        header = (
            f"{'Rank':<6} "
            f"{'Symbol':<8} "
            f"{'Company Name':<28} "
            f"{'Sector':<18} "
            f"{'Score':>8} "
            f"{'Value':>8} "
            f"{'Quality':>8} "
            f"{'Momntm':>8} "
            f"{'LowVol':>8} "
            f"{'Status':<10}"
        )
    else:
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
    if is_v3:
        # Extract weights from scorer_kwargs or use defaults
        vw = scorer_kwargs.get("value_weight", 0.25) if scorer_kwargs else 0.25
        qw = scorer_kwargs.get("quality_weight", 0.25) if scorer_kwargs else 0.25
        mw = scorer_kwargs.get("momentum_weight", 0.25) if scorer_kwargs else 0.25
        lw = scorer_kwargs.get("lowvol_weight", 0.25) if scorer_kwargs else 0.25

        print("COMPOSITE SCORING SUMMARY")
        if sector_neutral:
            print(f"  Score = pctrank({vw:.0%}*Value_sec + {qw:.0%}*Quality_sec + {mw:.0%}*Momentum_sec + {lw:.0%}*LowVol_sec)")
            print(f"  *_sec = within-sector percentile rank")
        else:
            print(f"  Score = pctrank({vw:.0%}*Value + {qw:.0%}*Quality + {mw:.0%}*Momentum + {lw:.0%}*LowVol)")
        print(f"  Value   = pctrank(avg_z(EarningsYield, FCFY, BookPrice))")
        print(f"  Quality = pctrank(avg_z(ROE, ROIC, GrossMargin, LeverageScore))")
        print(f"  Momntm  = pctrank(z(52wk_return))")
        print(f"  LowVol  = pctrank(avg_z(1/beta, 1/range_vol))")
        print(f"  * Companies missing any sub-score are excluded from ranking")
    else:
        print("FUNDAMENTALS ANALYSIS SUMMARY")
    print(separator)
    print(header)
    print("-" * len(header))

    for rank, result in enumerate(sorted_results, 1):
        # Truncate company name if too long
        name = result.name[:26] + ".." if len(result.name) > 28 else result.name

        if is_v3:
            sector = (result.sector or "N/A")[:16]
            if len(result.sector or "") > 18:
                sector = (result.sector or "")[:16] + ".."
            row = (
                f"{rank:<6} "
                f"{result.symbol:<8} "
                f"{name:<28} "
                f"{sector:<18} "
                f"{format_score(result.score):>8} "
                f"{format_score(result.value_score) if result.value_score is not None else 'N/A':>8} "
                f"{format_score(result.quality_score) if result.quality_score is not None else 'N/A':>8} "
                f"{format_score(result.momentum_score) if result.momentum_score is not None else 'N/A':>8} "
                f"{format_score(result.lowvol_score) if result.lowvol_score is not None else 'N/A':>8} "
                f"{result.status:<10}"
            )
        else:
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
    filtered = [r for r in results if r.status == "filtered"]

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
        if filtered:
            print(f"  Filtered Out:       {len(filtered)}")
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
        # Resolve scoring profile
        try:
            config = load_config()
        except FileNotFoundError:
            config = None

        if config is not None:
            profile_name, profile = resolve_profile(config)
        else:
            # No config file — use synthetic default
            from tokenomics.config import _SYNTHETIC_DEFAULT
            profile_name, profile = ("default", _SYNTHETIC_DEFAULT)

        print(f"Scoring Profile: {profile_name}")
        print(f"  Scorer:    {profile.scorer_class}")
        print(f"  Namespace: {profile.redis_namespace}")
        print()

        logger.info(
            "fundamentals_job.profile_resolved",
            profile=profile_name,
            scorer_class=profile.scorer_class,
            namespace=profile.redis_namespace,
        )

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

        limit = int(os.getenv("FUNDAMENTALS_LIMIT", "1000"))
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
        scorer = create_scorer(profile.scorer_class, **profile.scorer_kwargs)
        print("  Finnhub provider: OK")
        print(f"  Scorer: {profile.scorer_class} OK")

        print("Connecting to Redis...")
        store = FundamentalsStore(namespace=profile.redis_namespace)
        print(f"  Redis connection: OK (namespace={profile.redis_namespace})")
        print()

        logger.info("fundamentals_job.providers_initialized")

        # Step 1: Get symbols from universe in Redis
        universe = store.get_universe()

        if not universe or not universe.get("symbols"):
            print("ERROR: No stock universe found in Redis!")
            print("       Run the universe_job first to populate the universe.")
            print("       kubectl create job --from=cronjob/universe-refresh universe-manual -n tokenomics")
            logger.error("fundamentals_job.no_universe")
            return 1

        # Use universe from Redis (sorted by market cap)
        symbol_list = universe["symbols"][:limit]
        universe_age = store.get_universe_age_days()

        print(f"Using stock universe from Redis:")
        print(f"  Universe updated: {universe.get('updated_at')}")
        print(f"  Universe age: {universe_age:.1f} days" if universe_age else "  Universe age: unknown")
        print(f"  Total in universe: {universe.get('count')}")
        print(f"  Using top {len(symbol_list)} by market cap")

        # Apply exclusion list
        excluded_symbols = load_exclusion_list(profile.exclusion_list)
        if excluded_symbols:
            before_count = len(symbol_list)
            symbol_list = [s for s in symbol_list if s not in excluded_symbols]
            excluded_count = before_count - len(symbol_list)
            print(f"  Exclusion list: {len(excluded_symbols)} symbols configured, {excluded_count} removed")
            logger.info(
                "fundamentals_job.exclusions_applied",
                configured=len(excluded_symbols),
                removed=excluded_count,
                remaining=len(symbol_list),
            )
        else:
            print(f"  Exclusion list: none configured")

        print()

        # No descriptions available from universe - use symbol as name
        symbol_names = {s: s for s in symbol_list}

        # Load sector data for sector-neutral scoring
        all_sectors = store.get_sectors()
        sector_neutral = bool(all_sectors)
        if sector_neutral:
            print(f"  Sector data: {len(all_sectors)} symbols with sectors")
            # Count unique sectors
            unique_sectors = set(all_sectors.values())
            print(f"  Unique sectors: {len(unique_sectors)}")
        else:
            print(f"  Sector data: NOT AVAILABLE (run universe_job to populate)")
        print()

        logger.info(
            "fundamentals_job.using_universe",
            universe_count=universe.get("count"),
            using_count=len(symbol_list),
            age_days=universe_age,
        )

        if not symbol_list:
            print("ERROR: No symbols to process")
            logger.error("fundamentals_job.no_symbols")
            return 1

        # Snapshot old top N index before scoring begins
        top_n = config.rebalancing.top_n_stocks if config else 100
        old_top_n = store.get_top_scores(top_n)
        old_top_set = {symbol for symbol, _ in old_top_n}
        old_top_ranks = {symbol: rank for rank, (symbol, _) in enumerate(old_top_n, 1)}

        # Step 2: Fetch financials for each company
        # Rate limiting: 60 calls/minute = 1 call/second
        # Retry: up to 3 attempts with 2 second delay between retries
        # Cache: skip API call if data is < 7 days old
        rate_limit_delay = 1.0  # 1 second between calls (60/min)
        retry_delay = 2.0  # 2 seconds between retries
        max_retries = 3
        cache_max_age_days = 7

        print(f"Processing {len(symbol_list)} companies...")
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

        # Phase 1 — Fetch all financials (cache check + retry + rate limiting)
        fetched: list[tuple[str, BasicFinancials]] = []  # (company_name, financials)
        previous_scores: dict[str, float | None] = {}

        for i, symbol in enumerate(symbol_list):
            progress_pct = ((i + 1) / len(symbol_list)) * 100
            company_name = symbol_names.get(symbol, symbol)

            # Check cache first - skip API call if data is fresh
            cached = store.get_cached_result(symbol)
            if cached and cached.get("score_details"):
                cached_count += 1
                details = cached["score_details"]

                result = CompanyResult(
                    symbol=symbol,
                    name=company_name,
                    score=cached["score"],
                    roe=details.get("roe"),
                    debt_to_equity=details.get("debt_to_equity"),
                    revenue_growth=details.get("revenue_growth"),
                    eps_growth=details.get("eps_growth"),
                    status="cached",
                    value_score=details.get("value_score"),
                    quality_score=details.get("quality_score"),
                    momentum_score=details.get("momentum_score"),
                    lowvol_score=details.get("lowvol_score"),
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

            # Get previous score before updating
            previous_scores[symbol] = store.get_score(symbol)

            # Retry loop for each company
            financials = None
            last_error = None

            for attempt in range(max_retries):
                try:
                    # Fetch financials
                    financials = provider.get_basic_financials(symbol)
                    break  # Success, exit retry loop

                except NoFinancialsDataError as e:
                    # No data available for this symbol - don't retry
                    last_error = e
                    logger.debug(
                        "fundamentals_job.no_data",
                        symbol=symbol,
                        error=str(e),
                    )
                    break  # Exit retry loop - retrying won't help

                except FinancialsFetchError as e:
                    # API error - may be transient, retry
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
                fetched.append((company_name, financials))
            else:
                # Failed after all retries
                failed_count += 1
                results.append(
                    CompanyResult(
                        symbol=symbol,
                        name=company_name,
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
                # ETA based on remaining non-cached companies (estimate)
                remaining = len(symbol_list) - i - 1
                eta_minutes = remaining / 60 if len(fetched) > 0 else remaining
                print(
                    f"  [{progress_pct:5.1f}%] Fetched {i + 1}/{len(symbol_list)} - "
                    f"Cached: {cached_count}, Fetched: {len(fetched)}, Failed: {failed_count}, "
                    f"Retries: {retry_count} | ETA: {eta_minutes:.0f}min"
                )

            # Rate limiting: 1 request per second (60/min limit)
            time.sleep(rate_limit_delay)

        # Phase 2 — Batch score all fetched financials
        if fetched:
            financials_list = [f for _, f in fetched]
            scores = scorer.calculate_scores_batch(
                financials_list,
                sectors=all_sectors if sector_neutral else None,
            )

            for (name, financials), score in zip(fetched, scores):
                batch.append((financials, score))

                previous_score = previous_scores.get(financials.symbol)
                result = CompanyResult(
                    symbol=financials.symbol,
                    name=name,
                    score=score.composite_score,
                    roe=score.roe,
                    debt_to_equity=score.debt_to_equity,
                    revenue_growth=score.revenue_growth,
                    eps_growth=score.eps_growth,
                    status="success" if score.has_sufficient_data else "no_data",
                    previous_score=previous_score,
                    value_score=score.value_score,
                    quality_score=score.quality_score,
                    momentum_score=score.momentum_score,
                    lowvol_score=score.lowvol_score,
                    sector=all_sectors.get(financials.symbol),
                )
                results.append(result)

                if score.has_sufficient_data:
                    success_count += 1
                else:
                    no_data_count += 1

                logger.info(
                    "fundamentals_job.company_processed",
                    symbol=financials.symbol,
                    score=score.composite_score,
                    has_data=score.has_sufficient_data,
                )

        # Phase 2b — Apply post-scoring filters
        filters = profile.post_filters
        has_any_filter = (
            filters.min_quality is not None
            or (filters.speculative_lowvol is not None and filters.speculative_value is not None)
            or filters.deduplicate_share_classes
        )

        filtered_count = 0
        if has_any_filter and batch:
            # Build scores_by_symbol from both fresh scores and cached results
            # so deduplication can compare across cache boundaries
            all_scores_map: dict[str, FundamentalsScore] = {}
            for _, score in batch:
                all_scores_map[score.symbol] = score
            # Include cached results as FundamentalsScore-like objects for dedup
            for r in results:
                if r.status == "cached" and r.symbol not in all_scores_map:
                    all_scores_map[r.symbol] = FundamentalsScore(
                        symbol=r.symbol,
                        composite_score=r.score,
                        has_sufficient_data=True,
                        value_score=r.value_score,
                        quality_score=r.quality_score,
                        momentum_score=r.momentum_score,
                        lowvol_score=r.lowvol_score,
                    )

            before_count = len(batch)
            batch, removed_reasons = apply_post_filters(batch, all_scores_map, filters)
            filtered_count = before_count - len(batch)

            if removed_reasons:
                print(f"\n  Post-scoring filters removed {filtered_count} stocks:")
                for sym, reason in sorted(removed_reasons.items()):
                    print(f"    {sym}: {reason}")
                    # Update the result status
                    for r in results:
                        if r.symbol == sym and r.status in ("success", "no_data"):
                            r.status = "filtered"
                            break

                logger.info(
                    "fundamentals_job.post_filters_applied",
                    removed=filtered_count,
                    remaining=len(batch),
                    reasons={s: r for s, r in list(removed_reasons.items())[:20]},
                )
                print()

        # Phase 3 — Save to Redis in batches
        saved_so_far = 0
        while saved_so_far < len(batch):
            chunk = batch[saved_so_far : saved_so_far + batch_size]
            store.save_batch(chunk)
            logger.debug(
                "fundamentals_job.batch_saved",
                batch_size=len(chunk),
            )
            saved_so_far += len(chunk)

        if batch:
            logger.info(
                "fundamentals_job.all_batches_saved",
                total=len(batch),
            )

        print("-" * 60)
        print(f"Processing complete!")
        print()

        # Index change summary — compare old vs new top N
        new_top_n = store.get_top_scores(top_n)
        new_top_set = {symbol for symbol, _ in new_top_n}
        new_top_ranks = {symbol: rank for rank, (symbol, _) in enumerate(new_top_n, 1)}
        new_top_scores = {symbol: score for symbol, score in new_top_n}

        entered = new_top_set - old_top_set
        exited = old_top_set - new_top_set
        stayed = old_top_set & new_top_set

        if old_top_set:  # Only show if there was a previous index
            print(f"TOP {top_n} INDEX CHANGES")
            print("=" * 60)

            if entered:
                print(f"\n  ENTERED ({len(entered)}):")
                for symbol in sorted(entered, key=lambda s: new_top_ranks.get(s, 999)):
                    rank = new_top_ranks[symbol]
                    score = new_top_scores.get(symbol, 0)
                    print(f"    + {symbol:<8} rank #{rank:<4} score {score:.1f}")

            if exited:
                print(f"\n  EXITED ({len(exited)}):")
                # Get old scores for exited symbols
                old_top_scores = {symbol: score for symbol, score in old_top_n}
                for symbol in sorted(exited, key=lambda s: old_top_ranks.get(s, 999)):
                    old_rank = old_top_ranks[symbol]
                    old_score = old_top_scores.get(symbol, 0)
                    print(f"    - {symbol:<8} was rank #{old_rank:<4} score {old_score:.1f}")

            # Rank changes for symbols that stayed
            rank_changes = []
            for symbol in stayed:
                old_rank = old_top_ranks[symbol]
                new_rank = new_top_ranks[symbol]
                if old_rank != new_rank:
                    rank_changes.append((symbol, old_rank, new_rank))

            if rank_changes:
                rank_changes.sort(key=lambda x: x[1] - x[2], reverse=True)  # Biggest movers first
                print(f"\n  RANK CHANGES ({len(rank_changes)} moved):")
                for symbol, old_rank, new_rank in rank_changes[:20]:
                    direction = "up" if new_rank < old_rank else "down"
                    delta = abs(old_rank - new_rank)
                    score = new_top_scores.get(symbol, 0)
                    print(f"    {symbol:<8} #{old_rank} -> #{new_rank} ({direction} {delta}) score {score:.1f}")
                if len(rank_changes) > 20:
                    print(f"    ... and {len(rank_changes) - 20} more")

            if not entered and not exited:
                print(f"\n  No changes to the top {top_n} index.")

            print("=" * 60)
            print()

            logger.info(
                "fundamentals_job.index_changes",
                top_n=top_n,
                entered=sorted(entered),
                exited=sorted(exited),
                entered_count=len(entered),
                exited_count=len(exited),
                rank_changes_count=len(rank_changes),
            )
        else:
            print(f"  (No previous top {top_n} index — first run)")
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
            total_symbols=len(symbol_list),
            cached=cached_count,
            success=success_count,
            failed=failed_count,
            no_data=no_data_count,
            filtered=filtered_count,
            retries=retry_count,
            total_in_store=total_in_store,
            top_10=[(s, round(score, 1)) for s, score in top_companies],
        )

        # Print the summary table
        print_summary_table(results, scorer_kwargs=profile.scorer_kwargs, sector_neutral=sector_neutral)

        # Show what was updated in this run
        updated_results = [r for r in results if r.status == "success"]
        is_v3 = any(r.value_score is not None for r in results)

        if updated_results:
            # Sort by score descending
            updated_results.sort(key=lambda x: x.score, reverse=True)
            print()
            if is_v3:
                print("=" * 102)
                print(f"UPDATED THIS RUN: {len(updated_results)} companies")
                print("=" * 102)
                print(f"{'Symbol':<8} {'Sector':<18} {'Score':>8} {'Prev':>8} {'Change':>8} {'Value':>8} {'Quality':>8} {'Momntm':>8} {'LowVol':>8}")
                print("-" * 102)
                for r in updated_results[:30]:  # Show top 30
                    prev_str = f"{r.previous_score:.1f}" if r.previous_score is not None else "NEW"
                    if r.previous_score is not None:
                        change = r.score - r.previous_score
                        change_str = f"{change:+.1f}"
                    else:
                        change_str = "-"
                    sector = (r.sector or "N/A")[:16]
                    val_str = f"{r.value_score:.1f}" if r.value_score is not None else "N/A"
                    qual_str = f"{r.quality_score:.1f}" if r.quality_score is not None else "N/A"
                    mom_str = f"{r.momentum_score:.1f}" if r.momentum_score is not None else "N/A"
                    lvol_str = f"{r.lowvol_score:.1f}" if r.lowvol_score is not None else "N/A"
                    print(f"{r.symbol:<8} {sector:<18} {r.score:>8.1f} {prev_str:>8} {change_str:>8} {val_str:>8} {qual_str:>8} {mom_str:>8} {lvol_str:>8}")
            else:
                print("=" * 70)
                print(f"UPDATED THIS RUN: {len(updated_results)} companies")
                print("=" * 70)
                print(f"{'Symbol':<8} {'Score':>8} {'Prev':>8} {'Change':>8} {'ROE':>10} {'D/E':>10}")
                print("-" * 70)
                for r in updated_results[:30]:  # Show top 30
                    roe_str = f"{r.roe:.1f}%" if r.roe is not None else "N/A"
                    de_str = f"{r.debt_to_equity:.2f}" if r.debt_to_equity is not None else "N/A"
                    prev_str = f"{r.previous_score:.1f}" if r.previous_score is not None else "NEW"
                    if r.previous_score is not None:
                        change = r.score - r.previous_score
                        change_str = f"{change:+.1f}"
                    else:
                        change_str = "-"
                    print(f"{r.symbol:<8} {r.score:>8.1f} {prev_str:>8} {change_str:>8} {roe_str:>10} {de_str:>10}")
            if len(updated_results) > 30:
                print(f"... and {len(updated_results) - 30} more")
            print()

        # Final job summary
        print("JOB SUMMARY")
        print("=" * 60)
        print(f"  Start Time:         {start_time.isoformat()}")
        print(f"  End Time:           {end_time.isoformat()}")
        print(f"  Duration:           {duration_minutes:.1f} minutes ({duration:.0f} seconds)")
        print(f"  Companies in Redis: {total_in_store}")
        print()
        print(f"  THIS RUN:")
        print(f"    Updated (fresh):  {success_count}")
        print(f"    Skipped (cached): {cached_count}")
        print(f"    No data:          {no_data_count}")
        print(f"    Failed:           {failed_count}")
        if filtered_count:
            print(f"    Filtered out:     {filtered_count}")
        print()
        print("Job completed successfully!")
        print("=" * 60)

        # VIX Guard: optionally spawn an emergency rebalance Job
        if (
            profile.regime_config is not None
            and profile.regime_config.vix_guard is not None
            and profile.regime_config.vix_guard.enabled
        ):
            print()
            print("Checking VIX guard...")
            from tokenomics.risk.vix_guard import VixGuard
            guard = VixGuard(profile_name=profile_name, config=profile.regime_config.vix_guard)
            should_trigger, reason = guard.check()
            guard.close()
            print(f"  Result: {reason}")

            if should_trigger:
                print(f"  *** EMERGENCY REBALANCE TRIGGERED: {reason} ***")
                logger.warning(
                    "fundamentals_job.emergency_rebalance_triggered",
                    profile=profile_name,
                    reason=reason,
                )
                try:
                    from tokenomics.risk.k8s_trigger import trigger_emergency_rebalance
                    k8s_ns = os.getenv("K8S_NAMESPACE", "tokenomics")
                    job_name = trigger_emergency_rebalance(profile_name, reason, namespace=k8s_ns)
                    print(f"  Created emergency Job: {job_name}")
                    logger.info(
                        "fundamentals_job.emergency_job_created",
                        job=job_name,
                        namespace=k8s_ns,
                    )
                except Exception as e:
                    # Non-fatal: log the failure but don't abort the refresh job
                    print(f"  WARNING: Failed to create emergency Job: {e}")
                    logger.error("fundamentals_job.emergency_job_failed", error=str(e))
            else:
                print(f"  No emergency rebalance needed.")
            print()

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
