"""Main event loop orchestrating the news sentiment trading system."""

import asyncio
import json
import signal as signal_mod
from datetime import datetime, timezone
from pathlib import Path

import structlog

from tokenomics.config import AppConfig, Secrets
from tokenomics.logging_config import get_trade_logger
from tokenomics.models import TradeAction
from tokenomics.news.fetcher import NewsFetchError
from tokenomics.portfolio.manager import PositionManager
from tokenomics.portfolio.risk import RiskManager
from tokenomics.providers import create_broker_provider, create_llm_provider, create_news_provider
from tokenomics.trading.broker import OrderError
from tokenomics.trading.signals import SignalGenerator

logger = structlog.get_logger(__name__)

STATE_FILE = Path("data/state.json")


class TokenomicsEngine:
    """Main event loop for the news sentiment trading system."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config
        self._secrets = secrets
        self._broker = create_broker_provider(config, secrets)
        self._fetcher = create_news_provider(config, secrets)
        self._analyzer = create_llm_provider(config, secrets)
        self._signal_gen = SignalGenerator(config)
        self._position_mgr = PositionManager(config, self._broker)
        self._risk_mgr = RiskManager(config)
        self._trade_log = get_trade_logger()
        self._running = False
        self._tick_count = 0

    async def start(self) -> None:
        """Start the engine. Runs until shutdown signal received."""
        logger.info(
            "tokenomics.starting",
            strategy=self._config.strategy.name,
            paper=self._config.trading.paper,
            capital=self._config.strategy.capital_usd,
        )

        self._running = True
        self._register_signal_handlers()
        self._restore_state()

        # Log initial account state
        try:
            account = self._broker.get_account()
            logger.info("tokenomics.account", **account)
        except Exception as e:
            logger.error("tokenomics.account_check_failed", error=str(e))

        try:
            while self._running:
                await self._tick()
                # Sleep until next poll, but check for shutdown every second
                for _ in range(self._config.news.poll_interval_seconds):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("tokenomics.cancelled")
        finally:
            self._shutdown()

    async def _tick(self) -> None:
        """Single iteration of the main loop."""
        self._tick_count += 1
        tick_start = datetime.now(timezone.utc)

        try:
            # Step 1: Check market hours
            if self._config.trading.market_hours_only:
                if not self._broker.is_market_open():
                    if self._tick_count % 20 == 1:  # Log every ~10 minutes
                        clock = self._broker.get_clock()
                        logger.info("tokenomics.market_closed", **clock)
                    return

            # Step 2: Check risk limits
            halted, halt_reason = self._risk_mgr.is_trading_halted()

            # Step 3: Check existing positions for exits (always, even when halted)
            await self._check_position_exits()

            if halted:
                logger.warning("tokenomics.trading_halted", reason=halt_reason)
                self._persist_state()
                return

            # Step 4: Fetch new articles
            try:
                articles = self._fetcher.fetch_new_articles()
            except NewsFetchError:
                # Already logged by fetcher
                return

            if not articles:
                return

            logger.info("tokenomics.processing_articles", count=len(articles))

            # Step 5: Analyze sentiment and generate signals
            results = self._analyzer.analyze_batch(articles)

            for result in results:
                # Step 6: Generate signal
                signal = self._signal_gen.evaluate(
                    result,
                    self._position_mgr.get_open_symbols(),
                    self._position_mgr.get_open_count(),
                )
                if signal is None:
                    continue

                # Step 7: Risk check
                account = self._broker.get_account()
                approved, reason = self._risk_mgr.approve_signal(
                    signal,
                    self._position_mgr.get_open_count(),
                    account["equity"],
                )
                if not approved:
                    logger.info(
                        "tokenomics.signal_rejected",
                        symbol=signal.symbol,
                        reason=reason,
                    )
                    continue

                # Step 8: Execute
                await self._execute_signal(signal)

            # Step 9: Periodic reconciliation (every 20 ticks ~ 10 minutes)
            if self._tick_count % 20 == 0:
                warnings = self._position_mgr.reconcile_with_broker()
                if warnings:
                    for w in warnings:
                        logger.warning("tokenomics.reconciliation_warning", msg=w)

            # Step 10: Persist state
            self._persist_state()

        except Exception as e:
            logger.error(
                "tokenomics.tick_error",
                error=str(e),
                tick=self._tick_count,
                exc_info=True,
            )

        tick_duration = (datetime.now(timezone.utc) - tick_start).total_seconds()
        if tick_duration > 5:
            logger.info("tokenomics.slow_tick", duration_s=round(tick_duration, 2))

    async def _execute_signal(self, signal) -> None:
        """Submit order and record position."""
        try:
            if signal.action == TradeAction.BUY:
                order_id = self._broker.submit_buy_order(signal)

                # Get fill info from broker position
                # (market orders fill nearly instantly on paper)
                await asyncio.sleep(1)  # Brief wait for fill
                broker_pos = self._broker.get_position(signal.symbol)

                if broker_pos:
                    self._position_mgr.open_position(
                        signal=signal,
                        order_id=order_id,
                        fill_price=broker_pos["avg_entry_price"],
                        quantity=broker_pos["qty"],
                    )
                else:
                    logger.warning(
                        "tokenomics.fill_not_confirmed",
                        symbol=signal.symbol,
                        order_id=order_id,
                    )

            elif signal.action == TradeAction.SELL:
                pos = self._position_mgr.get_position(signal.symbol)
                if pos:
                    self._broker.submit_sell_order(signal.symbol, pos.quantity)
                    broker_pos = self._broker.get_position(signal.symbol)
                    # If position is gone from broker, it was sold
                    if broker_pos is None:
                        # Use last known price as exit price
                        positions = self._broker.get_open_positions()
                        # Position closed, estimate exit price
                        closed = self._position_mgr.close_position(
                            signal.symbol,
                            pos.entry_price,  # Will be updated on reconciliation
                            "signal_reversal",
                        )
                        if closed and closed.pnl_usd is not None:
                            self._risk_mgr.record_realized_pnl(
                                closed.pnl_usd, closed.exit_date.date()
                            )

        except OrderError as e:
            logger.error(
                "tokenomics.execution_failed",
                symbol=signal.symbol,
                action=signal.action.value,
                error=str(e),
            )

    async def _check_position_exits(self) -> None:
        """Check all positions against exit criteria and close as needed."""
        if self._position_mgr.get_open_count() == 0:
            return

        # Get current prices from broker
        current_prices = {}
        try:
            broker_positions = self._broker.get_open_positions()
            for bp in broker_positions:
                current_prices[bp["symbol"]] = bp["current_price"]
        except Exception as e:
            logger.error("tokenomics.price_fetch_failed", error=str(e))
            return

        exits = self._position_mgr.check_exits(current_prices)

        for symbol, reason, price in exits:
            pos = self._position_mgr.get_position(symbol)
            if pos is None:
                continue

            logger.info(
                "tokenomics.closing_position",
                symbol=symbol,
                reason=reason,
                current_price=price,
                entry_price=pos.entry_price,
            )

            try:
                self._broker.submit_sell_order(symbol, pos.quantity)
                closed = self._position_mgr.close_position(symbol, price, reason)
                if closed and closed.pnl_usd is not None:
                    self._risk_mgr.record_realized_pnl(
                        closed.pnl_usd, closed.exit_date.date()
                    )
            except OrderError as e:
                logger.error(
                    "tokenomics.exit_failed",
                    symbol=symbol,
                    reason=reason,
                    error=str(e),
                )

    def _register_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()
        for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

    def _request_shutdown(self) -> None:
        """Signal the main loop to stop."""
        logger.info("tokenomics.shutdown_requested")
        self._running = False

    def _shutdown(self) -> None:
        """Graceful shutdown: persist state, log final stats."""
        logger.info("tokenomics.shutting_down")
        self._persist_state()
        stats = self._position_mgr.get_portfolio_stats()
        logger.info("tokenomics.final_stats", **stats)
        logger.info("tokenomics.stopped")

    def _persist_state(self) -> None:
        """Save all state to disk."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "version": 1,
            "last_saved": datetime.now(timezone.utc).isoformat(),
            "positions": self._position_mgr.to_state_dict(),
            "risk": self._risk_mgr.to_state_dict(),
            "seen_article_ids": list(self._fetcher.get_seen_ids()),
        }

        # Write atomically (write to temp, then rename)
        tmp_path = STATE_FILE.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        tmp_path.rename(STATE_FILE)

    def _restore_state(self) -> None:
        """Load state from disk if available."""
        if not STATE_FILE.exists():
            logger.info("tokenomics.no_state_file", path=str(STATE_FILE))
            return

        try:
            with open(STATE_FILE) as f:
                state = json.load(f)

            self._position_mgr.restore_from_state(state.get("positions", {}))
            self._risk_mgr.restore_from_state(state.get("risk", {}))
            self._fetcher.restore_seen_ids(
                set(state.get("seen_article_ids", []))
            )

            logger.info(
                "tokenomics.state_restored",
                last_saved=state.get("last_saved"),
                open_positions=self._position_mgr.get_open_count(),
            )

            # Reconcile with broker after restore
            warnings = self._position_mgr.reconcile_with_broker()
            for w in warnings:
                logger.warning("tokenomics.restore_reconciliation", msg=w)

        except Exception as e:
            logger.error("tokenomics.state_restore_failed", error=str(e))
