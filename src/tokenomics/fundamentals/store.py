"""Redis storage for fundamentals data and scores."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
import structlog

from tokenomics.fundamentals.scorer import FundamentalsScore
from tokenomics.models import BasicFinancials

logger = structlog.get_logger(__name__)


class FundamentalsStore:
    """Redis-backed storage for company fundamentals and scores.

    Storage structure:
    - Hash per company: `fundamentals:{symbol}` containing:
        - raw_metrics: JSON blob of BasicFinancials
        - score: composite score float
        - score_details: JSON blob of FundamentalsScore
        - updated: ISO timestamp
    - Sorted set: `fundamentals:scores` mapping symbol -> score for leaderboard
    - Hash: `fundamentals:universe` containing:
        - symbols: JSON list of top symbols by market cap
        - updated_at: ISO timestamp
        - count: number of symbols
    - Sorted set: `fundamentals:universe:marketcap` mapping symbol -> market cap
    """

    # Key patterns
    KEY_PREFIX = "fundamentals"
    SCORES_KEY = "fundamentals:scores"
    UNIVERSE_KEY = "fundamentals:universe"
    UNIVERSE_MARKETCAP_KEY = "fundamentals:universe:marketcap"

    # TTL: 14 days (cronjob runs weekly, so 2x for safety)
    TTL_SECONDS = 14 * 24 * 60 * 60

    # Cache freshness: 7 days
    CACHE_FRESHNESS_DAYS = 7

    def __init__(self, namespace: str | None = None):
        """Initialize Redis connection from environment variables.

        Args:
            namespace: Optional Redis key namespace. If provided, overrides
                       KEY_PREFIX and SCORES_KEY. Universe keys are always shared.
        """
        if namespace is not None:
            self.KEY_PREFIX = namespace
            self.SCORES_KEY = f"{namespace}:scores"
        else:
            self.KEY_PREFIX = "fundamentals"
            self.SCORES_KEY = "fundamentals:scores"

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD")

        self._client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=30,
        )

        logger.info(
            "fundamentals_store.initialized",
            host=redis_host,
            port=redis_port,
        )

    def is_fresh(self, symbol: str, max_age_days: int = None) -> bool:
        """Check if cached data for a symbol is fresh (less than max_age_days old).

        Args:
            symbol: Stock ticker symbol
            max_age_days: Maximum age in days (default: CACHE_FRESHNESS_DAYS)

        Returns:
            True if data exists and is fresh, False otherwise
        """
        if max_age_days is None:
            max_age_days = self.CACHE_FRESHNESS_DAYS

        key = f"{self.KEY_PREFIX}:{symbol}"
        updated = self._client.hget(key, "updated")

        if not updated:
            return False

        try:
            updated_dt = datetime.fromisoformat(updated)
            age = datetime.now(timezone.utc) - updated_dt
            return age < timedelta(days=max_age_days)
        except (ValueError, TypeError):
            return False

    def get_cached_result(self, symbol: str) -> Optional[dict]:
        """Get cached score details for a symbol if fresh.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Dict with score_details if fresh, None otherwise
        """
        key = f"{self.KEY_PREFIX}:{symbol}"
        data = self._client.hmget(key, "updated", "score", "score_details")

        if not data[0]:  # No updated timestamp
            return None

        try:
            updated_dt = datetime.fromisoformat(data[0])
            age = datetime.now(timezone.utc) - updated_dt

            if age >= timedelta(days=self.CACHE_FRESHNESS_DAYS):
                return None  # Data is stale

            result = {
                "updated": data[0],
                "score": float(data[1]) if data[1] else 0.0,
                "age_days": age.days,
            }

            if data[2]:
                result["score_details"] = json.loads(data[2])

            return result

        except (ValueError, TypeError):
            return None

    def save_company(
        self,
        financials: BasicFinancials,
        score: FundamentalsScore,
    ) -> None:
        """Save a company's fundamentals and score to Redis.

        Args:
            financials: BasicFinancials object with raw metrics
            score: FundamentalsScore object with calculated scores
        """
        key = f"{self.KEY_PREFIX}:{financials.symbol}"
        now = datetime.now(timezone.utc).isoformat()

        # Prepare hash fields
        data = {
            "symbol": financials.symbol,
            "raw_metrics": financials.model_dump_json(),
            "score": str(score.composite_score),
            "score_details": json.dumps({
                "composite_score": score.composite_score,
                "roe_score": score.roe_score,
                "debt_score": score.debt_score,
                "growth_score": score.growth_score,
                "roe": score.roe,
                "debt_to_equity": score.debt_to_equity,
                "revenue_growth": score.revenue_growth,
                "eps_growth": score.eps_growth,
                "has_sufficient_data": score.has_sufficient_data,
            }),
            "updated": now,
        }

        # Use pipeline for atomic update
        pipeline = self._client.pipeline()
        pipeline.delete(key)
        pipeline.hset(key, mapping=data)
        pipeline.expire(key, self.TTL_SECONDS)
        pipeline.zadd(self.SCORES_KEY, {financials.symbol: score.composite_score})
        pipeline.execute()

        logger.debug(
            "fundamentals_store.saved",
            symbol=financials.symbol,
            score=score.composite_score,
        )

    def save_batch(
        self,
        items: list[tuple[BasicFinancials, FundamentalsScore]],
    ) -> int:
        """Save multiple companies in a batch operation.

        Args:
            items: List of (BasicFinancials, FundamentalsScore) tuples

        Returns:
            Number of companies saved
        """
        if not items:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        pipeline = self._client.pipeline()
        saved = 0

        for financials, score in items:
            key = f"{self.KEY_PREFIX}:{financials.symbol}"

            data = {
                "symbol": financials.symbol,
                "raw_metrics": financials.model_dump_json(),
                "score": str(score.composite_score),
                "score_details": json.dumps({
                    "composite_score": score.composite_score,
                    "roe_score": score.roe_score,
                    "debt_score": score.debt_score,
                    "growth_score": score.growth_score,
                    "roe": score.roe,
                    "debt_to_equity": score.debt_to_equity,
                    "revenue_growth": score.revenue_growth,
                    "eps_growth": score.eps_growth,
                    "has_sufficient_data": score.has_sufficient_data,
                }),
                "updated": now,
            }

            pipeline.delete(key)
            pipeline.hset(key, mapping=data)
            pipeline.expire(key, self.TTL_SECONDS)
            pipeline.zadd(self.SCORES_KEY, {financials.symbol: score.composite_score})
            saved += 1

        # Set TTL on the scores sorted set too
        pipeline.expire(self.SCORES_KEY, self.TTL_SECONDS)
        pipeline.execute()

        logger.info(
            "fundamentals_store.batch_saved",
            count=saved,
        )

        return saved

    def get_company(self, symbol: str) -> Optional[dict]:
        """Get a company's fundamentals and score from Redis.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Dict with raw_metrics, score, score_details, updated fields
            or None if not found
        """
        key = f"{self.KEY_PREFIX}:{symbol}"
        data = self._client.hgetall(key)

        if not data:
            return None

        # Parse JSON fields
        result = {
            "symbol": data.get("symbol"),
            "score": float(data.get("score", 0)),
            "updated": data.get("updated"),
        }

        if data.get("raw_metrics"):
            result["raw_metrics"] = json.loads(data["raw_metrics"])

        if data.get("score_details"):
            result["score_details"] = json.loads(data["score_details"])

        return result

    def get_score(self, symbol: str) -> Optional[float]:
        """Get just the composite score for a symbol.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Composite score float or None if not found
        """
        score = self._client.zscore(self.SCORES_KEY, symbol)
        return score

    def get_top_scores(self, limit: int = 100) -> list[tuple[str, float]]:
        """Get top N companies by composite score.

        Args:
            limit: Number of top companies to return

        Returns:
            List of (symbol, score) tuples sorted descending by score
        """
        results = self._client.zrevrange(
            self.SCORES_KEY,
            0,
            limit - 1,
            withscores=True,
        )
        return [(symbol, score) for symbol, score in results]

    def get_bottom_scores(self, limit: int = 100) -> list[tuple[str, float]]:
        """Get bottom N companies by composite score.

        Args:
            limit: Number of bottom companies to return

        Returns:
            List of (symbol, score) tuples sorted ascending by score
        """
        results = self._client.zrange(
            self.SCORES_KEY,
            0,
            limit - 1,
            withscores=True,
        )
        return [(symbol, score) for symbol, score in results]

    def get_scores_above_threshold(self, threshold: float) -> list[tuple[str, float]]:
        """Get all companies with score above threshold.

        Args:
            threshold: Minimum score (0-100)

        Returns:
            List of (symbol, score) tuples for companies above threshold
        """
        results = self._client.zrangebyscore(
            self.SCORES_KEY,
            threshold,
            100,
            withscores=True,
        )
        return [(symbol, score) for symbol, score in results]

    def get_total_count(self) -> int:
        """Get total number of companies in the store."""
        return self._client.zcard(self.SCORES_KEY)

    def delete_company(self, symbol: str) -> bool:
        """Delete a company from the store.

        Args:
            symbol: Stock ticker symbol

        Returns:
            True if deleted, False if not found
        """
        key = f"{self.KEY_PREFIX}:{symbol}"
        pipeline = self._client.pipeline()
        pipeline.delete(key)
        pipeline.zrem(self.SCORES_KEY, symbol)
        results = pipeline.execute()
        return results[0] > 0

    def clear_all(self) -> int:
        """Clear all fundamentals data. Use with caution.

        Returns:
            Number of keys deleted
        """
        # Find all fundamentals keys
        keys = list(self._client.scan_iter(f"{self.KEY_PREFIX}:*"))
        if self.SCORES_KEY not in keys:
            keys.append(self.SCORES_KEY)

        if keys:
            deleted = self._client.delete(*keys)
            logger.warning("fundamentals_store.cleared_all", deleted=deleted)
            return deleted
        return 0

    def close(self) -> None:
        """Close Redis connection."""
        self._client.close()
        logger.debug("fundamentals_store.closed")

    # Universe methods (for monthly market cap ranking job)

    def save_universe(
        self,
        symbols_with_marketcap: list[tuple[str, float]],
    ) -> None:
        """Save the stock universe (top companies by market cap).

        Args:
            symbols_with_marketcap: List of (symbol, market_cap) tuples,
                                    already sorted by market cap descending
        """
        now = datetime.now(timezone.utc).isoformat()
        symbols = [s for s, _ in symbols_with_marketcap]

        pipeline = self._client.pipeline()

        # Save universe metadata
        pipeline.delete(self.UNIVERSE_KEY)
        pipeline.hset(self.UNIVERSE_KEY, mapping={
            "symbols": json.dumps(symbols),
            "updated_at": now,
            "count": str(len(symbols)),
        })
        # Universe TTL: 45 days (job runs monthly, 1.5x for safety)
        pipeline.expire(self.UNIVERSE_KEY, 45 * 24 * 60 * 60)

        # Save market cap sorted set for lookups
        pipeline.delete(self.UNIVERSE_MARKETCAP_KEY)
        if symbols_with_marketcap:
            marketcap_dict = {symbol: mcap for symbol, mcap in symbols_with_marketcap}
            pipeline.zadd(self.UNIVERSE_MARKETCAP_KEY, marketcap_dict)
            pipeline.expire(self.UNIVERSE_MARKETCAP_KEY, 45 * 24 * 60 * 60)

        pipeline.execute()

        logger.info(
            "fundamentals_store.universe_saved",
            count=len(symbols),
            updated_at=now,
        )

    def get_universe(self) -> Optional[dict]:
        """Get the stock universe metadata and symbols.

        Returns:
            Dict with 'symbols' (list), 'updated_at' (ISO string), 'count' (int)
            or None if not found
        """
        data = self._client.hgetall(self.UNIVERSE_KEY)

        if not data:
            return None

        return {
            "symbols": json.loads(data.get("symbols", "[]")),
            "updated_at": data.get("updated_at"),
            "count": int(data.get("count", 0)),
        }

    def get_universe_symbols(self) -> list[str]:
        """Get just the list of symbols from the universe.

        Returns:
            List of symbols or empty list if universe not set
        """
        symbols_json = self._client.hget(self.UNIVERSE_KEY, "symbols")
        if not symbols_json:
            return []
        return json.loads(symbols_json)

    def get_universe_age_days(self) -> Optional[float]:
        """Get the age of the universe in days.

        Returns:
            Age in days or None if universe not set
        """
        updated_at = self._client.hget(self.UNIVERSE_KEY, "updated_at")
        if not updated_at:
            return None

        try:
            updated_dt = datetime.fromisoformat(updated_at)
            age = datetime.now(timezone.utc) - updated_dt
            return age.total_seconds() / (24 * 60 * 60)
        except (ValueError, TypeError):
            return None

    def get_market_cap(self, symbol: str) -> Optional[float]:
        """Get the market cap for a symbol from the universe.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Market cap in millions or None if not found
        """
        return self._client.zscore(self.UNIVERSE_MARKETCAP_KEY, symbol)

    def get_top_by_market_cap(self, limit: int = 100) -> list[tuple[str, float]]:
        """Get top N companies by market cap from the universe.

        Args:
            limit: Number of top companies to return

        Returns:
            List of (symbol, market_cap) tuples sorted descending by market cap
        """
        results = self._client.zrevrange(
            self.UNIVERSE_MARKETCAP_KEY,
            0,
            limit - 1,
            withscores=True,
        )
        return [(symbol, mcap) for symbol, mcap in results]
