"""
Post a Tokenomics weekly update to Discord with performance charts.

Fetches portfolio history for all profiles, generates comparison charts,
builds a formatted update message, and posts everything to a Discord webhook.

Usage:
    source .venv/bin/activate
    python scripts/discord_update.py              # post to Discord
    python scripts/discord_update.py --dry-run    # print message only, no post

Requires DISCORD_WEBHOOK_URL in .env.
"""

import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetPortfolioHistoryRequest

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------
PROFILES = {
    "v2": {
        "label": "Tokenomics V2 Base",
        "short_label": "V2 Base",
        "api_key_env": "ALPACA_API_KEY",
        "secret_key_env": "ALPACA_SECRET_KEY",
        "start": date(2026, 2, 14),
        "color": "#1f77b4",
    },
    "v3": {
        "label": "Tokenomics V3 Composite",
        "short_label": "V3 Composite",
        "api_key_env": "ALPACA_API_KEY_V3",
        "secret_key_env": "ALPACA_SECRET_KEY_V3",
        "start": date(2026, 2, 22),
        "color": "#2ca02c",
    },
    "v4": {
        "label": "Tokenomics V4 Regime",
        "short_label": "V4 Regime",
        "api_key_env": "ALPACA_API_KEY_V4",
        "secret_key_env": "ALPACA_SECRET_KEY_V4",
        "start": date(2026, 3, 14),
        "color": "#d62728",
    },
}


@dataclass
class ProfileResult:
    name: str
    label: str
    short_label: str
    start: date
    end: date
    trading_days: int
    portfolio_growth: float
    sp_growth: float
    portfolio_value: float
    portfolio_start_value: float
    sp_equivalent_value: float
    chart_path: Path


# ---------------------------------------------------------------------------
# Data fetching + chart generation (mirrors portfolio_vs_sp100.py logic)
# ---------------------------------------------------------------------------
def fetch_profile(name: str, profile: dict) -> ProfileResult | None:
    api_key = os.environ.get(profile["api_key_env"])
    secret_key = os.environ.get(profile["secret_key_env"])
    if not api_key or not secret_key:
        print(f"[{name}] Missing credentials, skipping.")
        return None

    start = profile["start"]
    end = date.today()
    bar_start = start - timedelta(days=3)
    label = profile["label"]
    color = profile["color"]

    # --- Portfolio history ---
    trading_client = TradingClient(api_key, secret_key, paper=True)
    request = GetPortfolioHistoryRequest(
        start=start.isoformat(),
        end=end.isoformat(),
        timeframe="1D",
        intraday_reporting="market_hours",
    )
    history = trading_client.get_portfolio_history(history_filter=request)

    portfolio_raw = pd.Series(
        history.equity,
        index=[pd.Timestamp(ts, unit="s").date() for ts in history.timestamp],
        name="portfolio",
    ).dropna().loc[lambda s: s > 0]
    portfolio_raw.index = pd.DatetimeIndex(portfolio_raw.index)

    if portfolio_raw.empty:
        print(f"[{name}] No portfolio data.")
        return None

    # --- SPY bars ---
    data_client = StockHistoricalDataClient(api_key, secret_key)
    bars_request = StockBarsRequest(
        symbol_or_symbols="SPY",
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=bar_start.isoformat(),
        end=end.isoformat(),
        feed="iex",
    )
    sp_df = data_client.get_stock_bars(bars_request).df
    if sp_df.empty:
        print(f"[{name}] No SPY data.")
        return None
    if isinstance(sp_df.index, pd.MultiIndex):
        sp_df = sp_df.droplevel(0)

    sp_raw = sp_df["close"].dropna().rename("sp500")
    sp_raw.index = pd.DatetimeIndex(
        [(ts.date() + timedelta(days=1)) for ts in sp_raw.index]
    )

    # --- Align ---
    aligned = (
        pd.concat([portfolio_raw, sp_raw], axis=1, sort=True)
        .dropna()
        .loc[lambda df: df.index >= pd.Timestamp(start)]
    )
    if len(aligned) < 2:
        print(f"[{name}] Not enough shared dates ({len(aligned)}).")
        return None

    portfolio_pct = (aligned["portfolio"] / aligned["portfolio"].iloc[0] - 1) * 100
    sp_pct = (aligned["sp500"] / aligned["sp500"].iloc[0] - 1) * 100

    portfolio_growth = portfolio_pct.iloc[-1]
    sp_growth = sp_pct.iloc[-1]
    portfolio_value = aligned["portfolio"].iloc[-1]
    portfolio_start_value = aligned["portfolio"].iloc[0]
    sp_equivalent = portfolio_start_value * (1 + sp_growth / 100)
    last_date = aligned.index[-1].date()

    print(f"[{name}] {portfolio_growth:+.2f}% vs SPY {sp_growth:+.2f}%  "
          f"(${portfolio_value:,.0f} | {len(aligned)} days)")

    # --- Chart ---
    labels = [d.strftime("%b %d") for d in aligned.index]
    x = list(range(len(labels)))

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(x, portfolio_pct.values, marker="o", linewidth=2, label=label, color=color)
    ax.plot(x, sp_pct.values, marker="s", linewidth=2,
            label="S&P 500 (SPY)", color="#ff7f0e")
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.fill_between(x, portfolio_pct.values, 0, alpha=0.08, color=color)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_title(
        f"{label} vs S&P 500 — {start.strftime('%b %d')} onwards\n"
        f"{label}: {portfolio_growth:+.2f}% (\\${portfolio_value:,.0f})  |  "
        f"S&P 500: {sp_growth:+.2f}% (\\${sp_equivalent:,.0f})",
        fontsize=13,
    )
    ax.set_ylabel("Return (%)", fontsize=11)
    ax.set_xlabel("Date", fontsize=11)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)

    chart_path = ROOT / f"tokenomics_{name}_vs_sp500.png"
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150)
    plt.close(fig)
    print(f"[{name}] Chart saved to {chart_path}")

    return ProfileResult(
        name=name,
        label=label,
        short_label=profile["short_label"],
        start=start,
        end=last_date,
        trading_days=len(aligned),
        portfolio_growth=portfolio_growth,
        sp_growth=sp_growth,
        portfolio_value=portfolio_value,
        portfolio_start_value=portfolio_start_value,
        sp_equivalent_value=sp_equivalent,
        chart_path=chart_path,
    )


