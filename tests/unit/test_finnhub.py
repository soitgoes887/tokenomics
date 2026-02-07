"""Tests for Finnhub news provider."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.news.finnhub import FinnhubNewsProvider


class TestFinnhubNewsProvider:
    @pytest.fixture
    def provider(self, test_config, mock_secrets):
        mock_secrets.finnhub_api_key = "test-finnhub-key"
        with patch("tokenomics.news.finnhub.finnhub") as mock_finnhub:
            p = FinnhubNewsProvider(test_config, mock_secrets)
            p._client = MagicMock()
            return p

    def test_fetch_company_news(self, provider):
        """Should return normalized articles from Finnhub."""
        provider._config.symbols = ["AAPL"]
        provider._client.company_news.return_value = [
            {
                "id": 12345,
                "headline": "Apple beats Q1 earnings",
                "summary": "Strong iPhone sales drove results",
                "related": "AAPL",
                "source": "Reuters",
                "url": "https://example.com/1",
                "datetime": 1738886400,
            },
        ]

        articles = provider.fetch_new_articles()
        assert len(articles) == 1
        assert articles[0].headline == "Apple beats Q1 earnings"
        assert articles[0].symbols == ["AAPL"]
        assert articles[0].source == "Reuters"

    def test_fetch_general_news(self, provider):
        """Should fetch general news when no symbols configured."""
        provider._config.symbols = []
        provider._client.general_news.return_value = [
            {
                "id": 67890,
                "headline": "Markets rally on Fed decision",
                "summary": "Broad rally across sectors",
                "related": "SPY,QQQ",
                "source": "Bloomberg",
                "url": "https://example.com/2",
                "datetime": 1738886400,
            },
        ]

        articles = provider.fetch_new_articles()
        assert len(articles) == 1
        assert articles[0].symbols == ["SPY", "QQQ"]

    def test_deduplication(self, provider):
        """Should not return articles already seen."""
        provider._config.symbols = []
        raw = [
            {
                "id": 111,
                "headline": "Test",
                "summary": "Test summary",
                "related": "AAPL",
                "source": "test",
                "url": "https://example.com",
                "datetime": 1738886400,
            },
        ]
        provider._client.general_news.return_value = raw

        assert len(provider.fetch_new_articles()) == 1
        assert len(provider.fetch_new_articles()) == 0

    def test_skip_no_symbols(self, provider):
        """Should skip articles with no related symbols."""
        provider._config.symbols = []
        provider._client.general_news.return_value = [
            {
                "id": 222,
                "headline": "General news",
                "summary": "No stocks mentioned",
                "related": "",
                "source": "test",
                "url": "https://example.com",
                "datetime": 1738886400,
            },
        ]

        articles = provider.fetch_new_articles()
        assert len(articles) == 0

    def test_seen_ids_persistence(self, provider):
        """Should save and restore seen IDs."""
        provider._seen_ids = {"a", "b", "c"}
        saved = provider.get_seen_ids()
        assert saved == {"a", "b", "c"}

        provider._seen_ids = set()
        provider.restore_seen_ids(saved)
        assert provider._seen_ids == {"a", "b", "c"}
