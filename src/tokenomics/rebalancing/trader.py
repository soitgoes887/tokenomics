"""Trade generation - convert target weights to trade orders."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Trade:
    """A single trade to execute."""

    symbol: str
    side: TradeSide
    shares: int
    notional_usd: float
    current_weight: float
    target_weight: float
    reason: str


@dataclass
class TradeList:
    """Complete list of trades for rebalancing."""

    sells: list[Trade]
    buys: list[Trade]
    skipped_count: int
    total_turnover_usd: float


def generate_trades(
    targets: dict[str, float],
    current_holdings: dict[str, float],  # symbol -> current weight
    current_prices: dict[str, float],  # symbol -> price per share
    portfolio_value: float,
    rebalance_threshold_pct: float = 20.0,
    min_trade_usd: float = 100.0,
) -> TradeList:
    """Generate trade list to rebalance from current to target weights.

    Args:
        targets: Target weights per symbol (0.0 to 1.0)
        current_holdings: Current weights per symbol
        current_prices: Current price per share
        portfolio_value: Total portfolio value in USD
        rebalance_threshold_pct: Only trade if deviation > this % (relative)
        min_trade_usd: Skip trades smaller than this

    Returns:
        TradeList with sells and buys
    """
    sells: list[Trade] = []
    buys: list[Trade] = []
    skipped_count = 0

    # All symbols we need to consider
    all_symbols = set(targets.keys()) | set(current_holdings.keys())

    for symbol in all_symbols:
        target_weight = targets.get(symbol, 0.0)
        current_weight = current_holdings.get(symbol, 0.0)

        # Calculate delta
        delta_weight = target_weight - current_weight
        delta_usd = delta_weight * portfolio_value

        # Skip if no price available
        price = current_prices.get(symbol)
        if price is None or price <= 0:
            if abs(delta_weight) > 0.001:  # Only log if significant
                logger.warning(
                    "trader.no_price",
                    symbol=symbol,
                    target_weight=round(target_weight * 100, 2),
                )
            skipped_count += 1
            continue

        # Check relative deviation threshold
        if target_weight > 0:
            relative_deviation = abs(delta_weight / target_weight) * 100
        else:
            relative_deviation = 100.0 if current_weight > 0 else 0.0

        # Skip if deviation is below threshold (unless we're exiting entirely)
        if relative_deviation < rebalance_threshold_pct and target_weight > 0:
            logger.debug(
                "trader.skip_below_threshold",
                symbol=symbol,
                deviation_pct=round(relative_deviation, 1),
                threshold=rebalance_threshold_pct,
            )
            skipped_count += 1
            continue

        # Skip if trade is too small
        if abs(delta_usd) < min_trade_usd:
            logger.debug(
                "trader.skip_small_trade",
                symbol=symbol,
                trade_usd=round(delta_usd, 2),
                min_trade=min_trade_usd,
            )
            skipped_count += 1
            continue

        # Calculate shares (round down for buys, up for sells to be conservative)
        shares = int(abs(delta_usd) / price)

        if shares == 0:
            skipped_count += 1
            continue

        if delta_weight > 0:
            # BUY
            reason = f"Increase from {current_weight*100:.1f}% to {target_weight*100:.1f}%"
            if current_weight == 0:
                reason = f"New position at {target_weight*100:.1f}%"

            trade = Trade(
                symbol=symbol,
                side=TradeSide.BUY,
                shares=shares,
                notional_usd=shares * price,
                current_weight=current_weight,
                target_weight=target_weight,
                reason=reason,
            )
            buys.append(trade)

            logger.info(
                "trader.buy_order",
                symbol=symbol,
                shares=shares,
                notional_usd=round(shares * price, 2),
                reason=reason,
            )
        else:
            # SELL
            reason = f"Decrease from {current_weight*100:.1f}% to {target_weight*100:.1f}%"
            if target_weight == 0:
                reason = f"Exit position (score dropped)"

            trade = Trade(
                symbol=symbol,
                side=TradeSide.SELL,
                shares=shares,
                notional_usd=shares * price,
                current_weight=current_weight,
                target_weight=target_weight,
                reason=reason,
            )
            sells.append(trade)

            logger.info(
                "trader.sell_order",
                symbol=symbol,
                shares=shares,
                notional_usd=round(shares * price, 2),
                reason=reason,
            )

    # Calculate total turnover
    total_turnover = sum(t.notional_usd for t in sells + buys)

    logger.info(
        "trader.trade_list_generated",
        sells=len(sells),
        buys=len(buys),
        skipped=skipped_count,
        turnover_usd=round(total_turnover, 2),
        turnover_pct=round(total_turnover / portfolio_value * 100, 1) if portfolio_value > 0 else 0,
    )

    return TradeList(
        sells=sells,
        buys=buys,
        skipped_count=skipped_count,
        total_turnover_usd=total_turnover,
    )


def get_current_prices(symbols: list[str], broker) -> dict[str, float]:
    """Fetch current prices for symbols from broker.

    Args:
        symbols: List of symbols to get prices for
        broker: BrokerProvider instance

    Returns:
        Dict mapping symbol to current price
    """
    prices = {}

    for symbol in symbols:
        try:
            quote = broker.get_last_quote(symbol)
            if quote and quote.ask_price > 0:
                # Use ask price for buys (conservative)
                prices[symbol] = float(quote.ask_price)
            elif quote and quote.bid_price > 0:
                prices[symbol] = float(quote.bid_price)
        except Exception as e:
            logger.warning(
                "trader.price_fetch_error",
                symbol=symbol,
                error=str(e),
            )

    return prices
