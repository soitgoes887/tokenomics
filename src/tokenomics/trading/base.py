"""Abstract base class for broker providers."""

from abc import ABC, abstractmethod

from tokenomics.models import TradeSignal


class BrokerProvider(ABC):
    """Interface for order execution and position management."""

    @abstractmethod
    def submit_buy_order(self, signal: TradeSignal) -> str:
        """Submit a buy order. Returns order ID."""
        ...

    @abstractmethod
    def submit_buy_order_qty(self, symbol: str, quantity: int) -> str:
        """Submit a buy order by quantity. Returns order ID."""
        ...

    @abstractmethod
    def submit_sell_order(self, symbol: str, quantity: float) -> str:
        """Submit a sell order. Returns order ID."""
        ...

    @abstractmethod
    def get_account(self) -> dict:
        """Get account info (equity, cash, buying_power, status)."""
        ...

    @abstractmethod
    def get_open_positions(self) -> list[dict]:
        """Get all open positions."""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> dict | None:
        """Get a specific position, or None if not held."""
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        ...

    @abstractmethod
    def get_clock(self) -> dict:
        """Get market clock details (is_open, next_open, next_close)."""
        ...
