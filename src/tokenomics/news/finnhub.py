"""Finnhub News API polling with deduplication."""

import re
from datetime import datetime, timezone

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
        self._symbol_set: set[str] = set(self._config.symbols) if self._config.symbols else set()

        # Pre-compile word boundary regex for ticker symbols (min 2 chars)
        self._symbol_pattern: re.Pattern | None = None
        if self._symbol_set:
            valid = sorted([s for s in self._symbol_set if len(s) >= 2], key=len, reverse=True)
            if valid:
                escaped = [re.escape(s) for s in valid]
                self._symbol_pattern = re.compile(
                    r'\b(' + '|'.join(escaped) + r')\b'
                )

    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch articles from Finnhub. Returns only unseen articles."""
        try:
            now = datetime.now(timezone.utc)

            logger.debug(
                "news.polling",
                provider="finnhub",
                symbols_configured=len(self._symbol_set),
            )

            raw_articles = self._client.general_news("general", min_id=0)

            already_seen = 0
            no_symbols = 0
            contentless = 0
            articles = []

            for raw in raw_articles:
                article_id = str(raw.get("id", raw.get("headline", "")))

                if article_id in self._seen_ids:
                    already_seen += 1
                    continue

                if self._config.exclude_contentless and not raw.get("summary"):
                    contentless += 1
                    continue

                article = self._normalize_article(raw)
                if article.symbols:
                    articles.append(article)
                    self._seen_ids.add(article.id)
                    logger.debug(
                        "news.article_relevant",
                        provider="finnhub",
                        article_id=article.id,
                        headline=article.headline[:80],
                        symbols=article.symbols,
                    )
                else:
                    no_symbols += 1

            self._last_fetch_time = now
            self._prune_seen_ids()

            logger.info(
                "news.poll_complete",
                provider="finnhub",
                raw_count=len(raw_articles),
                already_seen=already_seen,
                no_symbols=no_symbols,
                contentless=contentless,
                new_relevant=len(articles),
                total_seen=len(self._seen_ids),
            )

            return articles

        except Exception as e:
            logger.error("news.fetch_failed", provider="finnhub", error=str(e))
            raise NewsFetchError(f"Failed to fetch Finnhub news: {e}") from e

    def _normalize_article(self, raw: dict) -> NewsArticle:
        """Convert Finnhub news dict to our domain model."""
        related = raw.get("related", "")
        symbols = [s.strip() for s in related.split(",") if s.strip()] if related else []

        # If no symbols from 'related' field, try ticker regex extraction
        if not symbols and self._symbol_pattern:
            headline = raw.get("headline", "")
            summary = raw.get("summary", "")
            symbols = list(set(self._symbol_pattern.findall(f"{headline} {summary}")))

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
