"""Per-symbol backtest runner and portfolio-level aggregation.

Flow:
  1. For each symbol: merge OHLCV + signal → run backtesting.py → collect stats
  2. Aggregate per-symbol equity curves into a combined portfolio equity curve
  3. Compute portfolio-level KPIs (total return, CAGR, Sharpe, max drawdown)
"""

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import structlog
from backtesting import Backtest

from tokenomics.backtesting.strategies import ScoreSignalStrategy

logger = structlog.get_logger(__name__)

# Per-symbol initial cash used in backtesting.py simulation
_PER_SYMBOL_CASH = 10_000.0
# Commission per trade (0.1% = typical retail broker)
_COMMISSION = 0.001


def run_symbol(
    symbol: str,
    ohlcv: pd.DataFrame,
    signal: pd.Series,
) -> Optional[dict]:
    """Run backtesting.py on a single symbol.

    Returns a flat dict of KPIs plus the equity curve Series, or None if the
    backtest fails (e.g. no data overlap, all-zero signal).
    """
    # Align signal to OHLCV index
    sig = signal.reindex(ohlcv.index, method="ffill").fillna(0.0)

    if sig.sum() == 0:
        logger.debug("runner.no_signal", symbol=symbol)
        return None

    data = ohlcv.copy()
    data["Signal"] = sig.values

    try:
        bt = Backtest(
            data,
            ScoreSignalStrategy,
            cash=_PER_SYMBOL_CASH,
            commission=_COMMISSION,
            exclusive_orders=True,
        )
        stats = bt.run()
    except Exception as e:
        logger.warning("runner.backtest_error", symbol=symbol, error=str(e))
        return None

    equity_curve: pd.Series = stats._equity_curve["Equity"]

    return {
        "symbol": symbol,
        "return_pct": round(float(stats.get("Return [%]", 0)), 2),
        "return_ann_pct": round(float(stats.get("Return (Ann.) [%]", 0)), 2),
        "volatility_ann_pct": round(float(stats.get("Volatility (Ann.) [%]", 0)), 2),
        "sharpe": round(float(stats.get("Sharpe Ratio", 0) or 0), 3),
        "max_drawdown_pct": round(float(stats.get("Max. Drawdown [%]", 0)), 2),
        "trades": int(stats.get("# Trades", 0)),
        "win_rate_pct": round(float(stats.get("Win Rate [%]", 0) or 0), 1),
        "exposure_pct": round(float(stats.get("Exposure Time [%]", 0)), 1),
        "_equity": equity_curve,
    }


def run_profile(
    ohlcv_data: dict[str, pd.DataFrame],
    signals: dict[str, pd.Series],
) -> tuple[list[dict], dict]:
    """Run per-symbol backtests and aggregate into portfolio stats.

    Args:
        ohlcv_data: {symbol: OHLCV DataFrame}
        signals:    {symbol: signal Series}

    Returns:
        (per_symbol_results, portfolio_stats)
    """
    per_symbol: list[dict] = []
    equity_curves: dict[str, pd.Series] = {}

    symbols_to_run = [s for s in signals if signals[s].sum() > 0 and s in ohlcv_data]

    for symbol in symbols_to_run:
        result = run_symbol(symbol, ohlcv_data[symbol], signals[symbol])
        if result is not None:
            equity_curves[symbol] = result.pop("_equity")
            per_symbol.append(result)

    portfolio = _aggregate_portfolio(equity_curves)

    logger.info(
        "runner.profile_complete",
        symbols_run=len(symbols_to_run),
        symbols_ok=len(per_symbol),
        portfolio_return=portfolio.get("total_return_pct"),
        portfolio_sharpe=portfolio.get("sharpe"),
    )

    return per_symbol, portfolio


def _aggregate_portfolio(equity_curves: dict[str, pd.Series]) -> dict:
    """Combine per-symbol equity curves into portfolio-level KPIs.

    Each symbol gets an equal initial allocation of _PER_SYMBOL_CASH.
    The combined equity curve is the sum of all per-symbol curves.
    """
    if not equity_curves:
        return {}

    # Normalise each curve to start at _PER_SYMBOL_CASH
    normalised = {}
    for sym, eq in equity_curves.items():
        if eq.iloc[0] > 0:
            normalised[sym] = eq / eq.iloc[0] * _PER_SYMBOL_CASH

    if not normalised:
        return {}

    combined = (
        pd.concat(normalised.values(), axis=1)
        .ffill()
        .fillna(_PER_SYMBOL_CASH)
    )
    portfolio = combined.sum(axis=1)

    total_initial = _PER_SYMBOL_CASH * len(normalised)
    total_final = portfolio.iloc[-1]
    years = (portfolio.index[-1] - portfolio.index[0]).days / 365.25

    daily_ret = portfolio.pct_change().dropna()
    mean_ret = daily_ret.mean()
    std_ret = daily_ret.std()

    total_return = (total_final - total_initial) / total_initial * 100
    cagr = ((total_final / total_initial) ** (1 / max(years, 0.01)) - 1) * 100
    vol_ann = std_ret * np.sqrt(252) * 100
    sharpe = (mean_ret * 252) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

    rolling_max = portfolio.cummax()
    max_dd = ((portfolio - rolling_max) / rolling_max * 100).min()

    return {
        "symbols": len(normalised),
        "years": round(years, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "volatility_ann_pct": round(vol_ann, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
    }
