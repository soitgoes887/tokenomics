"""Tests for the 4-factor CompositeScorer."""

import random

import pytest

from tokenomics.fundamentals.composite_scorer import CompositeScorer
from tokenomics.models import BasicFinancials


def _make_financials(
    symbol: str,
    pe_ratio: float | None = 15.0,
    price_to_cash_flow: float | None = 10.0,
    pb_ratio: float | None = 3.0,
    roe: float | None = 15.0,
    roic: float | None = 12.0,
    gross_margin: float | None = 40.0,
    debt_to_equity: float | None = 0.5,
    price_return_52_week: float | None = 10.0,
    beta: float | None = 1.0,
    high_52_week: float | None = 120.0,
    low_52_week: float | None = 80.0,
) -> BasicFinancials:
    return BasicFinancials(
        symbol=symbol,
        pe_ratio=pe_ratio,
        price_to_cash_flow=price_to_cash_flow,
        pb_ratio=pb_ratio,
        roe=roe,
        roic=roic,
        gross_margin=gross_margin,
        debt_to_equity=debt_to_equity,
        price_return_52_week=price_return_52_week,
        beta=beta,
        high_52_week=high_52_week,
        low_52_week=low_52_week,
    )


class TestCompositeScorer:
    def test_batch_scoring_basic(self):
        """10+ symbols with varying metrics, verify scores 0-100 and sub-scores populated."""
        scorer = CompositeScorer()
        financials = []
        for i in range(12):
            financials.append(
                _make_financials(
                    symbol=f"SYM{i}",
                    pe_ratio=5.0 + i * 3,
                    roe=5.0 + i * 4,
                    roic=3.0 + i * 2,
                    gross_margin=20.0 + i * 5,
                    debt_to_equity=0.1 + i * 0.3,
                    price_return_52_week=-10.0 + i * 5,
                    beta=0.5 + i * 0.15,
                    high_52_week=100.0 + i * 10,
                    low_52_week=60.0 + i * 5,
                )
            )

        scores = scorer.calculate_scores_batch(financials)
        assert len(scores) == 12

        for s in scores:
            assert 0 <= s.composite_score <= 100
            assert s.has_sufficient_data is True
            assert s.value_score is not None
            assert s.quality_score is not None
            assert s.momentum_score is not None
            assert s.lowvol_score is not None
            assert 0 <= s.value_score <= 100
            assert 0 <= s.quality_score <= 100
            assert 0 <= s.momentum_score <= 100
            assert 0 <= s.lowvol_score <= 100

    def test_nan_handling(self):
        """Partial data stocks still scored via re-weighting."""
        scorer = CompositeScorer()

        full = _make_financials("FULL")
        # Missing value metrics (pe, pcf, pb) and momentum
        partial = _make_financials(
            "PART",
            pe_ratio=None,
            price_to_cash_flow=None,
            pb_ratio=None,
            price_return_52_week=None,
        )
        # Third symbol for valid ranking
        other = _make_financials("OTHR", pe_ratio=20.0, roe=25.0)

        scores = scorer.calculate_scores_batch([full, partial, other])
        assert len(scores) == 3

        part_score = scores[1]
        # Should still have quality and lowvol sub-scores
        assert part_score.quality_score is not None
        assert part_score.lowvol_score is not None
        # Value and momentum should be None (all inputs were None)
        assert part_score.value_score is None
        assert part_score.momentum_score is None
        # Still has sufficient data (2 sub-scores available)
        assert part_score.has_sufficient_data is True

    def test_single_symbol_returns_neutral(self):
        """calculate_score() returns 50.0 with has_sufficient_data=False."""
        scorer = CompositeScorer()
        f = _make_financials("TEST")
        score = scorer.calculate_score(f)
        assert score.composite_score == 50.0
        assert score.has_sufficient_data is False
        assert score.symbol == "TEST"

    def test_percentile_distribution(self):
        """100 symbols — final scores span roughly 1-100."""
        scorer = CompositeScorer()
        rng = random.Random(42)

        financials = []
        for i in range(100):
            financials.append(
                _make_financials(
                    symbol=f"S{i:03d}",
                    pe_ratio=rng.uniform(5, 50),
                    price_to_cash_flow=rng.uniform(3, 30),
                    pb_ratio=rng.uniform(0.5, 10),
                    roe=rng.uniform(-5, 40),
                    roic=rng.uniform(-5, 30),
                    gross_margin=rng.uniform(10, 80),
                    debt_to_equity=rng.uniform(0, 3),
                    price_return_52_week=rng.uniform(-30, 60),
                    beta=rng.uniform(0.3, 2.5),
                    high_52_week=rng.uniform(50, 200),
                    low_52_week=rng.uniform(20, 49),
                )
            )

        scores = scorer.calculate_scores_batch(financials)
        composite_vals = [s.composite_score for s in scores]

        assert min(composite_vals) <= 5, "Expected some low scores near 1"
        assert max(composite_vals) >= 95, "Expected some high scores near 100"

    def test_custom_weights(self):
        """value_weight=1.0, others=0 → ranking driven only by value."""
        scorer = CompositeScorer(
            value_weight=1.0,
            quality_weight=0.0,
            momentum_weight=0.0,
            lowvol_weight=0.0,
        )

        # Cheap stock (low PE = high earnings yield)
        cheap = _make_financials("CHEAP", pe_ratio=5.0, pb_ratio=1.0, price_to_cash_flow=3.0)
        # Expensive stock (high PE = low earnings yield)
        expensive = _make_financials("EXPNS", pe_ratio=50.0, pb_ratio=10.0, price_to_cash_flow=30.0)
        # Middle
        mid = _make_financials("MID", pe_ratio=15.0, pb_ratio=3.0, price_to_cash_flow=10.0)

        scores = scorer.calculate_scores_batch([cheap, expensive, mid])
        score_map = {s.symbol: s for s in scores}

        # Cheap should rank highest
        assert score_map["CHEAP"].composite_score > score_map["MID"].composite_score
        assert score_map["MID"].composite_score > score_map["EXPNS"].composite_score

    def test_zero_pe_handled(self):
        """pe_ratio=0 doesn't cause division error."""
        scorer = CompositeScorer()
        financials = [
            _make_financials("ZERO", pe_ratio=0.0),
            _make_financials("NORM", pe_ratio=15.0),
            _make_financials("OTHR", pe_ratio=25.0),
        ]

        scores = scorer.calculate_scores_batch(financials)
        assert len(scores) == 3
        # Zero PE → NaN earnings yield, but should not crash
        for s in scores:
            assert 0 <= s.composite_score <= 100

    def test_negative_debt_to_equity(self):
        """leverage_score handles negative debt_to_equity edge case."""
        scorer = CompositeScorer()
        financials = [
            _make_financials("NEG", debt_to_equity=-0.5),
            _make_financials("POS", debt_to_equity=1.0),
            _make_financials("OTHR", debt_to_equity=0.5),
        ]

        scores = scorer.calculate_scores_batch(financials)
        assert len(scores) == 3
        for s in scores:
            assert 0 <= s.composite_score <= 100
            assert s.has_sufficient_data is True

    def test_empty_list(self):
        """Empty input returns empty list."""
        scorer = CompositeScorer()
        assert scorer.calculate_scores_batch([]) == []
