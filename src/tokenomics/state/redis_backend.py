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

    def __init__(self, profile_id: str, broker: str):
        """
        Initialize Redis connection.

        Args:
            profile_id: Unique identifier for this profile (e.g., "alpaca-gemini-flash-alpaca-paper")
            broker: Broker identifier (e.g., "alpaca-paper") - used for shared state key
        """
        self._profile_id = profile_id
        self._broker = broker

        # SHARED state key - all profiles using same broker share this
        self._shared_state_key = f"tokenomics:state:{broker}"

        # PROFILE-SPECIFIC key for seen article IDs
        self._profile_state_key = f"tokenomics:profile:{profile_id}:seen"

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
            broker=broker,
            host=redis_host,
            port=redis_port,
            shared_state_key=self._shared_state_key,
            profile_state_key=self._profile_state_key,
        )

    def save_state(
        self,
        positions: dict,
        risk: dict,
        seen_article_ids: list[str],
    ) -> None:
        """
        Persist state to Redis.

        Positions and risk are SHARED across all profiles using the same broker.
        Seen article IDs are PROFILE-SPECIFIC.

        Args:
            positions: Position manager state dict (SHARED)
            risk: Risk manager state dict (SHARED)
            seen_article_ids: List of seen article IDs (PROFILE-SPECIFIC)
        """
        try:
            # Save SHARED state (positions + risk)
            shared_state = {
                "version": 1,
                "last_saved": datetime.now(timezone.utc).isoformat(),
                "last_saved_by": self._profile_id,
                "positions": positions,
                "risk": risk,
            }

            shared_json = json.dumps(shared_state, default=str)

            # Save with 30-day TTL
            self._client.setex(
                self._shared_state_key,
                30 * 24 * 60 * 60,
                shared_json,
            )

            # Save PROFILE-SPECIFIC seen articles
            profile_state = {
                "seen_article_ids": seen_article_ids,
                "last_saved": datetime.now(timezone.utc).isoformat(),
            }

            profile_json = json.dumps(profile_state, default=str)

            self._client.setex(
                self._profile_state_key,
                30 * 24 * 60 * 60,
                profile_json,
            )

            logger.debug(
                "redis.state_saved",
                profile_id=self._profile_id,
                broker=self._broker,
                positions_count=len(positions.get("positions", {})),
                seen_articles=len(seen_article_ids),
            )

        except Exception as e:
            logger.error(
                "redis.save_failed",
                profile_id=self._profile_id,
                broker=self._broker,
                error=str(e),
                exc_info=True,
            )
            raise

    def load_state(self) -> Optional[dict]:
        """
        Load state from Redis.

        Merges SHARED state (positions + risk) with PROFILE-SPECIFIC state (seen articles).

        Returns:
            State dict if found, None otherwise
        """
        try:
            # Load SHARED state
            shared_json = self._client.get(self._shared_state_key)

            if shared_json is None:
                logger.info(
                    "redis.no_shared_state_found",
                    profile_id=self._profile_id,
                    broker=self._broker,
                    shared_state_key=self._shared_state_key,
                )
                shared_state = {
                    "positions": {},
                    "risk": {},
                }
            else:
                shared_state = json.loads(shared_json)

            # Load PROFILE-SPECIFIC state
            profile_json = self._client.get(self._profile_state_key)

            if profile_json is None:
                logger.info(
                    "redis.no_profile_state_found",
                    profile_id=self._profile_id,
                    profile_state_key=self._profile_state_key,
                )
                seen_article_ids = []
            else:
                profile_state = json.loads(profile_json)
                seen_article_ids = profile_state.get("seen_article_ids", [])

            # Merge shared + profile state
            state = {
                "version": shared_state.get("version", 1),
                "last_saved": shared_state.get("last_saved"),
                "positions": shared_state.get("positions", {}),
                "risk": shared_state.get("risk", {}),
                "seen_article_ids": seen_article_ids,
            }

            logger.info(
                "redis.state_loaded",
                profile_id=self._profile_id,
                broker=self._broker,
                last_saved=state.get("last_saved"),
                positions_count=len(state.get("positions", {}).get("positions", {})),
                seen_articles=len(seen_article_ids),
            )

            return state

        except json.JSONDecodeError as e:
            logger.error(
                "redis.state_parse_failed",
                profile_id=self._profile_id,
                broker=self._broker,
                error=str(e),
            )
            return None
        except Exception as e:
            logger.error(
                "redis.load_failed",
                profile_id=self._profile_id,
                broker=self._broker,
                error=str(e),
                exc_info=True,
            )
            raise

    def close(self) -> None:
        """Close Redis connection."""
        try:
            self._client.close()
            logger.debug("redis.connection_closed", profile_id=self._profile_id)
        except Exception as e:
            logger.warning("redis.close_failed", error=str(e))
