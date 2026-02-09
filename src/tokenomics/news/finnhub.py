"""Finnhub News API polling with deduplication."""

import re
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
        # Build lookup set for symbol extraction from headlines
        self._symbol_set: set[str] = set(self._config.symbols) if self._config.symbols else set()
        # Pre-compile word boundary regex for each symbol (min 2 chars to avoid noise)
        self._symbol_pattern: re.Pattern | None = None
        if self._symbol_set:
            # Only match symbols that are 2+ characters to avoid false positives
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

            # Always use general_news (single API call) and match symbols from text
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

    def _extract_symbols_from_text(self, text: str) -> list[str]:
        """Extract known S&P 500 symbols mentioned in text."""
        if not self._symbol_pattern:
            return []
        return list(set(self._symbol_pattern.findall(text)))

    def _normalize_article(self, raw: dict) -> NewsArticle:
        """Convert Finnhub news dict to our domain model."""
        # Finnhub uses 'related' field for symbols (e.g. "AAPL,MSFT")
        related = raw.get("related", "")
        symbols = [s.strip() for s in related.split(",") if s.strip()] if related else []

        # If no symbols from 'related' field, extract from headline/summary
        if not symbols and self._symbol_set:
            headline = raw.get("headline", "")
            summary = raw.get("summary", "")
            symbols = self._extract_symbols_from_text(f"{headline} {summary}")

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
