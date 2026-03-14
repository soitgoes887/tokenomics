"""Risk regime model and Redis store."""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import redis
import structlog

logger = structlog.get_logger(__name__)


class RiskRegime(str, Enum):
    """Geopolitical / market stress classification."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class RegimeSnapshot:
    """Point-in-time regime reading."""

    date: str           # ISO date, e.g. "2026-03-14"
    vix: float          # Raw VIX value (-1.0 if unavailable)
    sentiment: float    # Avg market sentiment [-1, +1]
    cgrs: float         # CGRS-lite composite score [0, 100]
    regime: RiskRegime
    updated_at: str     # ISO datetime


class RegimeStore:
    """Redis-backed storage for the daily risk regime snapshot."""

    # 3-day TTL; daily job provides ample margin
    TTL_SECONDS = 3 * 24 * 60 * 60

    def __init__(self, namespace: str = "risk:regime"):
        self._key = f"{namespace}:current"
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

    def save(self, snapshot: RegimeSnapshot) -> None:
        data = {
            "date": snapshot.date,
            "vix": str(snapshot.vix),
            "sentiment": str(snapshot.sentiment),
            "cgrs": str(snapshot.cgrs),
            "regime": snapshot.regime.value,
            "updated_at": snapshot.updated_at,
        }
        pipe = self._client.pipeline()
        pipe.delete(self._key)
        pipe.hset(self._key, mapping=data)
        pipe.expire(self._key, self.TTL_SECONDS)
        pipe.execute()
        logger.info(
            "regime_store.saved",
            regime=snapshot.regime.value,
            cgrs=snapshot.cgrs,
            vix=snapshot.vix,
        )

    def load(self) -> Optional[RegimeSnapshot]:
        data = self._client.hgetall(self._key)
        if not data:
            return None
        try:
            return RegimeSnapshot(
                date=data["date"],
                vix=float(data["vix"]),
                sentiment=float(data["sentiment"]),
                cgrs=float(data["cgrs"]),
                regime=RiskRegime(data["regime"]),
                updated_at=data["updated_at"],
            )
        except (KeyError, ValueError) as e:
            logger.warning("regime_store.load_error", error=str(e))
            return None

    def is_stale(self, max_age_hours: int = 30) -> bool:
        """Return True if regime data is older than max_age_hours or missing."""
        updated_at = self._client.hget(self._key, "updated_at")
        if not updated_at:
            return True
        try:
            dt = datetime.fromisoformat(updated_at)
            return (datetime.now(timezone.utc) - dt) > timedelta(hours=max_age_hours)
        except (ValueError, TypeError):
            return True

    def close(self) -> None:
        self._client.close()