# ---------------------------------------------------------------------------
# Build Discord message
# ---------------------------------------------------------------------------
def build_message(results: list[ProfileResult]) -> str:
    today = date.today().strftime("%b %d")
    lines = [
        f"🚀 **Update ({today})**",
        "",
        "Running three strategies live on paper trading with $100k initial capital. "
        "Here are the results:",
        "",
    ]

    for r in results:
        start_str = r.start.strftime("%b %d")
        end_str = r.end.strftime("%b %d")
        alpha_bps = abs(r.portfolio_growth - r.sp_growth) * 100
        beating = r.portfolio_growth > r.sp_growth

        arrow = "📈" if r.portfolio_growth >= 0 else "📉"
        status = "✅ Outperforming" if beating else "❌ Underperforming"

        lines.append(f"📊 **{r.label}** ({start_str} – {end_str}, "
                     f"{r.trading_days} trading days):")
        lines.append(f"{arrow} {r.short_label}: **{r.portfolio_growth:+.2f}%** "
                     f"(${r.portfolio_value:,.0f})")
        lines.append(f"{arrow} S&P 500 (SPY): **{r.sp_growth:+.2f}%** "
                     f"(${r.sp_equivalent_value:,.0f})")
        lines.append(f"{status} the benchmark by ~{alpha_bps:.0f}bps")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post to Discord webhook
# ---------------------------------------------------------------------------
def post_to_discord(message: str, chart_paths: list[Path]) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL not set in .env")
        sys.exit(1)

    files = []
    for i, path in enumerate(chart_paths):
        files.append(
            ("file" if i == 0 else f"file{i}",
             (path.name, open(path, "rb"), "image/png"))
        )

    response = httpx.post(
        webhook_url,
        data={"content": message},
        files=files,
        timeout=30,
    )

    # Close file handles
    for _, (_, fh, _) in files:
        fh.close()

    if response.status_code in (200, 204):
        print("\nPosted to Discord successfully.")
    else:
        print(f"\nDiscord post failed ({response.status_code}): {response.text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    dry_run = "--dry-run" in sys.argv

    results = []
    for name, profile in PROFILES.items():
        result = fetch_profile(name, profile)
        if result:
            results.append(result)

    if not results:
        print("No profile data available.")
        sys.exit(1)

    message = build_message(results)

    print("\n" + "=" * 60)
    print(message)
    print("=" * 60)

    if dry_run:
        print("\n(dry run — not posting to Discord)")
    else:
        post_to_discord(message, [r.chart_path for r in results])


if __name__ == "__main__":
    main()
