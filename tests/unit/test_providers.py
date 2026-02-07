"""Tests for provider factory."""

import pytest

from tokenomics.analysis.base import LLMProvider
from tokenomics.analysis.sentiment import GeminiLLMProvider
from tokenomics.news.base import NewsProvider
from tokenomics.news.fetcher import AlpacaNewsProvider
from tokenomics.providers import create_broker_provider, create_llm_provider, create_news_provider
from tokenomics.trading.base import BrokerProvider
from tokenomics.trading.broker import AlpacaBrokerProvider

from unittest.mock import patch


class TestProviderFactory:
    def test_create_news_provider_alpaca(self, test_config, mock_secrets):
        with patch("tokenomics.news.fetcher.NewsClient"):
            provider = create_news_provider(test_config, mock_secrets)
        assert isinstance(provider, NewsProvider)
        assert isinstance(provider, AlpacaNewsProvider)

    def test_create_llm_provider_gemini(self, test_config, mock_secrets):
        with patch("tokenomics.analysis.sentiment.genai"):
            with patch("tokenomics.analysis.sentiment.get_decision_logger"):
                provider = create_llm_provider(test_config, mock_secrets)
        assert isinstance(provider, LLMProvider)
        assert isinstance(provider, GeminiLLMProvider)

    def test_create_broker_provider_alpaca(self, test_config, mock_secrets):
        with patch("tokenomics.trading.broker.TradingClient"):
            provider = create_broker_provider(test_config, mock_secrets)
        assert isinstance(provider, BrokerProvider)
        assert isinstance(provider, AlpacaBrokerProvider)

    def test_unknown_news_provider_raises(self, test_config, mock_secrets):
        test_config.providers.news = "unknown"
        with pytest.raises(ValueError, match="Unknown news provider"):
            create_news_provider(test_config, mock_secrets)

    def test_unknown_llm_provider_raises(self, test_config, mock_secrets):
        test_config.providers.llm = "unknown"
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_provider(test_config, mock_secrets)

    def test_unknown_broker_provider_raises(self, test_config, mock_secrets):
        test_config.providers.broker = "unknown"
        with pytest.raises(ValueError, match="Unknown broker provider"):
            create_broker_provider(test_config, mock_secrets)
