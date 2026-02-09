"""Tests for Perplexity Sonar sentiment analyzer with mocked OpenAI client."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.analysis.perplexity import PerplexityLLMProvider
from tokenomics.models import NewsArticle, Sentiment, TimeHorizon


class TestPerplexityLLMProvider:
    @pytest.fixture
    def analyzer(self, test_config, mock_secrets):
        mock_secrets.perplexity_api_key = "test-perplexity-key"
        with patch("tokenomics.analysis.perplexity.OpenAI") as mock_openai:
            with patch("tokenomics.analysis.perplexity.get_decision_logger"):
                a = PerplexityLLMProvider(test_config, mock_secrets)
                a._client = MagicMock()
                return a

    def _mock_response(self, data: dict) -> MagicMock:
        """Create a mock OpenAI chat completion response."""
        mock_message = MagicMock()
        mock_message.content = json.dumps(data)
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        return mock_resp

    def test_analyze_bullish(self, analyzer, sample_article):
        """Should parse a valid bullish response."""
        analyzer._client.chat.completions.create.return_value = self._mock_response(
            {
                "sentiment": "BULLISH",
                "conviction": 85,
                "time_horizon": "MEDIUM",
                "reasoning": "Strong earnings beat expectations.",
                "key_factors": ["earnings beat", "revenue growth"],
            }
        )

        result = analyzer.analyze(sample_article, "AAPL")
        assert result is not None
        assert result.sentiment == Sentiment.BULLISH
        assert result.conviction == 85
        assert result.time_horizon == TimeHorizon.MEDIUM
        assert result.symbol == "AAPL"
        assert result.article_id == "article-001"

    def test_analyze_bearish(self, analyzer, sample_article):
        """Should parse a valid bearish response."""
        analyzer._client.chat.completions.create.return_value = self._mock_response(
            {
                "sentiment": "BEARISH",
                "conviction": 72,
                "time_horizon": "SHORT",
                "reasoning": "CEO resignation is concerning.",
                "key_factors": ["leadership change"],
            }
        )

        result = analyzer.analyze(sample_article, "AAPL")
        assert result is not None
        assert result.sentiment == Sentiment.BEARISH
        assert result.conviction == 72

    def test_analyze_invalid_json_returns_none(self, analyzer, sample_article):
        """Should return None on malformed JSON."""
        mock_message = MagicMock()
        mock_message.content = "This is not valid JSON"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        analyzer._client.chat.completions.create.return_value = mock_resp

        result = analyzer.analyze(sample_article, "AAPL")
        assert result is None

    def test_analyze_batch(self, analyzer):
        """Should produce one result per (article, symbol) pair."""
        articles = [
            NewsArticle(
                id="a1",
                headline="News 1",
                summary="Summary 1",
                symbols=["AAPL", "MSFT"],
                source="test",
                url="http://test.com",
                created_at=datetime.now(timezone.utc),
            ),
        ]

        analyzer._client.chat.completions.create.return_value = self._mock_response(
            {
                "sentiment": "NEUTRAL",
                "conviction": 50,
                "time_horizon": "SHORT",
                "reasoning": "Mixed signals.",
                "key_factors": ["mixed"],
            }
        )

        results = analyzer.analyze_batch(articles)
        assert len(results) == 2
        symbols = {r.symbol for r in results}
        assert symbols == {"AAPL", "MSFT"}

    def test_prompt_includes_article_details(self, analyzer, sample_article):
        """Prompt should contain the article headline and summary."""
        prompt = analyzer._build_prompt(sample_article, "AAPL")
        assert "Apple Reports Record Q1 Services Revenue" in prompt
        assert "AAPL" in prompt
        assert "reuters" in prompt

    def test_prompt_truncates_long_content(self, analyzer, sample_article):
        """Content longer than 3000 chars should be truncated."""
        sample_article.content = "x" * 5000
        prompt = analyzer._build_prompt(sample_article, "AAPL")
        assert len(prompt) < 5000 + 1000
