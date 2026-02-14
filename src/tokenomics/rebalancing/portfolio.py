"""Portfolio construction - convert scores to target weights."""

from dataclasses import dataclass
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class TargetPortfolio:
    """Target portfolio with weights and metadata."""

    weights: dict[str, float]  # symbol -> weight (0.0 to 1.0)
    total_weight: float  # Should be ~1.0
    stock_count: int


def compute_target_weights(
    scores: list[tuple[str, float]],
    top_n: int = 50,
    weighting: Literal["score", "equal"] = "score",
    max_position_pct: float = 5.0,
    min_score: float = 50.0,
) -> TargetPortfolio:
    """Compute target portfolio weights from fundamental scores.

    Args:
        scores: List of (symbol, score) tuples, sorted by score descending
        top_n: Number of top stocks to include
        weighting: "score" for score-proportional, "equal" for equal weight
        max_position_pct: Maximum weight per stock (percentage, e.g., 5.0 = 5%)
        min_score: Minimum score to include (filter out low-quality stocks)

    Returns:
        TargetPortfolio with normalized weights summing to 1.0
    """
    # Filter by minimum score
    filtered = [(s, score) for s, score in scores if score >= min_score]

    if not filtered:
        logger.warning(
            "portfolio.no_qualifying_stocks",
            min_score=min_score,
            total_scores=len(scores),
        )
        return TargetPortfolio(weights={}, total_weight=0.0, stock_count=0)

    # Take top N
    top_stocks = filtered[:top_n]

    logger.info(
        "portfolio.selecting_stocks",
        total_available=len(filtered),
        selecting=len(top_stocks),
        top_score=top_stocks[0][1] if top_stocks else 0,
        bottom_score=top_stocks[-1][1] if top_stocks else 0,
    )

    # Compute raw weights
    if weighting == "equal":
        raw_weights = {symbol: 1.0 for symbol, _ in top_stocks}
    else:  # score-weighted
        raw_weights = {symbol: score for symbol, score in top_stocks}

    # Normalize to sum to 1.0
    total = sum(raw_weights.values())
    if total == 0:
        return TargetPortfolio(weights={}, total_weight=0.0, stock_count=0)

    weights = {symbol: w / total for symbol, w in raw_weights.items()}

    # Apply max position cap
    max_weight = max_position_pct / 100.0
    capped = False

    for symbol in weights:
        if weights[symbol] > max_weight:
            weights[symbol] = max_weight
            capped = True

    # Re-normalize after capping
    if capped:
        total = sum(weights.values())
        weights = {symbol: w / total for symbol, w in weights.items()}

        logger.info(
            "portfolio.weights_capped",
            max_position_pct=max_position_pct,
        )

    # Log top 10 weights
    sorted_weights = sorted(weights.items(), key=lambda x: -x[1])
    logger.info(
        "portfolio.target_computed",
        stock_count=len(weights),
        top_10=[(s, round(w * 100, 2)) for s, w in sorted_weights[:10]],
    )

    return TargetPortfolio(
        weights=weights,
        total_weight=sum(weights.values()),
        stock_count=len(weights),
    )
