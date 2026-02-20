"""Rebalancing engine - main orchestration for score-based portfolio rebalancing."""

import sys
from datetime import datetime, timezone

import structlog

from tokenomics.config import AppConfig, ProfileSecrets, Secrets, resolve_profile
from tokenomics.fundamentals.store import FundamentalsStore
from tokenomics.rebalancing.portfolio import compute_target_weights
from tokenomics.rebalancing.trader import generate_trades, TradeSide
from tokenomics.trading.broker import AlpacaBrokerProvider

logger = structlog.get_logger(__name__)


class RebalancingEngine:
    """Score-based portfolio rebalancing engine.

    Reads fundamental scores from Redis, computes target weights,
    and executes trades to rebalance the portfolio.
    """

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config
        self._secrets = secrets

        # Resolve scoring profile
        self._profile_name, self._profile = resolve_profile(config)
        profile_secrets = ProfileSecrets(self._profile)

        # Use profile-specific Alpaca keys if available, fall back to secrets
        broker_kwargs = {}
        if profile_secrets.alpaca_api_key:
            broker_kwargs["alpaca_api_key"] = profile_secrets.alpaca_api_key
        if profile_secrets.alpaca_secret_key:
            broker_kwargs["alpaca_secret_key"] = profile_secrets.alpaca_secret_key

        self._broker = AlpacaBrokerProvider(config, secrets, **broker_kwargs)
        self._store = FundamentalsStore(namespace=self._profile.redis_namespace)

    def run(self) -> int:
        """Run the rebalancing process.

        Returns:
            Exit code (0 for success, 1 for failure)
        """
        start_time = datetime.now(timezone.utc)

        print("=" * 80)
        print("TOKENOMICS PORTFOLIO REBALANCER")
        print("=" * 80)
        print(f"Start Time: {start_time.isoformat()}")
        print(f"Profile:    {self._profile_name}")
        print(f"Namespace:  {self._profile.redis_namespace}")
        print()

        logger.info("rebalancer.starting", timestamp=start_time.isoformat(),
                     profile=self._profile_name)

        try:
            # Check market hours if configured
            if self._config.trading.market_hours_only:
                if not self._broker.is_market_open():
                    print("Market is closed. Exiting.")
                    logger.info("rebalancer.market_closed")
                    return 0

            # Step 1: Load scores from Redis
            print("Loading fundamental scores from Redis...")
            # Load more scores than needed to allow for filtering by min_score
            scores = self._store.get_top_scores(limit=self._config.rebalancing.top_n_stocks * 2)

            if not scores:
                print("ERROR: No scores found in Redis!")
                print("       Run the fundamentals_job first.")
                logger.error("rebalancer.no_scores")
                return 1

            print(f"  Loaded {len(scores)} scores")
            print(f"  Top score: {scores[0][0]} ({scores[0][1]:.1f})")
            print(f"  Min score: {scores[-1][0]} ({scores[-1][1]:.1f})")
            print()

            # Step 2: Compute target weights
            print("Computing target portfolio weights...")
            rebal_config = self._config.rebalancing
            target = compute_target_weights(
                scores=scores,
                top_n=rebal_config.top_n_stocks,
                weighting=rebal_config.weighting,
                max_position_pct=rebal_config.max_position_pct,
                min_score=rebal_config.min_score,
            )

            if target.stock_count == 0:
                print("ERROR: No stocks qualify for portfolio!")
                logger.error("rebalancer.no_qualifying_stocks")
                return 1

            print(f"  Target: {target.stock_count} stocks")
            print(f"  Total weight: {target.total_weight:.4f}")
            print()

            # Step 3: Get current holdings from Alpaca
            print("Getting current holdings from Alpaca...")
            account = self._broker.get_account()
            positions = self._broker.get_open_positions()

            portfolio_value = account["equity"]
            print(f"  Portfolio value: ${portfolio_value:,.2f}")
            print(f"  Cash: ${account['cash']:,.2f}")
            print(f"  Current positions: {len(positions)}")
            print()

            # Calculate current weights
            current_holdings = {}
            current_prices = {}
            for pos in positions:
                symbol = pos["symbol"]
                market_value = pos["market_value"]
                current_holdings[symbol] = market_value / portfolio_value
                current_prices[symbol] = pos["current_price"]

            # Get prices for target stocks not currently held
            missing_symbols = [s for s in target.weights.keys() if s not in current_prices]
            if missing_symbols:
                print(f"  Fetching prices for {len(missing_symbols)} new symbols...")
                for symbol in missing_symbols:
                    try:
                        # Use broker to get latest price
                        pos = self._broker.get_position(symbol)
                        if pos:
                            current_prices[symbol] = pos["current_price"]
                        else:
                            # Fallback: use Alpaca data API
                            from alpaca.data.historical import StockHistoricalDataClient
                            from alpaca.data.requests import StockLatestTradeRequest

                            data_client = StockHistoricalDataClient(
                                api_key=self._secrets.alpaca_api_key,
                                secret_key=self._secrets.alpaca_secret_key,
                            )
                            trade = data_client.get_stock_latest_trade(
                                StockLatestTradeRequest(symbol_or_symbols=symbol)
                            )
                            current_prices[symbol] = float(trade[symbol].price)
                    except Exception as e:
                        logger.warning(
                            "rebalancer.price_fetch_error",
                            symbol=symbol,
                            error=str(e),
                        )

            # Step 4: Generate trade list
            print("Generating trade list...")
            trades = generate_trades(
                targets=target.weights,
                current_holdings=current_holdings,
                current_prices=current_prices,
                portfolio_value=portfolio_value,
                rebalance_threshold_pct=rebal_config.rebalance_threshold_pct,
                min_trade_usd=rebal_config.min_trade_usd,
            )

            print(f"  Sells: {len(trades.sells)}")
            print(f"  Buys: {len(trades.buys)}")
            print(f"  Skipped: {trades.skipped_count}")
            print(f"  Turnover: ${trades.total_turnover_usd:,.2f} ({trades.total_turnover_usd/portfolio_value*100:.1f}%)")
            print()

            if not trades.sells and not trades.buys:
                print("No trades needed - portfolio is balanced!")
                logger.info("rebalancer.no_trades_needed")
                return 0

            # Step 5: Execute trades (sells first, then buys)
            print("Executing trades...")
            print("-" * 60)

            executed_sells = 0
            executed_buys = 0
            failed = 0

            # Execute sells first to free up capital
            for trade in trades.sells:
                try:
                    print(f"  SELL ~{trade.shares:.2f} {trade.symbol} (${trade.notional_usd:,.0f}) - {trade.reason}")
                    order_id = self._broker.submit_sell_order_notional(trade.symbol, trade.notional_usd)
                    logger.info(
                        "rebalancer.order_executed",
                        side="sell",
                        symbol=trade.symbol,
                        shares=trade.shares,
                        notional_usd=trade.notional_usd,
                        order_id=order_id,
                    )
                    executed_sells += 1
                except Exception as e:
                    print(f"    ERROR: {e}")
                    logger.error(
                        "rebalancer.order_failed",
                        side="sell",
                        symbol=trade.symbol,
                        error=str(e),
                    )
                    failed += 1

            # Execute buys (using notional orders for fractional share support)
            for trade in trades.buys:
                try:
                    print(f"  BUY ~{trade.shares:.2f} {trade.symbol} (${trade.notional_usd:,.0f}) - {trade.reason}")
                    order_id = self._broker.submit_buy_order_notional(trade.symbol, trade.notional_usd)
                    logger.info(
                        "rebalancer.order_executed",
                        side="buy",
                        symbol=trade.symbol,
                        shares=trade.shares,
                        notional_usd=trade.notional_usd,
                        order_id=order_id,
                    )
                    executed_buys += 1
                except Exception as e:
                    print(f"    ERROR: {e}")
                    logger.error(
                        "rebalancer.order_failed",
                        side="buy",
                        symbol=trade.symbol,
                        error=str(e),
                    )
                    failed += 1

            print("-" * 60)

            # Summary
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()

            logger.info(
                "rebalancer.completed",
                duration_seconds=round(duration, 2),
                executed_sells=executed_sells,
                executed_buys=executed_buys,
                failed=failed,
                turnover_usd=round(trades.total_turnover_usd, 2),
            )

            print()
            print("=" * 80)
            print("REBALANCING COMPLETE")
            print("=" * 80)
            print(f"  Duration: {duration:.1f} seconds")
            print(f"  Sells executed: {executed_sells}")
            print(f"  Buys executed: {executed_buys}")
            print(f"  Failed: {failed}")
            print(f"  Total turnover: ${trades.total_turnover_usd:,.2f}")
            print()

            self._store.close()
            return 0 if failed == 0 else 1

        except Exception as e:
            print(f"\nFATAL ERROR: {e}")
            logger.error(
                "rebalancer.fatal_error",
                error=str(e),
                exc_info=True,
            )
            return 1


def main() -> int:
    """Entry point for rebalancing job."""
    from tokenomics.config import load_config
    from tokenomics.logging import setup_logging

    config = load_config()
    secrets = Secrets()
    setup_logging(config)

    engine = RebalancingEngine(config, secrets)
    return engine.run()


if __name__ == "__main__":
    sys.exit(main())
