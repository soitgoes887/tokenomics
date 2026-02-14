"""Redis storage for fundamentals data and scores."""

import json
import os
from datetime import datetime, timezone
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
    """

    # Key patterns
    KEY_PREFIX = "fundamentals"
    SCORES_KEY = "fundamentals:scores"

    # TTL: 14 days (cronjob runs weekly, so 2x for safety)
    TTL_SECONDS = 14 * 24 * 60 * 60

    def __init__(self):
        """Initialize Redis connection from environment variables."""
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
