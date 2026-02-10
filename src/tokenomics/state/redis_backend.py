"""Redis-based state management backend."""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import redis
import structlog

logger = structlog.get_logger(__name__)


class RedisStateBackend:
    """Redis-backed state persistence for tokenomics."""

    def __init__(self, profile_id: str):
        """
        Initialize Redis connection.

        Args:
            profile_id: Unique identifier for this profile (e.g., "alpaca-gemini-flash-alpaca-paper")
        """
        self._profile_id = profile_id
        self._state_key = f"tokenomics:state:{profile_id}"

        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_password = os.getenv("REDIS_PASSWORD")

        self._client = redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

        logger.info(
            "redis.backend_initialized",
            profile_id=profile_id,
            host=redis_host,
            port=redis_port,
            state_key=self._state_key,
        )

    def save_state(
        self,
        positions: dict,
        risk: dict,
        seen_article_ids: list[str],
    ) -> None:
        """
        Persist all state to Redis atomically.

        Args:
            positions: Position manager state dict
            risk: Risk manager state dict
            seen_article_ids: List of seen article IDs
        """
        state = {
            "version": 1,
            "last_saved": datetime.now(timezone.utc).isoformat(),
            "positions": positions,
            "risk": risk,
            "seen_article_ids": seen_article_ids,
        }

        try:
            # Serialize to JSON
            state_json = json.dumps(state, default=str)

            # Save to Redis with 30-day TTL (prevents stale state accumulation)
            self._client.setex(
                self._state_key,
                30 * 24 * 60 * 60,  # 30 days in seconds
                state_json,
            )

            logger.debug(
                "redis.state_saved",
                profile_id=self._profile_id,
                positions_count=len(positions.get("positions", {})),
            )

        except Exception as e:
            logger.error(
                "redis.save_failed",
                profile_id=self._profile_id,
                error=str(e),
                exc_info=True,
            )
            raise

    def load_state(self) -> Optional[dict]:
        """
        Load state from Redis.

        Returns:
            State dict if found, None otherwise
        """
        try:
            state_json = self._client.get(self._state_key)

            if state_json is None:
                logger.info(
                    "redis.no_state_found",
                    profile_id=self._profile_id,
                    state_key=self._state_key,
                )
                return None

            state = json.loads(state_json)

            logger.info(
                "redis.state_loaded",
                profile_id=self._profile_id,
                last_saved=state.get("last_saved"),
                positions_count=len(state.get("positions", {}).get("positions", {})),
            )

            return state

        except json.JSONDecodeError as e:
            logger.error(
                "redis.state_parse_failed",
                profile_id=self._profile_id,
                error=str(e),
            )
            return None
        except Exception as e:
            logger.error(
                "redis.load_failed",
                profile_id=self._profile_id,
                error=str(e),
                exc_info=True,
            )
            raise

    def is_symbol_held_by_any_profile(self, symbol: str) -> bool:
        """
        Check if any profile currently holds an open position in this symbol.

        This prevents multiple profiles from opening duplicate positions.

        Args:
            symbol: Stock/crypto symbol to check

        Returns:
            True if any profile has an open position in this symbol
        """
        try:
            # Scan for all tokenomics state keys
            pattern = "tokenomics:state:*"
            cursor = 0
            all_keys = []

            # Use SCAN for safe iteration over large keyspaces
            while True:
                cursor, keys = self._client.scan(cursor, match=pattern, count=100)
                all_keys.extend(keys)
                if cursor == 0:
                    break

            # Check each profile's state for the symbol
            for key in all_keys:
                # Skip our own state
                if key == self._state_key:
                    continue

                try:
                    state_json = self._client.get(key)
                    if state_json is None:
                        continue

                    state = json.loads(state_json)
                    positions = state.get("positions", {}).get("positions", {})

                    if symbol in positions:
                        status = positions[symbol].get("status", "open")
                        if status == "open":
                            logger.debug(
                                "redis.symbol_held_by_other",
                                symbol=symbol,
                                other_profile=key.replace("tokenomics:state:", ""),
                            )
                            return True

                except (json.JSONDecodeError, redis.RedisError):
                    # Skip profiles with corrupt/inaccessible state
                    continue

            return False

        except Exception as e:
            logger.error(
                "redis.duplicate_check_failed",
                symbol=symbol,
                error=str(e),
                exc_info=True,
            )
            # Fail-safe: assume symbol might be held (prevents duplicate)
            return True

    def close(self) -> None:
        """Close Redis connection."""
        try:
            self._client.close()
            logger.debug("redis.connection_closed", profile_id=self._profile_id)
        except Exception as e:
            logger.warning("redis.close_failed", error=str(e))
