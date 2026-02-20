"""
Tokenomics V2 Base vs S&P 100 performance comparison.

Fetches portfolio equity from Alpaca and S&P 500 (via SPY ETF) daily closes
via Alpaca IEX feed, aligns them on shared trading days, and plots a normalised
return comparison.

Usage:
    source .venv/bin/activate
    python scripts/portfolio_vs_sp100.py

Credentials are loaded from .env (ALPACA_API_KEY / ALPACA_SECRET_KEY).
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv

# Load .env from project root (one level up from scripts/)
load_dotenv(Path(__file__).parent.parent / ".env")

API_KEY = os.environ["ALPACA_API_KEY"]
SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]

start = date(2026, 2, 14)
end = date.today()

# We request bars from 1 day earlier because Alpaca daily-bar timestamps
# represent the session open date, while portfolio-history timestamps land
# on the following calendar day (the post-close snapshot).  Fetching one
# extra day ensures the bar for start's session is included.
bar_start = start - timedelta(days=3)  # cover weekends / holidays


# ---------------------------------------------------------------------------
# Portfolio history from Alpaca
# ---------------------------------------------------------------------------
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetPortfolioHistoryRequest

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)

request = GetPortfolioHistoryRequest(
    start=start.isoformat(),
    end=end.isoformat(),
    timeframe="1D",
    intraday_reporting="market_hours",
)
history = trading_client.get_portfolio_history(history_filter=request)

# Portfolio timestamps are unix seconds.  Each stamp (e.g. 2026-02-14 01:00 UTC)
# is the post-close snapshot — it belongs to the previous trading session.
# We convert directly to dates which already gives us the "next calendar day"
# label that Alpaca uses.
portfolio_raw = pd.Series(
    history.equity,
    index=[pd.Timestamp(ts, unit="s").date() for ts in history.timestamp],
    name="portfolio",
).dropna().loc[lambda s: s > 0]
portfolio_raw.index = pd.DatetimeIndex(portfolio_raw.index)

if portfolio_raw.empty:
    print("No portfolio data returned for the requested period.")
    sys.exit(1)

print(f"Portfolio data points: {len(portfolio_raw)}  "
      f"({portfolio_raw.index[0].date()} -> {portfolio_raw.index[-1].date()})")


# ---------------------------------------------------------------------------
# S&P 500 daily closes via Alpaca IEX feed (SPY — most liquid ETF)
# ---------------------------------------------------------------------------
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

bars_request = StockBarsRequest(
    symbol_or_symbols="SPY",
    timeframe=TimeFrame(1, TimeFrameUnit.Day),
    start=bar_start.isoformat(),
    end=end.isoformat(),
    feed="iex",
)
bars = data_client.get_stock_bars(bars_request)
sp_df = bars.df

if sp_df.empty:
    print("No SPY bar data returned.")
    sys.exit(1)

if isinstance(sp_df.index, pd.MultiIndex):
    sp_df = sp_df.droplevel(0)

# Bar timestamps (e.g. 2026-02-13 05:00 UTC) represent the session open date.
# Shift forward by 1 calendar day so they match the portfolio's labelling
# (portfolio's "Feb 14" = market session that opened Feb 13).
sp_raw = sp_df["close"].dropna().rename("sp500")
sp_raw.index = pd.DatetimeIndex([(ts.date() + timedelta(days=1)) for ts in sp_raw.index])

print(f"SPY data points:       {len(sp_raw)}  "
      f"({sp_raw.index[0].date()} -> {sp_raw.index[-1].date()})")


# ---------------------------------------------------------------------------
# Align on shared dates (inner join), filter to >= start
# ---------------------------------------------------------------------------
aligned = (
    pd.concat([portfolio_raw, sp_raw], axis=1, sort=True)
    .dropna()
    .loc[lambda df: df.index >= pd.Timestamp(start)]
)

if len(aligned) < 2:
    print(f"\nOnly {len(aligned)} shared date(s) — need at least 2.")
    print("Portfolio dates:", sorted(portfolio_raw.index.date.tolist()))
    print("SPY dates:     ", sorted(sp_raw.index.date.tolist()))
    sys.exit(1)

print(f"\nShared trading days:   {len(aligned)}  "
      f"({aligned.index[0].date()} -> {aligned.index[-1].date()})")

# Percentage return from day-0
portfolio_pct = (aligned["portfolio"] / aligned["portfolio"].iloc[0] - 1) * 100
sp_pct = (aligned["sp500"] / aligned["sp500"].iloc[0] - 1) * 100

portfolio_growth = portfolio_pct.iloc[-1]
sp_growth = sp_pct.iloc[-1]

print(f"Tokenomics V2 Base growth: {portfolio_growth:+.2f}%")
print(f"S&P 500 (SPY) growth:      {sp_growth:+.2f}%")


# ---------------------------------------------------------------------------
# Plot — categorical x-axis (trading days only, no weekend gaps)
# ---------------------------------------------------------------------------
labels = [d.strftime("%b %d") for d in aligned.index]
x = list(range(len(labels)))

fig, ax = plt.subplots(figsize=(11, 6))

ax.plot(x, portfolio_pct.values, marker="o", linewidth=2,
        label="Tokenomics V2 Base", color="#1f77b4")
ax.plot(x, sp_pct.values, marker="s", linewidth=2,
        label="S&P 500 (SPY)", color="#ff7f0e")

ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
ax.fill_between(x, portfolio_pct.values, 0, alpha=0.08, color="#1f77b4")

ax.set_xticks(x)
ax.set_xticklabels(labels, rotation=45, ha="right")

ax.set_title(
    f"Tokenomics V2 Base vs S&P 500 — {start.strftime('%b %d')} onwards\n"
    f"Tokenomics V2 Base: {portfolio_growth:+.2f}%  |  S&P 500: {sp_growth:+.2f}%",
    fontsize=13,
)
ax.set_ylabel("Return (%)", fontsize=11)
ax.set_xlabel("Date", fontsize=11)
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3)

output_path = Path(__file__).parent.parent / "tokenomics_v2_base_vs_sp100.png"
plt.tight_layout()
plt.savefig(output_path, dpi=150)
print(f"\nChart saved to {output_path}")
plt.show()
