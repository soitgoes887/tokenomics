"""MarketAux News API polling with deduplication."""

from datetime import datetime, timedelta, timezone

import httpx
import structlog

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import NewsArticle
from tokenomics.news.base import NewsProvider
from tokenomics.news.fetcher import NewsFetchError

logger = structlog.get_logger(__name__)

MARKETAUX_BASE_URL = "https://api.marketaux.com/v1/news/all"


class MarketauxNewsProvider(NewsProvider):
    """Polls MarketAux News API and yields unseen articles."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        if not secrets.marketaux_api_key:
            raise ValueError(
                "MARKETAUX_API_KEY is required when using marketaux provider. "
                "Set it in .env or as an environment variable."
            )
        self._config = config.news
        self._api_key = secrets.marketaux_api_key
        self._http = httpx.Client(timeout=30)
        self._seen_ids: set[str] = set()
        self._max_seen_ids = 10_000
        self._last_fetch_time: datetime | None = None
        self._symbol_set: set[str] = set(self._config.symbols) if self._config.symbols else set()

    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch articles from MarketAux. Returns only unseen articles."""
        try:
            params = self._build_params()

            logger.debug(
                "news.polling",
                provider="marketaux",
                symbols_in_request=len(self._config.symbols) if self._config.symbols else "all",
                since=params.get("published_after", "none"),
            )

            response = self._http.get(MARKETAUX_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

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

            self._last_fetch_time = datetime.now(timezone.utc)
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

        if self._last_fetch_time:
            params["published_after"] = self._last_fetch_time.strftime(
                "%Y-%m-%dT%H:%M"
            )
        else:
            lookback = datetime.now(timezone.utc) - timedelta(
                minutes=self._config.lookback_minutes
            )
            params["published_after"] = lookback.strftime("%Y-%m-%dT%H:%M")

        # MarketAux supports comma-separated symbols in the request
        if self._config.symbols:
            # API may have URL length limits; batch symbols
            # Send up to 100 symbols per request
            symbols = self._config.symbols[:100]
            params["symbols"] = ",".join(symbols)

        return params

    def _normalize_article(self, raw: dict) -> NewsArticle:
        """Convert MarketAux article to our domain model."""
        # Extract symbols from entities array
        symbols = []
        for entity in raw.get("entities", []):
            symbol = entity.get("symbol")
            entity_type = entity.get("type", "")
            if symbol and entity_type in ("equity", ""):
                # Filter to configured symbols if set
                if not self._symbol_set or symbol in self._symbol_set:
                    symbols.append(symbol)

        # Deduplicate while preserving order
        seen = set()
        unique_symbols = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                unique_symbols.append(s)

        # Parse published_at
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
