"""Tests for news fetcher with mocked Alpaca client."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.models import NewsArticle
from tokenomics.news.fetcher import NewsFetcher


class MockNewsItem:
    """Mock Alpaca news article."""

    def __init__(self, id, headline, summary, symbols, source="reuters", content=None):
        self.id = id
        self.headline = headline
        self.summary = summary
        self.symbols = symbols
        self.source = source
        self.content = content
        self.url = f"https://example.com/{id}"
        self.created_at = datetime.now(timezone.utc)


class MockNewsResponse:
    def __init__(self, articles):
        self.data = {"news": articles}


class TestNewsFetcher:
    @pytest.fixture
    def fetcher(self, test_config, mock_secrets):
        with patch("tokenomics.news.fetcher.NewsClient"):
            return NewsFetcher(test_config, mock_secrets)

    def test_fetch_new_articles(self, fetcher):
        """Should return normalized articles."""
        mock_articles = [
            MockNewsItem("1", "Apple beats earnings", "Great quarter", ["AAPL"]),
            MockNewsItem("2", "Google unveils AI", "New model", ["GOOG"]),
        ]
        fetcher._client.get_news.return_value = MockNewsResponse(mock_articles)

        articles = fetcher.fetch_new_articles()
        assert len(articles) == 2
        assert articles[0].id == "1"
        assert articles[0].headline == "Apple beats earnings"
        assert articles[0].symbols == ["AAPL"]

    def test_deduplication(self, fetcher):
        """Should not return articles already seen."""
        mock_articles = [
            MockNewsItem("1", "Apple beats earnings", "Great quarter", ["AAPL"]),
        ]
        fetcher._client.get_news.return_value = MockNewsResponse(mock_articles)

        # First fetch
        articles = fetcher.fetch_new_articles()
        assert len(articles) == 1

        # Second fetch with same article
        articles = fetcher.fetch_new_articles()
        assert len(articles) == 0

    def test_skip_no_symbols(self, fetcher):
        """Should skip articles mentioning no specific stocks."""
        mock_articles = [
            MockNewsItem("1", "Market update", "General news", []),
        ]
        fetcher._client.get_news.return_value = MockNewsResponse(mock_articles)

        articles = fetcher.fetch_new_articles()
        assert len(articles) == 0

    def test_skip_contentless(self, fetcher):
        """Should skip articles with no summary when configured."""
        mock_articles = [
            MockNewsItem("1", "No summary", "", ["AAPL"]),
        ]
        fetcher._client.get_news.return_value = MockNewsResponse(mock_articles)

        articles = fetcher.fetch_new_articles()
        assert len(articles) == 0

    def test_prune_seen_ids(self, fetcher):
        """Seen IDs should be pruned when exceeding max."""
        fetcher._max_seen_ids = 10
        for i in range(15):
            fetcher._seen_ids.add(str(i))

        fetcher._prune_seen_ids()
        assert len(fetcher._seen_ids) <= 10

    def test_seen_ids_persistence(self, fetcher):
        """Should be able to save and restore seen IDs."""
        fetcher._seen_ids = {"a", "b", "c"}
        saved = fetcher.get_seen_ids()
        assert saved == {"a", "b", "c"}

        fetcher._seen_ids = set()
        fetcher.restore_seen_ids(saved)
        assert fetcher._seen_ids == {"a", "b", "c"}
