"""
Tokenomics positions P&L export to CSV.

Fetches current open positions from Alpaca (live prices + unrealized P&L)
and closed positions from local state.json, then writes a CSV report.

Usage:
    source .venv/bin/activate
    python scripts/positions_pnl.py v2
    python scripts/positions_pnl.py v3
    python scripts/positions_pnl.py all

Output: positions_pnl_<date>.csv in the project root.
"""

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from alpaca.trading.client import TradingClient

ROOT = Path(__file__).parent.parent

PROFILES = {
    "v2": {
        "label": "V2 Base",
        "api_key_env": "ALPACA_API_KEY",
        "secret_key_env": "ALPACA_SECRET_KEY",
        "state_file": ROOT / "data" / "state.json",
    },
    "v3": {
        "label": "V3 Composite",
        "api_key_env": "ALPACA_API_KEY_V3",
        "secret_key_env": "ALPACA_SECRET_KEY_V3",
        "state_file": ROOT / "data" / "state.json",
    },
    "v4": {
        "label": "V4 Regime",
        "api_key_env": "ALPACA_API_KEY_V4",
        "secret_key_env": "ALPACA_SECRET_KEY_V4",
        "state_file": ROOT / "data" / "state.json",
    },
}

FIELDNAMES = [
    "profile", "symbol", "company_name", "status",
    "entry_date", "entry_price", "quantity", "cost_basis_usd",
    "current_price", "market_value_usd",
    "pnl_usd", "pnl_pct",
    "stop_loss_price", "take_profit_price", "max_hold_date",
    "exit_date", "exit_price",
]

if len(sys.argv) < 2 or sys.argv[1] not in (*PROFILES, "all"):
    print(f"Usage: python {sys.argv[0]} <{'|'.join(PROFILES)}|all>")
    sys.exit(1)

arg = sys.argv[1]
profiles_to_run = list(PROFILES.keys()) if arg == "all" else [arg]

rows = []

for profile_name in profiles_to_run:
    profile = PROFILES[profile_name]

    api_key = os.environ.get(profile["api_key_env"])
    secret_key = os.environ.get(profile["secret_key_env"])

    if not api_key or not secret_key:
        print(
            f"[{profile_name}] Missing credentials "
            f"({profile['api_key_env']} / {profile['secret_key_env']}), skipping."
        )
        continue

    print(f"[{profile_name}] Fetching open positions from Alpaca...")
    client = TradingClient(api_key, secret_key, paper=True)
    alpaca_positions = client.get_all_positions()

    # Load state.json for extra metadata: stop_loss, take_profit, max_hold_date
    state_meta: dict = {}
    closed_positions: list = []
    state_file = profile["state_file"]

    # Fetch company names for all symbols in one pass
    company_names: dict[str, str] = {}
    all_symbols = {pos.symbol for pos in alpaca_positions}
    if state_file.exists():
        with open(state_file) as f:
            _state_peek = json.load(f)
        for sym in _state_peek.get("positions", {}).get("closed_positions", []):
            all_symbols.add(sym["symbol"])
    for symbol in all_symbols:
        try:
            asset = client.get_asset(symbol)
            company_names[symbol] = asset.name or symbol
        except Exception:
            company_names[symbol] = symbol

    if state_file.exists():
        with open(state_file) as f:
            state = json.load(f)
        state_meta = state.get("positions", {}).get("positions", {})
        closed_positions = state.get("positions", {}).get("closed_positions", [])

    # --- Open positions (live data from Alpaca) ---
    for pos in alpaca_positions:
        symbol = pos.symbol
        meta = state_meta.get(symbol, {})

        entry_price = float(pos.avg_entry_price)
        current_price = float(pos.current_price)
        pnl_usd = float(pos.unrealized_pl or 0)
        pnl_pct = (current_price / entry_price - 1) * 100 if entry_price else 0

        rows.append({
            "profile": profile["label"],
            "symbol": symbol,
            "company_name": company_names.get(symbol, symbol),
            "status": "open",
            "entry_date": meta.get("entry_date", ""),
            "entry_price": round(entry_price, 4),
            "quantity": float(pos.qty),
            "cost_basis_usd": round(float(pos.cost_basis or 0), 2),
            "current_price": round(current_price, 4),
            "market_value_usd": round(float(pos.market_value or 0), 2),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss_price": meta.get("stop_loss_price", ""),
            "take_profit_price": meta.get("take_profit_price", ""),
            "max_hold_date": meta.get("max_hold_date", ""),
            "exit_date": "",
            "exit_price": "",
        })

    # --- Closed positions (from state.json) ---
    for pos in closed_positions:
        rows.append({
            "profile": profile["label"],
            "symbol": pos["symbol"],
            "company_name": company_names.get(pos["symbol"], pos["symbol"]),
            "status": "closed",
            "entry_date": pos.get("entry_date", ""),
            "entry_price": pos.get("entry_price", ""),
            "quantity": pos.get("quantity", ""),
            "cost_basis_usd": pos.get("position_size_usd", ""),
            "current_price": "",
            "market_value_usd": "",
            "pnl_usd": round(pos.get("pnl_usd") or 0, 2),
            "pnl_pct": round(pos.get("pnl_pct") or 0, 2),
            "stop_loss_price": pos.get("stop_loss_price", ""),
            "take_profit_price": pos.get("take_profit_price", ""),
            "max_hold_date": pos.get("max_hold_date", ""),
            "exit_date": pos.get("exit_date", ""),
            "exit_price": pos.get("exit_price", ""),
        })

    print(
        f"[{profile_name}] {len(alpaca_positions)} open, "
        f"{len(closed_positions)} closed"
    )

if not rows:
    print("No positions found.")
    sys.exit(0)

# Sort by pnl_usd descending (best performers first)
rows.sort(key=lambda r: r["pnl_usd"] if isinstance(r["pnl_usd"], float) else float("-inf"), reverse=True)

# --- Console summary ---
open_rows = [r for r in rows if r["status"] == "open"]
closed_rows = [r for r in rows if r["status"] == "closed"]
total_market_value = sum(r["market_value_usd"] for r in open_rows if isinstance(r["market_value_usd"], float))
total_cost_basis = sum(r["cost_basis_usd"] for r in open_rows if isinstance(r["cost_basis_usd"], float))
total_unrealized_pnl = sum(r["pnl_usd"] for r in open_rows if isinstance(r["pnl_usd"], float))
total_realized_pnl = sum(r["pnl_usd"] for r in closed_rows if isinstance(r["pnl_usd"], float))
total_pnl = total_unrealized_pnl + total_realized_pnl

print()
print("--- Summary ---")
print(f"Open positions:        {len(open_rows)}")
print(f"Closed positions:      {len(closed_rows)}")
if open_rows:
    print(f"Total cost basis:      ${total_cost_basis:,.2f}")
    print(f"Total market value:    ${total_market_value:,.2f}")
    print(f"Unrealized P&L:        ${total_unrealized_pnl:+,.2f}")
if closed_rows:
    print(f"Realized P&L:          ${total_realized_pnl:+,.2f}")
print(f"Overall P&L:           ${total_pnl:+,.2f}")

# --- Write CSV ---
suffix = arg if arg != "all" else "all"
output_path = ROOT / f"positions_pnl_{suffix}_{date.today().isoformat()}.csv"

with open(output_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)

print(f"\nCSV saved to {output_path}")
