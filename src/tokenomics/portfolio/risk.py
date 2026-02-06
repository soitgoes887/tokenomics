"""Portfolio-level risk management rules."""

from datetime import date

import structlog

from tokenomics.config import AppConfig
from tokenomics.models import TradeAction, TradeSignal

logger = structlog.get_logger(__name__)


class RiskManager:
    """Enforces portfolio-level risk constraints."""

    def __init__(self, config: AppConfig):
        self._risk = config.risk
        self._strategy = config.strategy
        self._daily_pnl: dict[date, float] = {}
        self._monthly_pnl: dict[str, float] = {}  # "2026-02" -> pnl

    def approve_signal(
        self,
        signal: TradeSignal,
        open_position_count: int,
        current_equity: float,
    ) -> tuple[bool, str]:
        """Check if a trade signal passes all risk rules."""
        # SELL signals are always approved (we want to exit risk)
        if signal.action == TradeAction.SELL:
            return True, "sell_always_approved"

        # Check max open positions
        if open_position_count >= self._strategy.max_open_positions:
            return False, "max_positions_reached"

        # Check position size bounds
        if signal.position_size_usd < self._strategy.position_size_min_usd:
            return False, "position_size_below_minimum"
        if signal.position_size_usd > self._strategy.position_size_max_usd:
            return False, "position_size_above_maximum"

        # Check daily loss limit
        halted, reason = self.is_trading_halted()
        if halted:
            return False, reason

        # Check buying power (rough check: need enough equity for the position)
        if signal.position_size_usd > current_equity * 0.95:  # 5% buffer
            return False, "insufficient_buying_power"

        return True, "approved"

    def record_realized_pnl(self, pnl_usd: float, trade_date: date) -> None:
        """Record realized P&L for daily/monthly tracking."""
        # Daily
        self._daily_pnl[trade_date] = self._daily_pnl.get(trade_date, 0.0) + pnl_usd

        # Monthly
        month_key = trade_date.strftime("%Y-%m")
        self._monthly_pnl[month_key] = self._monthly_pnl.get(month_key, 0.0) + pnl_usd

        logger.info(
            "risk.pnl_recorded",
            pnl_usd=pnl_usd,
            daily_total=self._daily_pnl[trade_date],
            monthly_total=self._monthly_pnl[month_key],
        )

    def is_trading_halted(self) -> tuple[bool, str]:
        """Check if trading should be halted due to risk limits."""
        today = date.today()
        daily_pnl = self._daily_pnl.get(today, 0.0)
        daily_limit = -(self._strategy.capital_usd * self._risk.daily_loss_limit_pct)

        if daily_pnl <= daily_limit:
            return True, f"daily_loss_limit_breached: ${daily_pnl:.2f} <= ${daily_limit:.2f}"

        month_key = today.strftime("%Y-%m")
        monthly_pnl = self._monthly_pnl.get(month_key, 0.0)
        monthly_limit = -(self._strategy.capital_usd * self._risk.monthly_loss_limit_pct)

        if monthly_pnl <= monthly_limit:
            return True, f"monthly_loss_limit_breached: ${monthly_pnl:.2f} <= ${monthly_limit:.2f}"

        return False, ""

    def get_daily_pnl(self, day: date | None = None) -> float:
        """Get realized P&L for a specific day (default: today)."""
        return self._daily_pnl.get(day or date.today(), 0.0)

    def get_monthly_pnl(self, month_key: str | None = None) -> float:
        """Get realized P&L for a specific month (default: current month)."""
        if month_key is None:
            month_key = date.today().strftime("%Y-%m")
        return self._monthly_pnl.get(month_key, 0.0)

    def to_state_dict(self) -> dict:
        """Serialize for state persistence."""
        return {
            "daily_pnl": {str(k): v for k, v in self._daily_pnl.items()},
            "monthly_pnl": self._monthly_pnl.copy(),
        }

    def restore_from_state(self, state: dict) -> None:
        """Restore from persisted state."""
        self._daily_pnl = {
            date.fromisoformat(k): v
            for k, v in state.get("daily_pnl", {}).items()
        }
        self._monthly_pnl = state.get("monthly_pnl", {})
