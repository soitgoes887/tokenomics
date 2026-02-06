"""Domain models for the tokenomics trading system."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


class TimeHorizon(str, Enum):
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class NewsArticle(BaseModel):
    """Normalized representation of a news article from Alpaca."""

    id: str
    headline: str
    summary: str
    content: Optional[str] = None
    symbols: list[str]
    source: str
    url: str
    created_at: datetime
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SentimentResult(BaseModel):
    """Output from LLM sentiment analysis."""

    article_id: str
    headline: str
    symbol: str
    sentiment: Sentiment
    conviction: int = Field(ge=0, le=100)
    time_horizon: TimeHorizon
    reasoning: str
    key_factors: list[str]
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradeSignal(BaseModel):
    """A decision to act on a sentiment result."""

    signal_id: str
    article_id: str
    symbol: str
    action: TradeAction
    conviction: int
    sentiment: Sentiment
    position_size_usd: float
    reasoning: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Position(BaseModel):
    """Tracked position with entry metadata."""

    symbol: str
    alpaca_order_id: str
    entry_price: float
    quantity: float
    position_size_usd: float
    entry_date: datetime
    signal: TradeSignal
    stop_loss_price: float
    take_profit_price: float
    max_hold_date: datetime
    status: str = "open"
    exit_price: Optional[float] = None
    exit_date: Optional[datetime] = None
    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None


class PortfolioSnapshot(BaseModel):
    """Point-in-time portfolio state for logging."""

    timestamp: datetime
    total_equity_usd: float
    cash_usd: float
    open_positions: int
    unrealized_pnl_usd: float
    realized_pnl_today_usd: float
    daily_return_pct: float
