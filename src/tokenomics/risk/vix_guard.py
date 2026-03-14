"""VIX-based emergency rebalance trigger guard.

Checks two conditions against current VIX data:
  1. Panic override:  VIX >= panic_threshold            (fires unconditionally)
  2. Elevated + spike: VIX >= elevated_threshold
                       AND VIX spiked >= spike_points in spike_days days

Subject to a per-profile cooldown (default 15 days) stored in Redis so
consecutive weekly reindex runs don't hammer the same account.

The VIX history is fetched live from yfinance at check time — independent
of the daily regime-job schedule.
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import redis
import structlog
import yfinance as yf

from tokenomics.config import VixGuardConfig

logger = structlog.get_logger(__name__)

_COOLDOWN_KEY = "risk:vix_guard:{profile}:last_trigger"


class VixGuard:
    """Evaluates VIX conditions and manages the per-profile emergency cooldown."""

    def __init__(self, profile_name: str, config: VixGuardConfig):
        self._profile = profile_name
        self._cfg = config
        self._redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=30,
        )

    # --- Cooldown helpers ---

    def _cooldown_key(self) -> str:
        return _COOLDOWN_KEY.format(profile=self._profile)

    def _is_on_cooldown(self) -> bool:
        last = self._redis.get(self._cooldown_key())
        if not last:
            return False
        try:
            dt = datetime.fromisoformat(last)
            return (datetime.now(timezone.utc) - dt) < timedelta(days=self._cfg.cooldown_days)
        except (ValueError, TypeError):
            return False

    def _set_cooldown(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # TTL = 2× cooldown days as a safety margin
        ttl_s = int(self._cfg.cooldown_days * 24 * 3600 * 2)
        self._redis.set(self._cooldown_key(), now, ex=ttl_s)

    # --- VIX data ---

    def _fetch_vix(self) -> list[float]:
        """Return recent daily VIX closes (oldest first).

        Fetches enough history to measure the spike window.
        Returns empty list on any error.
        """
        needed = self._cfg.vix_spike_days + 3  # a few extra for weekends / gaps
        try:
            hist = yf.download(
                "^VIX",
                period=f"{needed + 5}d",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
            if hist.empty:
                return []
            # yfinance ≥0.2.54 returns MultiIndex columns even for single tickers
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            closes = [float(v) for v in hist["Close"].dropna().tolist()]
            return closes[-(needed):] if len(closes) >= needed else closes
        except Exception as e:
            logger.warning("vix_guard.fetch_error", error=str(e))
            return []

    # --- Public API ---

    def check(self) -> tuple[bool, str]:
        """Return (should_trigger, reason).

        Sets the cooldown if a trigger fires.  Safe to call even when disabled.
        """
        if not self._cfg.enabled:
            return False, "disabled"

        if self._is_on_cooldown():
            logger.info(
                "vix_guard.cooldown_active",
                profile=self._profile,
                cooldown_days=self._cfg.cooldown_days,
            )
            return False, f"cooldown active ({self._cfg.cooldown_days}d)"

        closes = self._fetch_vix()
        if not closes:
            logger.warning("vix_guard.no_data")
            return False, "VIX data unavailable"

        current = closes[-1]
        # Compare against `vix_spike_days` sessions ago
        lookback_idx = -(self._cfg.vix_spike_days + 1)
        prior = closes[lookback_idx] if abs(lookback_idx) <= len(closes) else closes[0]
        spike = current - prior

        logger.info(
            "vix_guard.check",
            profile=self._profile,
            vix=round(current, 2),
            prior_vix=round(prior, 2),
            spike=round(spike, 2),
            spike_days=self._cfg.vix_spike_days,
        )

        # Condition 1: absolute panic
        if current >= self._cfg.vix_panic_threshold:
            reason = (
                f"VIX {current:.1f} >= panic threshold {self._cfg.vix_panic_threshold:.0f}"
            )
            logger.warning("vix_guard.trigger_panic", profile=self._profile, vix=current)
            self._set_cooldown()
            return True, reason

        # Condition 2: elevated baseline + spike
        if (current >= self._cfg.vix_elevated_threshold
                and spike >= self._cfg.vix_spike_points):
            reason = (
                f"VIX {current:.1f} >= {self._cfg.vix_elevated_threshold:.0f}"
                f" AND +{spike:.1f}pt spike over {self._cfg.vix_spike_days}d"
            )
            logger.warning("vix_guard.trigger_spike", profile=self._profile, vix=current, spike=spike)
            self._set_cooldown()
            return True, reason

        logger.info(
            "vix_guard.no_trigger",
            profile=self._profile,
            vix=round(current, 2),
            spike=round(spike, 2),
        )
        return False, f"VIX {current:.1f} — conditions not met"

    def close(self) -> None:
        self._redis.close()
