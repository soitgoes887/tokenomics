"""Alpaca News API polling with deduplication."""

from datetime import datetime, timedelta, timezone

import structlog
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import NewsArticle
from tokenomics.news.base import NewsProvider

logger = structlog.get_logger(__name__)


class NewsFetchError(Exception):
    """Raised when news fetching fails after retries."""


class AlpacaNewsProvider(NewsProvider):
    """Polls Alpaca News API and yields unseen articles."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config.news
        self._client = NewsClient(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
        )
        self._seen_ids: set[str] = set()
        self._max_seen_ids = 10_000
        self._last_fetch_time: datetime | None = None

    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch articles newer than last fetch time. Returns only unseen articles."""
        try:
            request = self._build_request()
            logger.debug(
                "news.polling",
                provider="alpaca",
                symbols_configured=len(self._config.symbols),
                symbols_in_request="all" if not self._config.symbols or len(self._config.symbols) > 50 else len(self._config.symbols),
                since=str(request.start) if request.start else "none",
            )

            response = self._client.get_news(request)
            raw_articles = response.data.get("news", [])

            already_seen = 0
            no_symbols = 0
            contentless = 0
            articles = []
            for raw in raw_articles:
                if str(raw.id) in self._seen_ids:
                    already_seen += 1
                    continue

                if self._config.exclude_contentless and not raw.summary:
                    contentless += 1
                    continue

                article = self._normalize_article(raw)
                if article.symbols:
                    articles.append(article)
                    self._seen_ids.add(article.id)
                    logger.debug(
                        "news.article_relevant",
                        provider="alpaca",
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
                provider="alpaca",
                raw_count=len(raw_articles),
                already_seen=already_seen,
                no_symbols=no_symbols,
                contentless=contentless,
                new_relevant=len(articles),
                total_seen=len(self._seen_ids),
            )

            return articles

        except Exception as e:
            logger.error("news.fetch_failed", provider="alpaca", error=str(e))
            raise NewsFetchError(f"Failed to fetch news: {e}") from e

    def _build_request(self) -> NewsRequest:
        """Build Alpaca NewsRequest with configured parameters."""
        kwargs: dict = {"limit": 50}

        if self._last_fetch_time:
            kwargs["start"] = self._last_fetch_time
        else:
            # First fetch: look back configured minutes
            kwargs["start"] = datetime.now(timezone.utc) - timedelta(
                minutes=self._config.lookback_minutes
            )

        if self._config.symbols and len(self._config.symbols) <= 50:
            kwargs["symbols"] = ",".join(self._config.symbols)

        if self._config.include_content:
            kwargs["include_content"] = True

        return NewsRequest(**kwargs)

    def _normalize_article(self, raw) -> NewsArticle:
        """Convert Alpaca news object to our domain model."""
        return NewsArticle(
            id=str(raw.id),
            headline=raw.headline or "",
            summary=raw.summary or "",
            content=raw.content if hasattr(raw, "content") else None,
            symbols=[s for s in (raw.symbols or [])],
            source=raw.source or "unknown",
            url=raw.url or "",
            created_at=raw.created_at or datetime.now(timezone.utc),
        )

    def _prune_seen_ids(self) -> None:
        """Keep seen_ids set bounded."""
        if len(self._seen_ids) > self._max_seen_ids:
            # Remove oldest half (order not guaranteed with set, but that's fine
            # for dedup purposes -- we just need to prevent unbounded growth)
            to_remove = len(self._seen_ids) - (self._max_seen_ids // 2)
            for _ in range(to_remove):
                self._seen_ids.pop()

    def get_seen_ids(self) -> set[str]:
        """Return seen IDs for state persistence."""
        return self._seen_ids.copy()

    def restore_seen_ids(self, ids: set[str]) -> None:
        """Restore seen IDs from persisted state."""
        self._seen_ids = ids
