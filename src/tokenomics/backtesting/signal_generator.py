"""Convert Redis scores into per-symbol long/flat signal series.

⚠ This is a STATIC signal generator — it uses the CURRENT scores from Redis
   to determine which stocks are "in" the portfolio and marks them as long for
   the entire backtest period.  There is no point-in-time reconstitution.

   This means results will exhibit look-ahead bias on the portfolio composition
   (we know today which stocks scored well) but accurately reflect how those
   specific stocks would have performed historically.  It is best interpreted as
   "how have these stocks done?" not "how would the strategy have done?"

   A future improvement would snapshot scores at each historical rebalancing date
   using Finnhub historical fundamentals (requires premium tier).
"""

import pandas as pd


def build_signals(
    scores: list[tuple[str, float]],
    top_n: int,
    trading_calendar: pd.DatetimeIndex,
) -> dict[str, pd.Series]:
    """Build per-symbol signal series from a static score ranking.

    Stocks ranked in the top `top_n` get signal=1.0 throughout the period.
    All others get signal=0.0.

    Args:
        scores:            (symbol, score) pairs sorted descending — from Redis
        top_n:             Number of stocks to include in the simulated portfolio
        trading_calendar:  DatetimeIndex of trading days for the backtest period

    Returns:
        {symbol: pd.Series(float)} — 1.0 = long, 0.0 = flat
    """
    top_set = {sym for sym, _ in scores[:top_n]}
    signals: dict[str, pd.Series] = {}

    for sym, _ in scores:
        value = 1.0 if sym in top_set else 0.0
        signals[sym] = pd.Series(value, index=trading_calendar, name=sym)

    return signals


def build_trading_calendar(
    start: pd.Timestamp, end: pd.Timestamp
) -> pd.DatetimeIndex:
    """Business-day date range between start and end (inclusive)."""
    return pd.bdate_range(start=start, end=end, tz="UTC")
