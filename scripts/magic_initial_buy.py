"""One-off: buy a Magic Formula profile's holdings list at equal weight.

This mirrors what the monthly rebalancer does (compute_target_weights →
generate_trades → broker execution) but sources targets directly from the
profile's holdings file instead of Redis, so it can bootstrap the initial
purchase without a Redis connection. Safe to re-run: it diffs against current
holdings and only trades deviations beyond the profile's threshold.

Usage:
    source .venv/bin/activate
    python scripts/magic_initial_buy.py tokenomics_v5_magic_100m            # dry run
    python scripts/magic_initial_buy.py tokenomics_v5_magic_100m --execute  # place orders
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from tokenomics.config import ProfileSecrets, load_config
from tokenomics.magic.holdings import load_holdings
from tokenomics.rebalancing.portfolio import compute_target_weights
from tokenomics.rebalancing.trader import generate_trades
from tokenomics.trading.broker import AlpacaBrokerProvider


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/magic_initial_buy.py <profile_name> [--execute]")
        return 1

    profile_name = sys.argv[1]
    execute = "--execute" in sys.argv

    config = load_config(Path(__file__).parent.parent / "config" / "settings.yaml")
    if config.scoring_profiles is None or profile_name not in config.scoring_profiles.profiles:
        print(f"ERROR: profile '{profile_name}' not found in settings.yaml")
        return 1

    profile = config.scoring_profiles.profiles[profile_name]
    if not profile.holdings_list:
        print(f"ERROR: profile '{profile_name}' has no holdings_list (not a fixed-list profile)")
        return 1

    rebal = profile.rebalancing or config.rebalancing
    profile_secrets = ProfileSecrets(profile)
    if not profile_secrets.alpaca_api_key or not profile_secrets.alpaca_secret_key:
        print(f"ERROR: Alpaca keys for {profile_name} not set "
              f"({profile.alpaca_api_key_env} / {profile.alpaca_secret_key_env})")
        return 1
    # Pass keys explicitly; the broker only touches `secrets` as a fallback, so
    # we can skip building the full Secrets() model (which rejects the extra keys
    # present in a local .env).
    broker = AlpacaBrokerProvider(
        config, None,
        alpaca_api_key=profile_secrets.alpaca_api_key,
        alpaca_secret_key=profile_secrets.alpaca_secret_key,
    )

    symbols = load_holdings(profile.holdings_list)
    print("=" * 70)
    print(f"MAGIC FORMULA INITIAL BUY — {profile_name}")
    print("=" * 70)
    print(f"Holdings file: {profile.holdings_list}  ({len(symbols)} symbols)")
    print(f"Mode:          {'EXECUTE' if execute else 'DRY RUN'}")

    if not broker.is_market_open():
        print("NOTE: market is currently closed — market orders will queue for the next open.")
    print()

    # Equal-weight targets via the same path the rebalancer uses
    scores = [(s, 100.0) for s in symbols]
    target = compute_target_weights(
        scores=scores,
        top_n=rebal.top_n_stocks,
        weighting=rebal.weighting,
        max_position_pct=rebal.max_position_pct,
        min_score=rebal.min_score,
        max_sector_pct=rebal.max_sector_pct,
        sectors=None,
    )
    print(f"Target: {target.stock_count} stocks, {1.0 / target.stock_count * 100:.2f}% each "
          f"(total weight {target.total_weight:.4f})")

    account = broker.get_account()
    positions = broker.get_open_positions()
    portfolio_value = account["equity"]
    print(f"Account: equity ${portfolio_value:,.2f}, cash ${account['cash']:,.2f}, "
          f"{len(positions)} open positions")

    current_holdings = {p["symbol"]: p["market_value"] / portfolio_value for p in positions}
    current_prices = {p["symbol"]: p["current_price"] for p in positions}

    # Latest trade prices for target symbols not already held
    missing = [s for s in target.weights if s not in current_prices]
    if missing:
        data_client = StockHistoricalDataClient(
            profile_secrets.alpaca_api_key,
            profile_secrets.alpaca_secret_key,
        )
        trades = data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=missing)
        )
        for sym, tr in trades.items():
            current_prices[sym] = float(tr.price)
        unpriced = [s for s in missing if s not in current_prices]
        if unpriced:
            print(f"WARNING: no price for {len(unpriced)} symbols, they will be skipped: {unpriced}")

    trade_list = generate_trades(
        targets=target.weights,
        current_holdings=current_holdings,
        current_prices=current_prices,
        portfolio_value=portfolio_value,
        rebalance_threshold_pct=rebal.rebalance_threshold_pct,
        min_trade_usd=rebal.min_trade_usd,
    )
    print(f"\nPlanned: {len(trade_list.sells)} sells, {len(trade_list.buys)} buys, "
          f"{trade_list.skipped_count} skipped, "
          f"turnover ${trade_list.total_turnover_usd:,.0f}")
    print("-" * 70)

    executed = failed = 0
    for t in trade_list.sells + trade_list.buys:
        line = f"  {t.side.value.upper():4} {t.symbol:6} ${t.notional_usd:>9,.0f}  {t.reason}"
        if not execute:
            print(line + "   [dry run]")
            continue
        try:
            if t.side.value == "sell":
                broker.close_position(t.symbol) if t.is_full_exit \
                    else broker.submit_sell_order_notional(t.symbol, t.notional_usd)
            else:
                broker.submit_buy_order_notional(t.symbol, t.notional_usd)
            print(line + "   OK")
            executed += 1
        except Exception as e:
            print(line + f"   ERROR: {e}")
            failed += 1

    print("-" * 70)
    if execute:
        print(f"Executed {executed}, failed {failed}.")
    else:
        print("Dry run complete — re-run with --execute to place orders.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
