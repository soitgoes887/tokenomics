"""Finnhub News API polling with deduplication."""

from datetime import datetime, timedelta, timezone

import finnhub
import structlog

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import NewsArticle
from tokenomics.news.base import NewsProvider
from tokenomics.news.fetcher import NewsFetchError

logger = structlog.get_logger(__name__)


class FinnhubNewsProvider(NewsProvider):
    """Polls Finnhub News API and yields unseen articles."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config.news
        self._client = finnhub.Client(api_key=secrets.finnhub_api_key)
        self._seen_ids: set[str] = set()
        self._max_seen_ids = 10_000
        self._last_fetch_time: datetime | None = None

    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch articles from Finnhub. Returns only unseen articles."""
        try:
            now = datetime.now(timezone.utc)

            if self._last_fetch_time:
                from_date = self._last_fetch_time.strftime("%Y-%m-%d")
            else:
                from_date = (
                    now - timedelta(minutes=self._config.lookback_minutes)
                ).strftime("%Y-%m-%d")

            to_date = now.strftime("%Y-%m-%d")

            if self._config.symbols:
                # Fetch news per symbol
                raw_articles = []
                for symbol in self._config.symbols:
                    raw_articles.extend(
                        self._client.company_news(symbol, _from=from_date, to=to_date)
                    )
            else:
                # Fetch general market news
                raw_articles = self._client.general_news("general", min_id=0)

            articles = []
            for raw in raw_articles:
                article_id = str(raw.get("id", raw.get("headline", "")))

                if article_id in self._seen_ids:
                    continue

                if self._config.exclude_contentless and not raw.get("summary"):
                    continue

                article = self._normalize_article(raw)
                if article.symbols:
                    articles.append(article)
                    self._seen_ids.add(article.id)

            self._last_fetch_time = now
            self._prune_seen_ids()

            if articles:
                logger.info(
                    "news.fetched",
                    provider="finnhub",
                    new_count=len(articles),
                    total_seen=len(self._seen_ids),
                )

            return articles

        except Exception as e:
            logger.error("news.fetch_failed", provider="finnhub", error=str(e))
            raise NewsFetchError(f"Failed to fetch Finnhub news: {e}") from e

    def _normalize_article(self, raw: dict) -> NewsArticle:
        """Convert Finnhub news dict to our domain model."""
        # Finnhub uses 'related' field for symbols (e.g. "AAPL,MSFT")
        related = raw.get("related", "")
        symbols = [s.strip() for s in related.split(",") if s.strip()] if related else []

        # Finnhub datetime is a UNIX timestamp
        ts = raw.get("datetime", 0)
        created_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return NewsArticle(
            id=str(raw.get("id", "")),
            headline=raw.get("headline", ""),
            summary=raw.get("summary", ""),
            content=None,
            symbols=symbols,
            source=raw.get("source", "finnhub"),
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
