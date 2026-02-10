"""Main event loop orchestrating the news sentiment trading system."""

import asyncio
import os
import signal as signal_mod
from datetime import datetime, timezone

import redis
import structlog

from tokenomics.config import AppConfig, Secrets
from tokenomics.logging_config import get_trade_logger
from tokenomics.models import TradeAction
from tokenomics.news.fetcher import NewsFetchError
from tokenomics.portfolio.manager import PositionManager
from tokenomics.portfolio.risk import RiskManager
from tokenomics.providers import create_broker_provider, create_llm_provider, create_news_provider
from tokenomics.state.redis_backend import RedisStateBackend
from tokenomics.trading.broker import OrderError
from tokenomics.trading.signals import SignalGenerator

logger = structlog.get_logger(__name__)


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

        # Redis state backend (per-profile isolation)
        profile_id = f"{config.providers.news}-{config.providers.llm}-{config.providers.broker}"
        self._state_backend = RedisStateBackend(profile_id)

    async def start(self) -> None:
        """Start the engine. Runs until shutdown signal received."""
        logger.info(
            "tokenomics.starting",
            strategy=self._config.strategy.name,
            paper=self._config.trading.paper,
            capital=self._config.strategy.capital_usd,
            news_provider=self._config.providers.news,
            llm_provider=self._config.providers.llm,
            broker_provider=self._config.providers.broker,
        )

        self._running = True
        self._register_signal_handlers()
        self._restore_state()
        self._preflight_checks()

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
                logger.debug("tokenomics.no_new_articles", tick=self._tick_count)
                return

            logger.info("tokenomics.processing_articles", count=len(articles))

            # Step 5: Analyze sentiment and generate signals
            results = self._analyzer.analyze_batch(articles)

            logger.info(
                "tokenomics.sentiment_complete",
                articles=len(articles),
                results=len(results),
            )

            for result in results:
                logger.debug(
                    "tokenomics.sentiment_result",
                    symbol=result.symbol,
                    sentiment=result.sentiment.value,
                    conviction=result.conviction,
                    time_horizon=result.time_horizon.value,
                    headline=result.headline[:60],
                )

                # Step 6: Generate signal
                signal = self._signal_gen.evaluate(
                    result,
                    self._position_mgr.get_open_symbols(),
                    self._position_mgr.get_open_count(),
                )
                if signal is None:
                    continue

                logger.info(
                    "tokenomics.signal_generated",
                    symbol=signal.symbol,
                    action=signal.action.value,
                    conviction=signal.conviction,
                    position_size_usd=signal.position_size_usd,
                )

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
                        action=signal.action.value,
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
                # Check if any other pod already holds this symbol
                if self._is_symbol_held_by_any_pod(signal.symbol):
                    logger.info(
                        "tokenomics.duplicate_prevented",
                        symbol=signal.symbol,
                        msg="Another profile already holds this position",
                    )
                    return

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

    def _preflight_checks(self) -> None:
        """Test all provider API connections before entering the main loop."""
        logger.info("tokenomics.preflight_starting")
        all_ok = True

        # Broker check
        try:
            account = self._broker.get_account()
            clock = self._broker.get_clock()
            logger.info(
                "preflight.broker_ok",
                provider=self._config.providers.broker,
                status=account["status"],
                equity=account["equity"],
                cash=account["cash"],
                buying_power=account["buying_power"],
                market_open=clock["is_open"],
            )
        except Exception as e:
            logger.error(
                "preflight.broker_failed",
                provider=self._config.providers.broker,
                error=str(e),
            )
            all_ok = False

        # News provider check — save/restore seen_ids so articles aren't consumed
        saved_seen = self._fetcher.get_seen_ids()
        try:
            articles = self._fetcher.fetch_new_articles()
            logger.info(
                "preflight.news_ok",
                provider=self._config.providers.news,
                articles_available=len(articles),
            )
        except Exception as e:
            logger.error(
                "preflight.news_failed",
                provider=self._config.providers.news,
                error=str(e),
            )
            all_ok = False
        finally:
            self._fetcher.restore_seen_ids(saved_seen)

        # LLM provider check — send a minimal test prompt
        try:
            from tokenomics.models import NewsArticle

            test_article = NewsArticle(
                id="preflight-test",
                headline="Test connectivity",
                summary="This is a preflight connectivity test.",
                symbols=["TEST"],
                source="preflight",
                url="",
                created_at=datetime.now(timezone.utc),
            )
            result = self._analyzer.analyze(test_article, "TEST")
            if result is not None:
                logger.info(
                    "preflight.llm_ok",
                    provider=self._config.providers.llm,
                    model=self._config.sentiment.model,
                    test_sentiment=result.sentiment.value,
                    test_conviction=result.conviction,
                )
            else:
                logger.warning(
                    "preflight.llm_parse_failed",
                    provider=self._config.providers.llm,
                    model=self._config.sentiment.model,
                    msg="API responded but result could not be parsed",
                )
        except Exception as e:
            logger.error(
                "preflight.llm_failed",
                provider=self._config.providers.llm,
                model=self._config.sentiment.model,
                error=str(e),
            )
            all_ok = False

        # Redis check — verify connectivity and authentication
        try:
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            redis_password = os.getenv("REDIS_PASSWORD")

            redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
            )

            # Test connection with PING
            pong = redis_client.ping()

            # Test write/read
            test_key = "preflight:test"
            redis_client.set(test_key, "ok", ex=10)
            test_value = redis_client.get(test_key)
            redis_client.delete(test_key)

            logger.info(
                "preflight.redis_ok",
                host=redis_host,
                port=redis_port,
                ping=pong,
                read_write="ok" if test_value == "ok" else "failed",
            )
            redis_client.close()
        except Exception as e:
            logger.error(
                "preflight.redis_failed",
                host=os.getenv("REDIS_HOST", "localhost"),
                port=os.getenv("REDIS_PORT", "6379"),
                error=str(e),
            )
            all_ok = False

        if all_ok:
            logger.info("tokenomics.preflight_passed")
        else:
            logger.error(
                "tokenomics.preflight_failed",
                msg="One or more providers failed connectivity checks. "
                "The engine will start but may not function correctly.",
            )

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
        self._state_backend.close()

    def _is_symbol_held_by_any_pod(self, symbol: str) -> bool:
        """Check Redis for a symbol held by any profile."""
        return self._state_backend.is_symbol_held_by_any_profile(symbol)

    def _persist_state(self) -> None:
        """Save all state to Redis."""
        try:
            self._state_backend.save_state(
                positions=self._position_mgr.to_state_dict(),
                risk=self._risk_mgr.to_state_dict(),
                seen_article_ids=list(self._fetcher.get_seen_ids()),
            )
        except Exception as e:
            logger.error(
                "tokenomics.state_persist_failed",
                error=str(e),
                exc_info=True,
            )

    def _restore_state(self) -> None:
        """Load state from Redis if available, then reconcile with broker."""
        try:
            state = self._state_backend.load_state()

            if state:
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
            else:
                logger.info("tokenomics.no_state_found")

        except Exception as e:
            logger.error("tokenomics.state_restore_failed", error=str(e), exc_info=True)

        # Always reconcile with broker — adopts untracked positions
        try:
            warnings = self._position_mgr.reconcile_with_broker()
            for w in warnings:
                logger.warning("tokenomics.restore_reconciliation", msg=w)
        except Exception as e:
            logger.error("tokenomics.broker_reconciliation_failed", error=str(e))
