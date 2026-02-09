"""Position lifecycle management."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from tokenomics.config import AppConfig
from tokenomics.logging_config import get_trade_logger
from tokenomics.models import Position, TradeSignal
from tokenomics.trading.base import BrokerProvider

logger = structlog.get_logger(__name__)


class PositionManager:
    """Manages the lifecycle of all open positions."""

    def __init__(self, config: AppConfig, broker: BrokerProvider):
        self._config = config
        self._broker = broker
        self._positions: dict[str, Position] = {}  # symbol -> Position
        self._closed_positions: list[Position] = []
        self._trade_log = get_trade_logger()

    def open_position(
        self,
        signal: TradeSignal,
        order_id: str,
        fill_price: float,
        quantity: float,
    ) -> Position:
        """Record a new open position after order fill."""
        now = datetime.now(timezone.utc)
        # Calculate max hold date (65 trading days ~ 13 calendar weeks)
        max_hold_date = now + timedelta(weeks=13)

        position = Position(
            symbol=signal.symbol,
            alpaca_order_id=order_id,
            entry_price=fill_price,
            quantity=quantity,
            position_size_usd=fill_price * quantity,
            entry_date=now,
            signal=signal,
            stop_loss_price=fill_price * (1 - self._config.risk.stop_loss_pct),
            take_profit_price=fill_price * (1 + self._config.risk.take_profit_pct),
            max_hold_date=max_hold_date,
        )

        self._positions[signal.symbol] = position

        self._trade_log.info(
            "trade.opened",
            symbol=position.symbol,
            entry_price=position.entry_price,
            quantity=position.quantity,
            position_size=position.position_size_usd,
            stop_loss=position.stop_loss_price,
            take_profit=position.take_profit_price,
            max_hold_date=position.max_hold_date.isoformat(),
            order_id=order_id,
            conviction=signal.conviction,
            article_id=signal.article_id,
        )

        return position

    def check_exits(
        self, current_prices: dict[str, float]
    ) -> list[tuple[str, str, float]]:
        """
        Check all open positions against exit criteria.
        Returns list of (symbol, reason, current_price) tuples.
        """
        exits = []
        now = datetime.now(timezone.utc)

        for symbol, pos in self._positions.items():
            price = current_prices.get(symbol)
            if price is None:
                continue

            # Stop loss
            if price <= pos.stop_loss_price:
                exits.append((symbol, "stop_loss", price))
                continue

            # Take profit
            if price >= pos.take_profit_price:
                exits.append((symbol, "take_profit", price))
                continue

            # Max hold period
            if now >= pos.max_hold_date:
                exits.append((symbol, "max_hold", price))
                continue

        return exits

    def close_position(
        self, symbol: str, exit_price: float, reason: str
    ) -> Position | None:
        """Mark a position as closed and calculate P&L."""
        pos = self._positions.pop(symbol, None)
        if pos is None:
            logger.warning("position.close_not_found", symbol=symbol)
            return None

        pos.exit_price = exit_price
        pos.exit_date = datetime.now(timezone.utc)
        pos.status = f"closed_{reason}"
        pos.pnl_usd = (exit_price - pos.entry_price) * pos.quantity
        pos.pnl_pct = (exit_price - pos.entry_price) / pos.entry_price

        self._closed_positions.append(pos)

        self._trade_log.info(
            "trade.closed",
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl_usd=pos.pnl_usd,
            pnl_pct=f"{pos.pnl_pct:.4f}",
            reason=reason,
            hold_days=(pos.exit_date - pos.entry_date).days,
        )

        return pos

    def get_open_symbols(self) -> set[str]:
        """Return set of symbols with open positions."""
        return set(self._positions.keys())

    def get_open_count(self) -> int:
        """Return number of open positions."""
        return len(self._positions)

    def get_position(self, symbol: str) -> Position | None:
        """Get a specific open position."""
        return self._positions.get(symbol)

    def get_all_open(self) -> list[Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def reconcile_with_broker(self) -> list[str]:
        """Compare local state with Alpaca positions. Returns warnings."""
        warnings = []
        try:
            broker_positions = {
                p["symbol"]: p for p in self._broker.get_open_positions()
            }

            # Check for positions we track but Alpaca doesn't have
            for symbol in list(self._positions.keys()):
                if symbol not in broker_positions:
                    warnings.append(
                        f"Local position {symbol} not found in Alpaca"
                    )
                    logger.warning(
                        "reconcile.missing_in_broker", symbol=symbol
                    )

            # Check for positions Alpaca has but we don't track
            for symbol in broker_positions:
                if symbol not in self._positions:
                    warnings.append(
                        f"Alpaca position {symbol} not tracked locally"
                    )
                    logger.warning(
                        "reconcile.missing_locally", symbol=symbol
                    )

        except Exception as e:
            warnings.append(f"Reconciliation failed: {e}")
            logger.error("reconcile.failed", error=str(e))

        return warnings

    def get_portfolio_stats(self) -> dict:
        """Calculate aggregate portfolio statistics."""
        if not self._closed_positions:
            return {
                "open_positions": self.get_open_count(),
                "total_closed": 0,
                "total_pnl_usd": 0.0,
                "win_rate": 0.0,
            }

        wins = sum(1 for p in self._closed_positions if (p.pnl_usd or 0) > 0)
        total = len(self._closed_positions)
        total_pnl = sum(p.pnl_usd or 0 for p in self._closed_positions)

        return {
            "open_positions": self.get_open_count(),
            "total_closed": total,
            "total_pnl_usd": round(total_pnl, 2),
            "win_rate": round(wins / total, 4) if total > 0 else 0.0,
            "avg_pnl_per_trade": round(total_pnl / total, 2) if total > 0 else 0.0,
        }

    def to_state_dict(self) -> dict:
        """Serialize all positions for state persistence."""
        return {
            "positions": {
                symbol: pos.model_dump(mode="json")
                for symbol, pos in self._positions.items()
            },
            "closed_positions": [
                pos.model_dump(mode="json") for pos in self._closed_positions
            ],
        }

    def restore_from_state(self, state: dict) -> None:
        """Restore position manager from persisted state."""
        self._positions = {
            symbol: Position.model_validate(data)
            for symbol, data in state.get("positions", {}).items()
        }
        self._closed_positions = [
            Position.model_validate(data)
            for data in state.get("closed_positions", [])
        ]
        logger.info(
            "positions.restored",
            open=len(self._positions),
            closed=len(self._closed_positions),
        )
