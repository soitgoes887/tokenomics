"""Alpaca broker interface for order execution and position queries."""

import math

import structlog
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from tenacity import retry, stop_after_attempt, wait_exponential

from tokenomics.config import AppConfig, Secrets
from tokenomics.models import TradeSignal
from tokenomics.trading.base import BrokerProvider

logger = structlog.get_logger(__name__)


class OrderError(Exception):
    """Raised when an order is rejected or fails."""


CRYPTO_SUFFIXES = ("USD", "USDT", "BTC", "ETH")


def _is_crypto(symbol: str) -> bool:
    """Check if a symbol is a crypto pair (e.g. BTCUSD, BCHUSD)."""
    return any(symbol.endswith(suffix) for suffix in CRYPTO_SUFFIXES) and "/" not in symbol and len(symbol) > 4


class AlpacaBrokerProvider(BrokerProvider):
    """Handles all interactions with Alpaca Trading API."""

    def __init__(self, config: AppConfig, secrets: Secrets, *,
                 alpaca_api_key: str | None = None,
                 alpaca_secret_key: str | None = None):
        self._config = config.trading
        self._api_key = alpaca_api_key or secrets.alpaca_api_key
        self._secret_key = alpaca_secret_key or secrets.alpaca_secret_key
        self._client = TradingClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=config.trading.paper,
        )

    def _time_in_force(self, symbol: str) -> TimeInForce:
        """Crypto requires GTC; equities use DAY."""
        return TimeInForce.GTC if _is_crypto(symbol) else TimeInForce.DAY

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def submit_buy_order(self, signal: TradeSignal) -> str:
        """Submit a market buy order. Returns the Alpaca order ID.

        Tries notional (dollar amount) order first. Falls back to whole-share
        qty order if the asset is not fractionable.
        """
        try:
            request = MarketOrderRequest(
                symbol=signal.symbol,
                notional=round(signal.position_size_usd, 2),
                side=OrderSide.BUY,
                time_in_force=self._time_in_force(signal.symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=signal.symbol,
                notional=signal.position_size_usd,
                side="BUY",
            )

            return str(order.id)

        except Exception as e:
            if "not fractionable" in str(e):
                return self._submit_whole_share_buy(signal)
            logger.error(
                "broker.order_failed",
                symbol=signal.symbol,
                error=str(e),
            )
            raise OrderError(f"Buy order failed for {signal.symbol}: {e}") from e

    def _submit_whole_share_buy(self, signal: TradeSignal) -> str:
        """Fall back to whole-share buy when asset is not fractionable."""
        try:
            position = self.get_position(signal.symbol)
            if position:
                price = position["current_price"]
            else:
                # Use latest trade to get current price
                from alpaca.data.requests import StockLatestTradeRequest
                from alpaca.data.historical import StockHistoricalDataClient

                data_client = StockHistoricalDataClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                )
                trade = data_client.get_stock_latest_trade(
                    StockLatestTradeRequest(symbol_or_symbols=signal.symbol)
                )
                price = float(trade[signal.symbol].price)

            qty = math.floor(signal.position_size_usd / price)
            if qty < 1:
                raise OrderError(
                    f"Cannot buy {signal.symbol}: price ${price:.2f} exceeds "
                    f"position size ${signal.position_size_usd:.2f}"
                )

            request = MarketOrderRequest(
                symbol=signal.symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=self._time_in_force(signal.symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=signal.symbol,
                qty=qty,
                side="BUY",
                fallback="whole_share",
            )

            return str(order.id)

        except OrderError:
            raise
        except Exception as e:
            logger.error(
                "broker.order_failed",
                symbol=signal.symbol,
                error=str(e),
            )
            raise OrderError(f"Buy order failed for {signal.symbol}: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def submit_buy_order_qty(self, symbol: str, quantity: int) -> str:
        """Submit a market buy order by share quantity. Returns order ID."""
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.BUY,
                time_in_force=self._time_in_force(symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=symbol,
                qty=quantity,
                side="BUY",
            )

            return str(order.id)

        except Exception as e:
            logger.error(
                "broker.order_failed",
                symbol=symbol,
                error=str(e),
            )
            raise OrderError(f"Buy order failed for {symbol}: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def submit_buy_order_notional(self, symbol: str, notional_usd: float) -> str:
        """Submit a market buy order by dollar amount (supports fractional shares).

        Args:
            symbol: Stock symbol
            notional_usd: Dollar amount to invest

        Returns:
            Order ID
        """
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional_usd, 2),
                side=OrderSide.BUY,
                time_in_force=self._time_in_force(symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=symbol,
                notional_usd=round(notional_usd, 2),
                side="BUY",
            )

            return str(order.id)

        except Exception as e:
            # Fall back to whole shares if not fractionable
            if "not fractionable" in str(e).lower():
                return self._submit_whole_share_buy_notional(symbol, notional_usd)
            logger.error(
                "broker.order_failed",
                symbol=symbol,
                error=str(e),
            )
            raise OrderError(f"Buy order failed for {symbol}: {e}") from e

    def _submit_whole_share_buy_notional(self, symbol: str, notional_usd: float) -> str:
        """Fall back to whole-share buy when asset is not fractionable."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        try:
            data_client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
            trade = data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol)
            )
            price = float(trade[symbol].price)

            qty = math.floor(notional_usd / price)
            if qty < 1:
                raise OrderError(
                    f"Cannot buy {symbol}: price ${price:.2f} exceeds "
                    f"notional ${notional_usd:.2f}"
                )

            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=self._time_in_force(symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=symbol,
                qty=qty,
                side="BUY",
                fallback="whole_share",
            )

            return str(order.id)

        except OrderError:
            raise
        except Exception as e:
            logger.error(
                "broker.order_failed",
                symbol=symbol,
                error=str(e),
            )
            raise OrderError(f"Buy order failed for {symbol}: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def submit_sell_order_notional(self, symbol: str, notional_usd: float) -> str:
        """Submit a market sell order by dollar amount (supports fractional shares).

        Args:
            symbol: Stock symbol
            notional_usd: Dollar amount to sell

        Returns:
            Order ID
        """
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional_usd, 2),
                side=OrderSide.SELL,
                time_in_force=self._time_in_force(symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=symbol,
                notional_usd=round(notional_usd, 2),
                side="SELL",
            )

            return str(order.id)

        except Exception as e:
            logger.error(
                "broker.sell_failed",
                symbol=symbol,
                error=str(e),
            )
            raise OrderError(f"Sell order failed for {symbol}: {e}") from e

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def submit_sell_order(self, symbol: str, quantity: float) -> str:
        """Submit a market sell order to close a position. Returns order ID."""
        try:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=OrderSide.SELL,
                time_in_force=self._time_in_force(symbol),
            )

            order = self._client.submit_order(request)

            logger.info(
                "broker.order_submitted",
                order_id=str(order.id),
                symbol=symbol,
                qty=quantity,
                side="SELL",
            )

            return str(order.id)

        except Exception as e:
            logger.error(
                "broker.sell_failed",
                symbol=symbol,
                error=str(e),
            )
            raise OrderError(f"Sell order failed for {symbol}: {e}") from e

    def get_account(self) -> dict:
        """Get current account info."""
        account = self._client.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "status": account.status,
        }

    def get_open_positions(self) -> list[dict]:
        """Get all open positions from Alpaca."""
        positions = self._client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
            }
            for p in positions
        ]

    def get_position(self, symbol: str) -> dict | None:
        """Get a specific position, or None if not held."""
        try:
            p = self._client.get_open_position(symbol)
            return {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
        except Exception:
            return None

    def is_market_open(self) -> bool:
        """Check if the US market is currently open."""
        clock = self._client.get_clock()
        return clock.is_open

    def get_clock(self) -> dict:
        """Get market clock details."""
        clock = self._client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
        }
