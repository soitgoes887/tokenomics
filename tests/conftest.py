"""Shared test fixtures."""

from datetime import datetime, timezone

import pytest

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import (
    NewsArticle,
    Sentiment,
    SentimentResult,
    TimeHorizon,
    TradeAction,
    TradeSignal,
)


@pytest.fixture
def test_config() -> AppConfig:
    """Provide a test configuration with safe defaults."""
    return AppConfig(
        strategy={
            "name": "test-strategy",
            "capital_usd": 10000,
            "position_size_min_usd": 500,
            "position_size_max_usd": 1000,
            "max_open_positions": 10,
            "target_new_positions_per_month": 15,
        },
        sentiment={
            "model": "gemini-2.5-flash-lite",
            "min_conviction": 70,
            "temperature": 0.1,
            "max_output_tokens": 512,
        },
        risk={
            "stop_loss_pct": 0.025,
            "take_profit_pct": 0.06,
            "max_hold_trading_days": 65,
            "daily_loss_limit_pct": 0.05,
            "monthly_loss_limit_pct": 0.10,
        },
        news={
            "poll_interval_seconds": 30,
            "symbols": [],
            "include_content": True,
            "exclude_contentless": True,
            "lookback_minutes": 5,
        },
        trading={
            "paper": True,
            "market_hours_only": True,
            "order_type": "market",
            "time_in_force": "day",
        },
        logging={
            "level": "DEBUG",
            "trade_log": "/tmp/test_trades.log",
            "decision_log": "/tmp/test_decisions.log",
            "app_log": "/tmp/test_tokenomics.log",
        },
    )


@pytest.fixture
def mock_secrets() -> Secrets:
    """Provide fake API keys for unit tests."""
    return Secrets(
        alpaca_api_key="test-alpaca-key",
        alpaca_secret_key="test-alpaca-secret",
        gemini_api_key="test-gemini-key",
    )


@pytest.fixture
def sample_article() -> NewsArticle:
    """Provide a realistic sample article."""
    return NewsArticle(
        id="article-001",
        headline="Apple Reports Record Q1 Services Revenue",
        summary="Apple Inc. reported record services revenue of $23.1 billion in Q1 2026, beating analyst expectations by 5%.",
        content="Apple Inc. (AAPL) reported its fiscal Q1 2026 results today...",
        symbols=["AAPL"],
        source="reuters",
        url="https://example.com/article-001",
        created_at=datetime(2026, 2, 6, 14, 30, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_sentiment_result() -> SentimentResult:
    """Provide a sample bullish sentiment result."""
    return SentimentResult(
        article_id="article-001",
        headline="Apple Reports Record Q1 Services Revenue",
        symbol="AAPL",
        sentiment=Sentiment.BULLISH,
        conviction=82,
        time_horizon=TimeHorizon.MEDIUM,
        reasoning="Record services revenue indicates strong recurring income growth.",
        key_factors=["record revenue", "beat expectations", "services growth"],
    )


@pytest.fixture
def sample_signal() -> TradeSignal:
    """Provide a sample buy signal."""
    return TradeSignal(
        signal_id="signal-001",
        article_id="article-001",
        symbol="AAPL",
        action=TradeAction.BUY,
        conviction=82,
        sentiment=Sentiment.BULLISH,
        position_size_usd=700,
        reasoning="Record services revenue indicates strong recurring income growth.",
    )
