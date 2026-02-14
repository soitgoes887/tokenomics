"""MarketAux News API polling with deduplication and rate limiting."""

from datetime import datetime, timedelta, timezone

import requests
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import NewsArticle
from tokenomics.news.base import NewsProvider
from tokenomics.news.fetcher import NewsFetchError

logger = structlog.get_logger(__name__)

MARKETAUX_BASE_URL = "https://api.marketaux.com/v1/news/all"

# US market hours in UTC (ET + 5 during EST, ET + 4 during EDT)
# Conservative: 13:30 - 20:00 UTC covers EST market hours
MARKET_OPEN_UTC_HOUR = 13
MARKET_OPEN_UTC_MINUTE = 30
MARKET_CLOSE_UTC_HOUR = 20
MARKET_CLOSE_UTC_MINUTE = 0

# 100 calls/day budget spread across ~6.5 market hours = ~1 call every 4 minutes
DAILY_CALL_LIMIT = 100
MIN_POLL_INTERVAL_SECONDS = 240  # 4 minutes between calls


class MarketauxNewsProvider(NewsProvider):
    """Polls MarketAux News API with rate limiting (100 calls/day, market hours only)."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        if not secrets.marketaux_api_key:
            raise ValueError(
                "MARKETAUX_API_KEY is required when using marketaux provider. "
                "Set it in .env or as an environment variable."
            )
        self._config = config.news
        self._api_key = secrets.marketaux_api_key
        self._session = requests.Session()
        self._seen_ids: set[str] = set()
        self._max_seen_ids = 10_000
        self._last_fetch_time: datetime | None = None
        self._last_api_call: datetime | None = None
        self._daily_call_count = 0
        self._daily_call_date: str | None = None
        self._symbol_set: set[str] = set(self._config.symbols) if self._config.symbols else set()

    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch articles from MarketAux. Rate-limited to market hours, 100 calls/day."""
        now = datetime.now(timezone.utc)

        # Reset daily counter at midnight UTC
        today = now.strftime("%Y-%m-%d")
        if self._daily_call_date != today:
            self._daily_call_count = 0
            self._daily_call_date = today

        # Check daily limit
        if self._daily_call_count >= DAILY_CALL_LIMIT:
            logger.debug(
                "news.rate_limited",
                provider="marketaux",
                reason="daily_limit_reached",
                calls_today=self._daily_call_count,
            )
            return []

        # Check market hours (skip outside 13:30-20:00 UTC)
        market_open = now.replace(
            hour=MARKET_OPEN_UTC_HOUR, minute=MARKET_OPEN_UTC_MINUTE, second=0
        )
        market_close = now.replace(
            hour=MARKET_CLOSE_UTC_HOUR, minute=MARKET_CLOSE_UTC_MINUTE, second=0
        )
        if not (market_open <= now <= market_close):
            # Allow one call outside market hours on first fetch (preflight)
            if self._last_api_call is not None:
                if now.weekday() < 5:  # Weekday
                    logger.debug(
                        "news.rate_limited",
                        provider="marketaux",
                        reason="outside_market_hours",
                        current_utc=now.strftime("%H:%M"),
                    )
                return []

        # Enforce minimum interval between calls
        if self._last_api_call:
            elapsed = (now - self._last_api_call).total_seconds()
            if elapsed < MIN_POLL_INTERVAL_SECONDS:
                logger.debug(
                    "news.rate_limited",
                    provider="marketaux",
                    reason="min_interval",
                    seconds_remaining=int(MIN_POLL_INTERVAL_SECONDS - elapsed),
                )
                return []

        # Make the API call with retry on timeout
        try:
            params = self._build_params()

            logger.debug(
                "news.polling",
                provider="marketaux",
                call_number=self._daily_call_count + 1,
                daily_limit=DAILY_CALL_LIMIT,
                since=params.get("published_after", "first_fetch"),
            )

            response = self._fetch_with_retry(params)
            response.raise_for_status()
            data = response.json()

            self._last_api_call = now
            self._daily_call_count += 1

            raw_articles = data.get("data", [])

            already_seen = 0
            no_symbols = 0
            contentless = 0
            articles = []

            for raw in raw_articles:
                article_id = raw.get("uuid", "")
                if not article_id:
                    continue

                if article_id in self._seen_ids:
                    already_seen += 1
                    continue

                if self._config.exclude_contentless and not raw.get("description"):
                    contentless += 1
                    continue

                article = self._normalize_article(raw)
                if article.symbols:
                    articles.append(article)
                    self._seen_ids.add(article.id)
                    logger.debug(
                        "news.article_relevant",
                        provider="marketaux",
                        article_id=article.id,
                        headline=article.headline[:80],
                        symbols=article.symbols,
                        source=article.source,
                    )
                else:
                    no_symbols += 1

            self._last_fetch_time = now
            self._prune_seen_ids()

            logger.info(
                "news.poll_complete",
                provider="marketaux",
                raw_count=len(raw_articles),
                already_seen=already_seen,
                no_symbols=no_symbols,
                contentless=contentless,
                new_relevant=len(articles),
                total_seen=len(self._seen_ids),
                calls_today=self._daily_call_count,
            )

            return articles

        except Exception as e:
            logger.error("news.fetch_failed", provider="marketaux", error=str(e))
            raise NewsFetchError(f"Failed to fetch MarketAux news: {e}") from e

    def _build_params(self) -> dict:
        """Build query parameters for MarketAux API."""
        params = {
            "api_token": self._api_key,
            "language": "en",
            "filter_entities": "true",
            "limit": 50,
        }

        # Only apply time filter on subsequent polls, not the first fetch
        if self._last_fetch_time:
            params["published_after"] = self._last_fetch_time.strftime(
                "%Y-%m-%dT%H:%M"
            )

        return params

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_exception_type(requests.exceptions.Timeout),
        reraise=True,
    )
    def _fetch_with_retry(self, params: dict) -> requests.Response:
        """
        Fetch from marketaux API with retry on timeout.

        Retries up to 3 times with exponential backoff (4s, 8s, 16s).
        Only retries on Timeout errors, not HTTP errors.
        """
        logger.debug(
            "news.api_call_attempt",
            provider="marketaux",
            timeout=60,
        )
        return self._session.get(MARKETAUX_BASE_URL, params=params, timeout=60)

    def _normalize_article(self, raw: dict) -> NewsArticle:
        """Convert MarketAux article to our domain model."""
        symbols = []
        for entity in raw.get("entities", []):
            symbol = entity.get("symbol")
            entity_type = entity.get("type", "")
            if symbol and entity_type in ("equity", ""):
                if not self._symbol_set or symbol in self._symbol_set:
                    symbols.append(symbol)

        # Deduplicate while preserving order
        seen = set()
        unique_symbols = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                unique_symbols.append(s)

        published_at = raw.get("published_at", "")
        try:
            created_at = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            created_at = datetime.now(timezone.utc)

        return NewsArticle(
            id=raw.get("uuid", ""),
            headline=raw.get("title", ""),
            summary=raw.get("description", "") or raw.get("snippet", ""),
            content=None,
            symbols=unique_symbols,
            source=raw.get("source", "marketaux"),
            url=raw.get("url", ""),
            created_at=created_at,
        )

    def _prune_seen_ids(self) -> None:
        """Keep seen_ids set bounded."""
        if len(self._seen_ids) > self._max_seen_ids:
            to_remove = len(self._seen_ids) - (self._max_seen_ids // 2)
            for _ in range(to_remove):
                self._seen_ids.pop()

    def get_seen_ids(self) -> set[str]:
        """Return seen IDs for state persistence."""
        return self._seen_ids.copy()

    def restore_seen_ids(self, ids: set[str]) -> None:
        """Restore seen IDs from persisted state."""
        self._seen_ids = ids
