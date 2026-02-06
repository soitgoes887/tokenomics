"""Tests for sentiment analyzer with mocked Gemini client."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.analysis.sentiment import SentimentAnalyzer
from tokenomics.models import NewsArticle, Sentiment, TimeHorizon


class TestSentimentAnalyzer:
    @pytest.fixture
    def analyzer(self, test_config, mock_secrets):
        with patch("tokenomics.analysis.sentiment.genai") as mock_genai:
            with patch("tokenomics.analysis.sentiment.get_decision_logger"):
                a = SentimentAnalyzer(test_config, mock_secrets)
                a._client = MagicMock()
                return a

    def test_analyze_bullish(self, analyzer, sample_article):
        """Should parse a valid bullish response."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "sentiment": "BULLISH",
                "conviction": 85,
                "time_horizon": "MEDIUM",
                "reasoning": "Strong earnings beat expectations.",
                "key_factors": ["earnings beat", "revenue growth"],
            }
        )
        analyzer._client.models.generate_content.return_value = mock_response

        result = analyzer.analyze(sample_article, "AAPL")
        assert result is not None
        assert result.sentiment == Sentiment.BULLISH
        assert result.conviction == 85
        assert result.time_horizon == TimeHorizon.MEDIUM
        assert result.symbol == "AAPL"
        assert result.article_id == "article-001"

    def test_analyze_bearish(self, analyzer, sample_article):
        """Should parse a valid bearish response."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "sentiment": "BEARISH",
                "conviction": 72,
                "time_horizon": "SHORT",
                "reasoning": "CEO resignation is concerning.",
                "key_factors": ["leadership change"],
            }
        )
        analyzer._client.models.generate_content.return_value = mock_response

        result = analyzer.analyze(sample_article, "AAPL")
        assert result is not None
        assert result.sentiment == Sentiment.BEARISH
        assert result.conviction == 72

    def test_analyze_invalid_json_returns_none(self, analyzer, sample_article):
        """Should return None on malformed JSON."""
        mock_response = MagicMock()
        mock_response.text = "This is not valid JSON"
        analyzer._client.models.generate_content.return_value = mock_response

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

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "sentiment": "NEUTRAL",
                "conviction": 50,
                "time_horizon": "SHORT",
                "reasoning": "Mixed signals.",
                "key_factors": ["mixed"],
            }
        )
        analyzer._client.models.generate_content.return_value = mock_response

        results = analyzer.analyze_batch(articles)
        # One article with two symbols = two results
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
        # The content in the prompt should be truncated
        assert len(prompt) < 5000 + 1000  # prompt template + 3000 content
