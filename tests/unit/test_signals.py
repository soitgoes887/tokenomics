"""Tests for signal generation logic."""

import pytest

from tokenomics.models import (
    Sentiment,
    SentimentResult,
    TimeHorizon,
    TradeAction,
)
from tokenomics.trading.signals import SignalGenerator


class TestSignalGenerator:
    @pytest.fixture
    def generator(self, test_config):
        return SignalGenerator(test_config)

    def _make_result(
        self,
        symbol="AAPL",
        sentiment=Sentiment.BULLISH,
        conviction=82,
    ) -> SentimentResult:
        return SentimentResult(
            article_id="test-article",
            headline="Test headline",
            symbol=symbol,
            sentiment=sentiment,
            conviction=conviction,
            time_horizon=TimeHorizon.MEDIUM,
            reasoning="Test reasoning",
            key_factors=["factor1"],
        )

    def test_bullish_high_conviction_generates_buy(self, generator):
        result = self._make_result(conviction=82)
        signal = generator.evaluate(result, set(), 0)
        assert signal is not None
        assert signal.action == TradeAction.BUY
        assert signal.symbol == "AAPL"
        assert signal.conviction == 82

    def test_conviction_at_threshold_generates_signal(self, generator):
        """Conviction exactly at 70 should trigger."""
        result = self._make_result(conviction=70)
        signal = generator.evaluate(result, set(), 0)
        assert signal is not None
        assert signal.action == TradeAction.BUY

    def test_conviction_below_threshold_no_signal(self, generator):
        """Conviction at 69 should not trigger."""
        result = self._make_result(conviction=69)
        signal = generator.evaluate(result, set(), 0)
        assert signal is None

    def test_neutral_sentiment_no_signal(self, generator):
        """Neutral sentiment should never generate a signal."""
        result = self._make_result(sentiment=Sentiment.NEUTRAL, conviction=95)
        signal = generator.evaluate(result, set(), 0)
        assert signal is None

    def test_already_held_no_buy(self, generator):
        """Should not buy if already holding the symbol."""
        result = self._make_result()
        signal = generator.evaluate(result, {"AAPL"}, 1)
        assert signal is None

    def test_max_positions_no_buy(self, generator):
        """Should not buy if at max open positions."""
        result = self._make_result()
        signal = generator.evaluate(result, set(), 10)  # max is 10
        assert signal is None

    def test_bearish_on_held_generates_sell(self, generator):
        """Bearish sentiment on held position should generate SELL."""
        result = self._make_result(sentiment=Sentiment.BEARISH, conviction=80)
        signal = generator.evaluate(result, {"AAPL"}, 1)
        assert signal is not None
        assert signal.action == TradeAction.SELL

    def test_bearish_on_not_held_no_signal(self, generator):
        """Bearish sentiment on unheld position should not generate signal (long-only)."""
        result = self._make_result(sentiment=Sentiment.BEARISH, conviction=80)
        signal = generator.evaluate(result, set(), 0)
        assert signal is None

    def test_position_size_scales_with_conviction(self, generator):
        """Higher conviction should produce larger position sizes."""
        low = self._make_result(conviction=70)
        high = self._make_result(conviction=100)
        signal_low = generator.evaluate(low, set(), 0)
        signal_high = generator.evaluate(high, set(), 0)
        assert signal_low.position_size_usd < signal_high.position_size_usd
        assert signal_low.position_size_usd >= 500  # min
        assert signal_high.position_size_usd <= 1000  # max

    def test_position_size_at_min_conviction(self, generator):
        """Conviction at threshold should give minimum position size."""
        result = self._make_result(conviction=70)
        signal = generator.evaluate(result, set(), 0)
        assert signal.position_size_usd == pytest.approx(500, abs=1)

    def test_position_size_at_max_conviction(self, generator):
        """Conviction at 100 should give maximum position size."""
        result = self._make_result(conviction=100)
        signal = generator.evaluate(result, set(), 0)
        assert signal.position_size_usd == pytest.approx(1000, abs=1)
