"""Tests for MarketAux news provider."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.news.marketaux import MarketauxNewsProvider


class TestMarketauxNewsProvider:
    @pytest.fixture
    def provider(self, test_config, mock_secrets):
        mock_secrets.marketaux_api_key = "test-marketaux-key"
        with patch("tokenomics.news.marketaux.httpx") as mock_httpx:
            p = MarketauxNewsProvider(test_config, mock_secrets)
            p._http = MagicMock()
            return p

    def _mock_response(self, articles):
        """Create a mock httpx response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": articles, "meta": {"found": len(articles)}}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_fetch_new_articles(self, provider):
        """Should return normalized articles from MarketAux."""
        provider._http.get.return_value = self._mock_response([
            {
                "uuid": "article-001",
                "title": "Apple beats Q1 earnings expectations",
                "description": "Strong iPhone sales drove record revenue",
                "snippet": "Apple Inc reported...",
                "url": "https://example.com/1",
                "source": "reuters",
                "published_at": "2026-02-09T14:30:00.000000Z",
                "entities": [
                    {
                        "symbol": "AAPL",
                        "name": "Apple Inc",
                        "type": "equity",
                        "match_score": 15.0,
                        "sentiment_score": 0.8,
                    }
                ],
            },
        ])

        articles = provider.fetch_new_articles()
        assert len(articles) == 1
        assert articles[0].headline == "Apple beats Q1 earnings expectations"
        assert articles[0].symbols == ["AAPL"]
        assert articles[0].source == "reuters"
        assert articles[0].id == "article-001"

    def test_deduplication(self, provider):
        """Should not return articles already seen."""
        raw = [
            {
                "uuid": "article-001",
                "title": "Test",
                "description": "Test summary",
                "url": "https://example.com",
                "source": "test",
                "published_at": "2026-02-09T14:30:00.000000Z",
                "entities": [{"symbol": "AAPL", "type": "equity"}],
            },
        ]
        provider._http.get.return_value = self._mock_response(raw)

        assert len(provider.fetch_new_articles()) == 1
        assert len(provider.fetch_new_articles()) == 0

    def test_skip_no_symbols(self, provider):
        """Should skip articles with no entity symbols."""
        provider._http.get.return_value = self._mock_response([
            {
                "uuid": "article-002",
                "title": "General market news",
                "description": "Markets were mixed today",
                "url": "https://example.com",
                "source": "test",
                "published_at": "2026-02-09T14:30:00.000000Z",
                "entities": [],
            },
        ])

        articles = provider.fetch_new_articles()
        assert len(articles) == 0

    def test_multiple_entities(self, provider):
        """Should extract multiple symbols from entities."""
        provider._http.get.return_value = self._mock_response([
            {
                "uuid": "article-003",
                "title": "Tech stocks rally",
                "description": "AAPL and MSFT lead gains",
                "url": "https://example.com",
                "source": "test",
                "published_at": "2026-02-09T14:30:00.000000Z",
                "entities": [
                    {"symbol": "AAPL", "type": "equity"},
                    {"symbol": "MSFT", "type": "equity"},
                ],
            },
        ])

        articles = provider.fetch_new_articles()
        assert len(articles) == 1
        assert set(articles[0].symbols) == {"AAPL", "MSFT"}

    def test_seen_ids_persistence(self, provider):
        """Should save and restore seen IDs."""
        provider._seen_ids = {"a", "b", "c"}
        saved = provider.get_seen_ids()
        assert saved == {"a", "b", "c"}

        provider._seen_ids = set()
        provider.restore_seen_ids(saved)
        assert provider._seen_ids == {"a", "b", "c"}

    def test_missing_api_key_raises(self, test_config, mock_secrets):
        """Should raise ValueError if API key is missing."""
        mock_secrets.marketaux_api_key = ""
        with pytest.raises(ValueError, match="MARKETAUX_API_KEY"):
            MarketauxNewsProvider(test_config, mock_secrets)
