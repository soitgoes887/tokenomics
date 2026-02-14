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
    signal: Optional[TradeSignal] = None
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


class MetricDataPoint(BaseModel):
    """A single data point in a time series metric."""

    period: str
    value: float


class BasicFinancials(BaseModel):
    """Basic financial metrics for a company from Finnhub."""

    symbol: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Valuation metrics
    pe_ratio: Optional[float] = Field(None, alias="peBasicExclExtraTTM")
    pe_ratio_annual: Optional[float] = Field(None, alias="peExclExtraAnnual")
    pb_ratio: Optional[float] = Field(None, alias="pbAnnual")
    ps_ratio: Optional[float] = Field(None, alias="psAnnual")
    price_to_cash_flow: Optional[float] = Field(None, alias="pcfShareTTM")
    enterprise_value: Optional[float] = Field(None, alias="enterpriseValue")
    ev_to_ebitda: Optional[float] = Field(None, alias="evToEbitdAnnual")
    ev_to_revenue: Optional[float] = Field(None, alias="evToRevenue")

    # Profitability metrics
    gross_margin: Optional[float] = Field(None, alias="grossMarginTTM")
    operating_margin: Optional[float] = Field(None, alias="operatingMarginTTM")
    net_margin: Optional[float] = Field(None, alias="netProfitMarginTTM")
    roe: Optional[float] = Field(None, alias="roeTTM")
    roa: Optional[float] = Field(None, alias="roaTTM")
    roic: Optional[float] = Field(None, alias="roicTTM")

    # Growth metrics
    revenue_growth_3y: Optional[float] = Field(None, alias="revenueGrowth3Y")
    revenue_growth_5y: Optional[float] = Field(None, alias="revenueGrowth5Y")
    eps_growth_3y: Optional[float] = Field(None, alias="epsGrowth3Y")
    eps_growth_5y: Optional[float] = Field(None, alias="epsGrowth5Y")
    eps_growth_ttm: Optional[float] = Field(None, alias="epsGrowthTTMYoy")

    # Financial health
    current_ratio: Optional[float] = Field(None, alias="currentRatioAnnual")
    quick_ratio: Optional[float] = Field(None, alias="quickRatioAnnual")
    debt_to_equity: Optional[float] = Field(None, alias="totalDebtToEquityAnnual")
    debt_to_assets: Optional[float] = Field(None, alias="totalDebtToTotalAssetsAnnual")
    interest_coverage: Optional[float] = Field(None, alias="interestCoverageAnnual")

    # Per-share data
    eps_ttm: Optional[float] = Field(None, alias="epsBasicExclExtraItemsTTM")
    eps_annual: Optional[float] = Field(None, alias="epsExclExtraItemsAnnual")
    book_value_per_share: Optional[float] = Field(None, alias="bookValuePerShareAnnual")
    revenue_per_share: Optional[float] = Field(None, alias="revenuePerShareAnnual")
    cash_per_share: Optional[float] = Field(None, alias="cashPerSharePerShareAnnual")

    # Dividends
    dividend_yield: Optional[float] = Field(None, alias="dividendYieldIndicatedAnnual")
    dividend_per_share: Optional[float] = Field(None, alias="dividendPerShareAnnual")
    payout_ratio: Optional[float] = Field(None, alias="payoutRatioAnnual")

    # Market data
    market_cap: Optional[float] = Field(None, alias="marketCapitalization")
    beta: Optional[float] = None
    high_52_week: Optional[float] = Field(None, alias="52WeekHigh")
    low_52_week: Optional[float] = Field(None, alias="52WeekLow")
    price_return_52_week: Optional[float] = Field(None, alias="52WeekPriceReturnDaily")
    avg_volume_10_day: Optional[float] = Field(None, alias="10DayAverageTradingVolume")
    avg_volume_3_month: Optional[float] = Field(None, alias="3MonthAverageTradingVolume")

    # Time series (annual historical data)
    current_ratio_history: list[MetricDataPoint] = Field(default_factory=list)
    net_margin_history: list[MetricDataPoint] = Field(default_factory=list)
    sales_per_share_history: list[MetricDataPoint] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
