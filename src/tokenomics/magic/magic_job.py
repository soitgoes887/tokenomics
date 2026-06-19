"""Magic Formula holdings loader (CronJob entry point).

Reads the active profile's fixed holdings list and writes it to Redis as a
uniform-score set, so the standard rebalancer holds it equal-weight. Designed to
run monthly, just before the profile's rebalancer CronJob.

Usage:
    SCORING_PROFILE=tokenomics_v5_magic_100m \
        PYTHONPATH=src python -m tokenomics.magic.magic_job

Environment variables:
    SCORING_PROFILE: profile to load (must have a `holdings_list` configured)
    REDIS_HOST / REDIS_PORT / REDIS_PASSWORD: Redis connection
"""

import logging
import sys
from datetime import datetime, timezone

import structlog

from tokenomics.config import load_config, resolve_profile
from tokenomics.fundamentals.store import FundamentalsStore
from tokenomics.magic.holdings import load_holdings


def _setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.getLogger().setLevel(logging.INFO)


_setup_logging()
logger = structlog.get_logger(__name__)


def main() -> int:
    start_time = datetime.now(timezone.utc)
    print("=" * 80)
    print("TOKENOMICS MAGIC FORMULA LOADER")
    print("=" * 80)
    print(f"Start Time: {start_time.isoformat()}")
    print()

    try:
        config = load_config()
        profile_name, profile = resolve_profile(config)

        print(f"Profile:    {profile_name}")
        print(f"Namespace:  {profile.redis_namespace}")

        if not profile.holdings_list:
            print("ERROR: This profile has no `holdings_list` configured.")
            print("       The Magic Formula loader only runs for fixed-list profiles.")
            logger.error("magic_loader.no_holdings_list", profile=profile_name)
            return 1

        print(f"Holdings:   {profile.holdings_list}")
        print()

        symbols = load_holdings(profile.holdings_list)
        if not symbols:
            print("ERROR: Holdings list is empty.")
            logger.error("magic_loader.empty_list", profile=profile_name)
            return 1

        store = FundamentalsStore(namespace=profile.redis_namespace)

        # Snapshot previous holdings to report the diff
        previous = {sym for sym, _ in store.get_top_scores(limit=500)}
        new_set = set(symbols)
        entered = sorted(new_set - previous)
        exited = sorted(previous - new_set)

        count = store.replace_scores(symbols, score=100.0)

        print(f"Loaded {count} symbols at equal weight ({100.0 / count:.2f}% each):")
        print("  " + ", ".join(symbols))
        print()
        if previous:
            print(f"  Entered ({len(entered)}): {', '.join(entered) or '—'}")
            print(f"  Exited  ({len(exited)}): {', '.join(exited) or '—'}")
        else:
            print("  (first load — no previous holdings)")
        print()

        logger.info(
            "magic_loader.completed",
            profile=profile_name,
            namespace=profile.redis_namespace,
            count=count,
            entered=entered,
            exited=exited,
        )

        store.close()
        print("Done. Run the rebalancer to trade into these holdings.")
        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        logger.error("magic_loader.fatal_error", error=str(e), exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
