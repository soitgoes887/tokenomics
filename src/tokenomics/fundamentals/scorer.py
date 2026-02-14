"""Composite score calculator for fundamental analysis."""

from dataclasses import dataclass
from typing import Optional

import structlog

from tokenomics.models import BasicFinancials

logger = structlog.get_logger(__name__)


@dataclass
class FundamentalsScore:
    """Composite score with component breakdown."""

    symbol: str
    composite_score: float  # 0-100 final score

    # Component scores (0-100 each)
    roe_score: float
    debt_score: float
    growth_score: float

    # Raw values used in calculation
    roe: Optional[float]
    debt_to_equity: Optional[float]
    revenue_growth: Optional[float]
    eps_growth: Optional[float]

    # Scoring flags
    has_sufficient_data: bool


class FundamentalsScorer:
    """Calculates composite fundamental quality scores.

    The composite score weights:
    - ROE (Return on Equity): 40% - profitability indicator
    - Debt Ratio (D/E): 30% - financial health indicator
    - Growth (Revenue + EPS): 30% - growth indicator

    Scores are normalized to 0-100 scale where higher is better.
    """

    def __init__(
        self,
        roe_weight: float = 0.40,
        debt_weight: float = 0.30,
        growth_weight: float = 0.30,
    ):
        """Initialize the scorer with component weights.

        Args:
            roe_weight: Weight for ROE component (default 0.40)
            debt_weight: Weight for debt ratio component (default 0.30)
            growth_weight: Weight for growth component (default 0.30)
        """
        self._roe_weight = roe_weight
        self._debt_weight = debt_weight
        self._growth_weight = growth_weight

        # Normalization parameters (based on typical ranges)
        # ROE: -20% to +40% typical range
        self._roe_min = -20.0
        self._roe_max = 40.0

        # Debt/Equity: 0 to 3.0 typical (lower is better)
        self._debt_min = 0.0
        self._debt_max = 3.0

        # Revenue growth: -30% to +50% typical
        self._growth_min = -30.0
        self._growth_max = 50.0

    def calculate_score(self, financials: BasicFinancials) -> FundamentalsScore:
        """Calculate composite fundamental score for a company.

        Args:
            financials: BasicFinancials object with company metrics

        Returns:
            FundamentalsScore with composite and component scores
        """
        # Extract raw values
        roe = financials.roe
        debt_to_equity = financials.debt_to_equity
        revenue_growth = financials.revenue_growth_3y or financials.revenue_growth_5y
        eps_growth = financials.eps_growth_3y or financials.eps_growth_5y

        # Calculate component scores
        roe_score = self._score_roe(roe)
        debt_score = self._score_debt(debt_to_equity)
        growth_score = self._score_growth(revenue_growth, eps_growth)

        # Check if we have enough data for a meaningful score
        has_data = sum([roe is not None, debt_to_equity is not None, revenue_growth is not None or eps_growth is not None])
        has_sufficient_data = has_data >= 2

        # Calculate weighted composite score
        if has_sufficient_data:
            # Use available components, reweight if some missing
            components = []
            weights = []

            if roe is not None:
                components.append(roe_score)
                weights.append(self._roe_weight)

            if debt_to_equity is not None:
                components.append(debt_score)
                weights.append(self._debt_weight)

            if revenue_growth is not None or eps_growth is not None:
                components.append(growth_score)
                weights.append(self._growth_weight)

            # Normalize weights to sum to 1
            total_weight = sum(weights)
            if total_weight > 0:
                composite_score = sum(c * (w / total_weight) for c, w in zip(components, weights))
            else:
                composite_score = 50.0  # Neutral if no weights
        else:
            # Insufficient data - assign neutral score
            composite_score = 50.0

        result = FundamentalsScore(
            symbol=financials.symbol,
            composite_score=round(composite_score, 2),
            roe_score=round(roe_score, 2),
            debt_score=round(debt_score, 2),
            growth_score=round(growth_score, 2),
            roe=roe,
            debt_to_equity=debt_to_equity,
            revenue_growth=revenue_growth,
            eps_growth=eps_growth,
            has_sufficient_data=has_sufficient_data,
        )

        logger.debug(
            "score.calculated",
            symbol=financials.symbol,
            composite=result.composite_score,
            roe_score=result.roe_score,
            debt_score=result.debt_score,
            growth_score=result.growth_score,
            has_data=has_sufficient_data,
        )

        return result

    def _score_roe(self, roe: Optional[float]) -> float:
        """Score ROE on 0-100 scale. Higher ROE = higher score."""
        if roe is None:
            return 50.0  # Neutral if missing

        # Normalize to 0-100 range
        return self._normalize(roe, self._roe_min, self._roe_max)

    def _score_debt(self, debt_to_equity: Optional[float]) -> float:
        """Score debt ratio on 0-100 scale. Lower debt = higher score."""
        if debt_to_equity is None:
            return 50.0  # Neutral if missing

        # Invert: lower debt is better
        # First normalize, then invert
        normalized = self._normalize(debt_to_equity, self._debt_min, self._debt_max)
        return 100.0 - normalized

    def _score_growth(
        self,
        revenue_growth: Optional[float],
        eps_growth: Optional[float],
    ) -> float:
        """Score growth on 0-100 scale. Higher growth = higher score."""
        if revenue_growth is None and eps_growth is None:
            return 50.0  # Neutral if missing

        # Average available growth metrics
        scores = []
        if revenue_growth is not None:
            scores.append(self._normalize(revenue_growth, self._growth_min, self._growth_max))
        if eps_growth is not None:
            scores.append(self._normalize(eps_growth, self._growth_min, self._growth_max))

        return sum(scores) / len(scores) if scores else 50.0

    def _normalize(self, value: float, min_val: float, max_val: float) -> float:
        """Normalize a value to 0-100 scale with clamping."""
        if max_val == min_val:
            return 50.0

        normalized = (value - min_val) / (max_val - min_val) * 100.0
        return max(0.0, min(100.0, normalized))
