"""Tests for Alpaca broker interface."""

from unittest.mock import MagicMock, patch

import pytest

from tokenomics.models import Sentiment, TradeAction, TradeSignal
from tokenomics.trading.broker import AlpacaBrokerProvider, OrderError


class TestAlpacaBrokerProvider:
    @pytest.fixture
    def broker(self, test_config, mock_secrets):
        with patch("tokenomics.trading.broker.TradingClient") as MockClient:
            b = AlpacaBrokerProvider(test_config, mock_secrets)
            b._client = MagicMock()
            return b

    def _make_signal(self, symbol="AAPL", size=700):
        return TradeSignal(
            signal_id="test",
            article_id="article-1",
            symbol=symbol,
            action=TradeAction.BUY,
            conviction=80,
            sentiment=Sentiment.BULLISH,
            position_size_usd=size,
            reasoning="Test",
        )

    def test_submit_buy_order(self, broker):
        """Should submit a market buy order and return order ID."""
        mock_order = MagicMock()
        mock_order.id = "order-123"
        broker._client.submit_order.return_value = mock_order

        signal = self._make_signal()
        order_id = broker.submit_buy_order(signal)

        assert order_id == "order-123"
        broker._client.submit_order.assert_called_once()

    def test_submit_sell_order(self, broker):
        """Should submit a market sell order."""
        mock_order = MagicMock()
        mock_order.id = "order-456"
        broker._client.submit_order.return_value = mock_order

        order_id = broker.submit_sell_order("AAPL", 3.2)
        assert order_id == "order-456"

    def test_get_account(self, broker):
        """Should return account info as a dict."""
        mock_account = MagicMock()
        mock_account.equity = "10000.00"
        mock_account.cash = "8000.00"
        mock_account.buying_power = "16000.00"
        mock_account.status = "ACTIVE"
        broker._client.get_account.return_value = mock_account

        account = broker.get_account()
        assert account["equity"] == 10000.0
        assert account["cash"] == 8000.0
        assert account["status"] == "ACTIVE"

    def test_get_open_positions(self, broker):
        """Should return positions as list of dicts."""
        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"
        mock_pos.qty = "3.2"
        mock_pos.avg_entry_price = "245.50"
        mock_pos.current_price = "248.00"
        mock_pos.market_value = "793.60"
        mock_pos.unrealized_pl = "8.00"
        mock_pos.unrealized_plpc = "0.0102"
        broker._client.get_all_positions.return_value = [mock_pos]

        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["qty"] == 3.2

    def test_is_market_open(self, broker):
        mock_clock = MagicMock()
        mock_clock.is_open = True
        broker._client.get_clock.return_value = mock_clock

        assert broker.is_market_open() is True

    def test_get_position_not_found(self, broker):
        """Should return None when position doesn't exist."""
        broker._client.get_open_position.side_effect = Exception("not found")
        result = broker.get_position("AAPL")
        assert result is None
