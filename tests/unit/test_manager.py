"""Tests for position manager."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenomics.models import Sentiment, TradeAction, TradeSignal
from tokenomics.portfolio.manager import PositionManager


class TestPositionManager:
    @pytest.fixture
    def manager(self, test_config):
        with patch("tokenomics.portfolio.manager.get_trade_logger"):
            mock_broker = MagicMock()
            return PositionManager(test_config, mock_broker)

    def _make_signal(self, symbol="AAPL"):
        return TradeSignal(
            signal_id="test-signal",
            article_id="article-1",
            symbol=symbol,
            action=TradeAction.BUY,
            conviction=80,
            sentiment=Sentiment.BULLISH,
            position_size_usd=700,
            reasoning="Test",
        )

    def test_open_position(self, manager):
        signal = self._make_signal()
        pos = manager.open_position(signal, "order-1", 245.50, 2.85)

        assert pos.symbol == "AAPL"
        assert pos.entry_price == 245.50
        assert pos.quantity == 2.85
        assert pos.status == "open"
        assert manager.get_open_count() == 1
        assert "AAPL" in manager.get_open_symbols()

    def test_close_position(self, manager):
        signal = self._make_signal()
        manager.open_position(signal, "order-1", 245.50, 2.85)

        closed = manager.close_position("AAPL", 250.00, "take_profit")
        assert closed is not None
        assert closed.status == "closed_take_profit"
        assert closed.exit_price == 250.00
        assert closed.pnl_usd == pytest.approx((250 - 245.50) * 2.85, abs=0.01)
        assert closed.pnl_pct == pytest.approx((250 - 245.50) / 245.50, abs=0.001)
        assert manager.get_open_count() == 0

    def test_close_nonexistent_returns_none(self, manager):
        result = manager.close_position("AAPL", 250, "stop_loss")
        assert result is None

    def test_check_exits_stop_loss(self, manager):
        signal = self._make_signal()
        pos = manager.open_position(signal, "order-1", 100.00, 5.0)
        # Stop loss at 100 * (1 - 0.025) = 97.50

        exits = manager.check_exits({"AAPL": 97.00})
        assert len(exits) == 1
        assert exits[0] == ("AAPL", "stop_loss", 97.00)

    def test_check_exits_take_profit(self, manager):
        signal = self._make_signal()
        manager.open_position(signal, "order-1", 100.00, 5.0)
        # Take profit at 100 * (1 + 0.06) = 106.00

        exits = manager.check_exits({"AAPL": 107.00})
        assert len(exits) == 1
        assert exits[0] == ("AAPL", "take_profit", 107.00)

    def test_check_exits_max_hold(self, manager):
        signal = self._make_signal()
        pos = manager.open_position(signal, "order-1", 100.00, 5.0)
        # Force max hold date to the past
        pos.max_hold_date = datetime.now(timezone.utc) - timedelta(days=1)
        manager._positions["AAPL"] = pos

        exits = manager.check_exits({"AAPL": 101.00})
        assert len(exits) == 1
        assert exits[0] == ("AAPL", "max_hold", 101.00)

    def test_check_exits_no_exit(self, manager):
        signal = self._make_signal()
        manager.open_position(signal, "order-1", 100.00, 5.0)

        # Price within bounds
        exits = manager.check_exits({"AAPL": 101.00})
        assert len(exits) == 0

    def test_multiple_positions(self, manager):
        for symbol in ["AAPL", "MSFT", "GOOG"]:
            signal = self._make_signal(symbol)
            manager.open_position(signal, f"order-{symbol}", 100, 5)

        assert manager.get_open_count() == 3
        assert manager.get_open_symbols() == {"AAPL", "MSFT", "GOOG"}

    def test_portfolio_stats(self, manager):
        signal = self._make_signal()
        manager.open_position(signal, "order-1", 100.00, 5.0)
        manager.close_position("AAPL", 105.00, "take_profit")

        signal2 = self._make_signal("MSFT")
        manager.open_position(signal2, "order-2", 200.00, 3.0)
        manager.close_position("MSFT", 195.00, "stop_loss")

        stats = manager.get_portfolio_stats()
        assert stats["total_closed"] == 2
        assert stats["win_rate"] == 0.5  # 1 win, 1 loss

    def test_state_roundtrip(self, manager, test_config):
        signal = self._make_signal()
        manager.open_position(signal, "order-1", 245.50, 2.85)

        state = manager.to_state_dict()

        with patch("tokenomics.portfolio.manager.get_trade_logger"):
            new_manager = PositionManager(test_config, MagicMock())
            new_manager.restore_from_state(state)

        assert new_manager.get_open_count() == 1
        assert "AAPL" in new_manager.get_open_symbols()
        pos = new_manager.get_position("AAPL")
        assert pos.entry_price == 245.50

    def test_adopt_broker_positions(self, manager):
        """Should adopt untracked broker positions with correct fields."""
        broker_positions = [
            {
                "symbol": "AAPL",
                "qty": 3.2,
                "avg_entry_price": 245.50,
                "current_price": 248.00,
                "market_value": 793.60,
                "unrealized_pl": 8.00,
                "unrealized_plpc": 0.0102,
            }
        ]
        adopted = manager.adopt_broker_positions(broker_positions)

        assert adopted == ["AAPL"]
        assert manager.get_open_count() == 1
        pos = manager.get_position("AAPL")
        assert pos is not None
        assert pos.entry_price == 245.50
        assert pos.quantity == 3.2
        assert pos.signal is None
        assert pos.alpaca_order_id == "adopted"
        assert pos.stop_loss_price == pytest.approx(245.50 * (1 - 0.025), abs=0.01)
        assert pos.take_profit_price == pytest.approx(245.50 * (1 + 0.06), abs=0.01)
        assert pos.status == "open"

    def test_adopt_skips_already_tracked(self, manager):
        """Should not adopt positions that are already tracked locally."""
        signal = self._make_signal("AAPL")
        manager.open_position(signal, "order-1", 245.50, 2.85)

        broker_positions = [
            {
                "symbol": "AAPL",
                "qty": 3.2,
                "avg_entry_price": 245.50,
                "current_price": 248.00,
                "market_value": 793.60,
                "unrealized_pl": 8.00,
                "unrealized_plpc": 0.0102,
            }
        ]
        adopted = manager.adopt_broker_positions(broker_positions)

        assert adopted == []
        assert manager.get_open_count() == 1

    def test_reconcile_adopts_untracked(self, manager):
        """Reconciliation should adopt broker positions not tracked locally."""
        manager._broker.get_open_positions.return_value = [
            {
                "symbol": "MSFT",
                "qty": 2.0,
                "avg_entry_price": 400.00,
                "current_price": 405.00,
                "market_value": 810.00,
                "unrealized_pl": 10.00,
                "unrealized_plpc": 0.0125,
            }
        ]

        warnings = manager.reconcile_with_broker()

        assert manager.get_open_count() == 1
        assert "MSFT" in manager.get_open_symbols()
        assert any("Adopted" in w for w in warnings)

    def test_adopted_position_exits(self, manager):
        """Adopted positions should trigger stop-loss/take-profit exits."""
        broker_positions = [
            {
                "symbol": "GOOG",
                "qty": 1.0,
                "avg_entry_price": 100.00,
                "current_price": 95.00,
                "market_value": 95.00,
                "unrealized_pl": -5.00,
                "unrealized_plpc": -0.05,
            }
        ]
        manager.adopt_broker_positions(broker_positions)

        # Stop loss at 100 * (1 - 0.025) = 97.50
        exits = manager.check_exits({"GOOG": 97.00})
        assert len(exits) == 1
        assert exits[0] == ("GOOG", "stop_loss", 97.00)

    def test_adopted_position_state_roundtrip(self, manager, test_config):
        """Adopted positions (signal=None) should serialize/deserialize correctly."""
        broker_positions = [
            {
                "symbol": "TSLA",
                "qty": 5.0,
                "avg_entry_price": 200.00,
                "current_price": 210.00,
                "market_value": 1050.00,
                "unrealized_pl": 50.00,
                "unrealized_plpc": 0.05,
            }
        ]
        manager.adopt_broker_positions(broker_positions)

        state = manager.to_state_dict()

        with patch("tokenomics.portfolio.manager.get_trade_logger"):
            new_manager = PositionManager(test_config, MagicMock())
            new_manager.restore_from_state(state)

        assert new_manager.get_open_count() == 1
        pos = new_manager.get_position("TSLA")
        assert pos is not None
        assert pos.signal is None
        assert pos.entry_price == 200.00
