"""Tests for risk management."""

from datetime import date

import pytest

from tokenomics.models import Sentiment, TradeAction, TradeSignal
from tokenomics.portfolio.risk import RiskManager


class TestRiskManager:
    @pytest.fixture
    def risk_mgr(self, test_config):
        return RiskManager(test_config)

    def _make_signal(
        self,
        action=TradeAction.BUY,
        position_size=700,
        symbol="AAPL",
    ) -> TradeSignal:
        return TradeSignal(
            signal_id="test-signal",
            article_id="test-article",
            symbol=symbol,
            action=action,
            conviction=82,
            sentiment=Sentiment.BULLISH,
            position_size_usd=position_size,
            reasoning="Test",
        )

    def test_approve_valid_buy(self, risk_mgr):
        signal = self._make_signal()
        approved, reason = risk_mgr.approve_signal(signal, 0, 10000)
        assert approved is True
        assert reason == "approved"

    def test_sell_always_approved(self, risk_mgr):
        signal = self._make_signal(action=TradeAction.SELL)
        approved, reason = risk_mgr.approve_signal(signal, 10, 100)
        assert approved is True
        assert reason == "sell_always_approved"

    def test_reject_max_positions(self, risk_mgr):
        signal = self._make_signal()
        approved, reason = risk_mgr.approve_signal(signal, 10, 10000)
        assert approved is False
        assert "max_positions" in reason

    def test_reject_position_too_small(self, risk_mgr):
        signal = self._make_signal(position_size=100)
        approved, reason = risk_mgr.approve_signal(signal, 0, 10000)
        assert approved is False
        assert "below_minimum" in reason

    def test_reject_position_too_large(self, risk_mgr):
        signal = self._make_signal(position_size=5000)
        approved, reason = risk_mgr.approve_signal(signal, 0, 10000)
        assert approved is False
        assert "above_maximum" in reason

    def test_reject_insufficient_buying_power(self, risk_mgr):
        signal = self._make_signal(position_size=700)
        approved, reason = risk_mgr.approve_signal(signal, 0, 500)
        assert approved is False
        assert "insufficient" in reason

    def test_daily_loss_limit(self, risk_mgr):
        """Should halt trading after daily loss limit breached."""
        today = date.today()
        # Capital is 10000, daily limit is 5% = $500
        risk_mgr.record_realized_pnl(-300, today)
        halted, _ = risk_mgr.is_trading_halted()
        assert halted is False

        risk_mgr.record_realized_pnl(-250, today)  # Total -550, limit is -500
        halted, reason = risk_mgr.is_trading_halted()
        assert halted is True
        assert "daily_loss_limit" in reason

    def test_monthly_loss_limit(self, risk_mgr):
        """Should halt trading after monthly loss limit breached."""
        today = date.today()
        # Capital is 10000, monthly limit is 10% = $1000
        risk_mgr.record_realized_pnl(-600, today)
        risk_mgr.record_realized_pnl(-500, today)  # Total -1100, limit is -1000
        halted, reason = risk_mgr.is_trading_halted()
        assert halted is True
        assert "loss_limit" in reason

    def test_pnl_tracking(self, risk_mgr):
        today = date.today()
        risk_mgr.record_realized_pnl(100, today)
        risk_mgr.record_realized_pnl(-50, today)
        assert risk_mgr.get_daily_pnl(today) == 50

    def test_state_roundtrip(self, risk_mgr):
        """State serialization and restoration should preserve data."""
        today = date.today()
        risk_mgr.record_realized_pnl(100, today)
        risk_mgr.record_realized_pnl(-25, today)

        state = risk_mgr.to_state_dict()

        new_mgr = RiskManager.__new__(RiskManager)
        new_mgr._risk = risk_mgr._risk
        new_mgr._strategy = risk_mgr._strategy
        new_mgr._daily_pnl = {}
        new_mgr._monthly_pnl = {}
        new_mgr.restore_from_state(state)

        assert new_mgr.get_daily_pnl(today) == 75
