"""Tests for domain models."""

from datetime import datetime, timezone

from tokenomics.models import (
    NewsArticle,
    Position,
    Sentiment,
    SentimentResult,
    TimeHorizon,
    TradeAction,
    TradeSignal,
)


class TestNewsArticle:
    def test_creation(self, sample_article):
        assert sample_article.id == "article-001"
        assert sample_article.symbols == ["AAPL"]
        assert sample_article.source == "reuters"

    def test_serialization_roundtrip(self, sample_article):
        data = sample_article.model_dump(mode="json")
        restored = NewsArticle.model_validate(data)
        assert restored.id == sample_article.id
        assert restored.headline == sample_article.headline
        assert restored.symbols == sample_article.symbols


class TestSentimentResult:
    def test_creation(self, sample_sentiment_result):
        assert sample_sentiment_result.sentiment == Sentiment.BULLISH
        assert sample_sentiment_result.conviction == 82
        assert sample_sentiment_result.time_horizon == TimeHorizon.MEDIUM

    def test_conviction_bounds(self):
        """Conviction must be 0-100."""
        import pytest

        with pytest.raises(ValueError):
            SentimentResult(
                article_id="test",
                headline="Test headline",
                symbol="AAPL",
                sentiment=Sentiment.BULLISH,
                conviction=150,  # out of range
                time_horizon=TimeHorizon.MEDIUM,
                reasoning="test",
                key_factors=[],
            )

    def test_serialization_roundtrip(self, sample_sentiment_result):
        data = sample_sentiment_result.model_dump(mode="json")
        restored = SentimentResult.model_validate(data)
        assert restored.sentiment == sample_sentiment_result.sentiment
        assert restored.conviction == sample_sentiment_result.conviction


class TestTradeSignal:
    def test_creation(self, sample_signal):
        assert sample_signal.action == TradeAction.BUY
        assert sample_signal.symbol == "AAPL"
        assert sample_signal.position_size_usd == 700

    def test_serialization_roundtrip(self, sample_signal):
        data = sample_signal.model_dump(mode="json")
        restored = TradeSignal.model_validate(data)
        assert restored.signal_id == sample_signal.signal_id
        assert restored.action == sample_signal.action


class TestPosition:
    def test_creation(self, sample_signal):
        now = datetime.now(timezone.utc)
        pos = Position(
            symbol="AAPL",
            alpaca_order_id="order-123",
            entry_price=245.50,
            quantity=3.2,
            position_size_usd=785.60,
            entry_date=now,
            signal=sample_signal,
            stop_loss_price=239.36,
            take_profit_price=260.23,
            max_hold_date=now,
            status="open",
        )
        assert pos.symbol == "AAPL"
        assert pos.entry_price == 245.50
        assert pos.status == "open"
        assert pos.pnl_usd is None

    def test_serialization_roundtrip(self, sample_signal):
        now = datetime.now(timezone.utc)
        pos = Position(
            symbol="AAPL",
            alpaca_order_id="order-123",
            entry_price=245.50,
            quantity=3.2,
            position_size_usd=785.60,
            entry_date=now,
            signal=sample_signal,
            stop_loss_price=239.36,
            take_profit_price=260.23,
            max_hold_date=now,
        )
        data = pos.model_dump(mode="json")
        restored = Position.model_validate(data)
        assert restored.symbol == pos.symbol
        assert restored.entry_price == pos.entry_price
        assert restored.signal.signal_id == sample_signal.signal_id


class TestEnums:
    def test_sentiment_values(self):
        assert Sentiment.BULLISH.value == "BULLISH"
        assert Sentiment.NEUTRAL.value == "NEUTRAL"
        assert Sentiment.BEARISH.value == "BEARISH"

    def test_trade_action_values(self):
        assert TradeAction.BUY.value == "BUY"
        assert TradeAction.SELL.value == "SELL"
        assert TradeAction.HOLD.value == "HOLD"

    def test_time_horizon_values(self):
        assert TimeHorizon.SHORT.value == "SHORT"
        assert TimeHorizon.MEDIUM.value == "MEDIUM"
        assert TimeHorizon.LONG.value == "LONG"
