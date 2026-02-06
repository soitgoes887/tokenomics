"""Convert sentiment analysis into trade signals."""

import uuid

import structlog

from tokenomics.config import AppConfig
from tokenomics.logging_config import get_decision_logger
from tokenomics.models import (
    Sentiment,
    SentimentResult,
    TradeAction,
    TradeSignal,
)

logger = structlog.get_logger(__name__)


class SignalGenerator:
    """Applies business rules to convert SentimentResults into TradeSignals."""

    def __init__(self, config: AppConfig):
        self._strategy = config.strategy
        self._min_conviction = config.sentiment.min_conviction
        self._decision_log = get_decision_logger()

    def evaluate(
        self,
        result: SentimentResult,
        open_position_symbols: set[str],
        open_position_count: int,
    ) -> TradeSignal | None:
        """
        Evaluate a sentiment result and return a TradeSignal or None.

        Returns None if:
        - Conviction below threshold
        - Sentiment is NEUTRAL
        - Already have a position in this symbol
        - At max open positions
        """
        # Skip neutral sentiment
        if result.sentiment == Sentiment.NEUTRAL:
            self._decision_log.info(
                "signal.skipped",
                symbol=result.symbol,
                headline=result.headline,
                reason="neutral_sentiment",
                conviction=result.conviction,
            )
            return None

        # Skip low conviction
        if result.conviction < self._min_conviction:
            self._decision_log.info(
                "signal.skipped",
                symbol=result.symbol,
                headline=result.headline,
                reason="low_conviction",
                conviction=result.conviction,
                threshold=self._min_conviction,
            )
            return None

        # For BULLISH signals: generate BUY
        if result.sentiment == Sentiment.BULLISH:
            # Skip if already holding
            if result.symbol in open_position_symbols:
                self._decision_log.info(
                    "signal.skipped",
                    symbol=result.symbol,
                    headline=result.headline,
                    reason="already_held",
                )
                return None

            # Skip if at capacity
            if open_position_count >= self._strategy.max_open_positions:
                self._decision_log.info(
                    "signal.skipped",
                    symbol=result.symbol,
                    headline=result.headline,
                    reason="max_positions_reached",
                    current=open_position_count,
                    max=self._strategy.max_open_positions,
                )
                return None

            position_size = self._calculate_position_size(result.conviction)

            signal = TradeSignal(
                signal_id=str(uuid.uuid4()),
                article_id=result.article_id,
                symbol=result.symbol,
                action=TradeAction.BUY,
                conviction=result.conviction,
                sentiment=result.sentiment,
                position_size_usd=position_size,
                reasoning=result.reasoning,
            )

            self._decision_log.info(
                "signal.generated",
                symbol=signal.symbol,
                headline=result.headline,
                action=signal.action.value,
                conviction=signal.conviction,
                position_size=signal.position_size_usd,
            )

            return signal

        # For BEARISH signals: generate SELL only if we hold the position
        if result.sentiment == Sentiment.BEARISH:
            if result.symbol in open_position_symbols:
                signal = TradeSignal(
                    signal_id=str(uuid.uuid4()),
                    article_id=result.article_id,
                    symbol=result.symbol,
                    action=TradeAction.SELL,
                    conviction=result.conviction,
                    sentiment=result.sentiment,
                    position_size_usd=0,  # Selling entire position
                    reasoning=result.reasoning,
                )

                self._decision_log.info(
                    "signal.generated",
                    symbol=signal.symbol,
                    headline=result.headline,
                    action=signal.action.value,
                    conviction=signal.conviction,
                    reason="bearish_reversal",
                )

                return signal

            # Bearish on something we don't hold -- no action (long-only)
            self._decision_log.info(
                "signal.skipped",
                symbol=result.symbol,
                headline=result.headline,
                reason="bearish_not_held",
            )
            return None

        return None

    def _calculate_position_size(self, conviction: int) -> float:
        """Scale position size by conviction within configured bounds."""
        min_size = self._strategy.position_size_min_usd
        max_size = self._strategy.position_size_max_usd

        # Linear interpolation: conviction 70 -> min_size, conviction 100 -> max_size
        # Clamp the range to [min_conviction, 100]
        conviction_range = 100 - self._min_conviction
        if conviction_range <= 0:
            return max_size

        normalized = (conviction - self._min_conviction) / conviction_range
        return min_size + normalized * (max_size - min_size)
