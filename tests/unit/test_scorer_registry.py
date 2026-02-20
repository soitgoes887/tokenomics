"""Tests for scorer registry."""

import pytest

from tokenomics.fundamentals.scorer import BaseScorer, FundamentalsScore, FundamentalsScorer
from tokenomics.fundamentals.scorer_registry import create_scorer, get_scorer_class, register_scorer


class TestScorerRegistry:
    def test_get_fundamentals_scorer(self):
        """FundamentalsScorer should be registered by default."""
        cls = get_scorer_class("FundamentalsScorer")
        assert cls is FundamentalsScorer

    def test_get_composite_scorer(self):
        """CompositeScorer should be registered via import."""
        # Import triggers registration
        import tokenomics.fundamentals.composite_scorer  # noqa: F401
        cls = get_scorer_class("CompositeScorer")
        assert issubclass(cls, BaseScorer)

    def test_create_fundamentals_scorer(self):
        """create_scorer should return an instance of the named scorer."""
        scorer = create_scorer("FundamentalsScorer")
        assert isinstance(scorer, FundamentalsScorer)

    def test_create_with_kwargs(self):
        """create_scorer should pass kwargs to the constructor."""
        scorer = create_scorer("FundamentalsScorer", roe_weight=0.5, debt_weight=0.25, growth_weight=0.25)
        assert isinstance(scorer, FundamentalsScorer)
        assert scorer._roe_weight == 0.5

    def test_unknown_scorer_raises(self):
        """Requesting an unregistered scorer should raise KeyError."""
        with pytest.raises(KeyError, match="Unknown scorer"):
            get_scorer_class("NonexistentScorer")

    def test_create_unknown_scorer_raises(self):
        """create_scorer with unknown name should raise KeyError."""
        with pytest.raises(KeyError, match="Unknown scorer"):
            create_scorer("NonexistentScorer")

    def test_register_custom_scorer(self):
        """Custom scorers can be registered."""
        class MyScorer(BaseScorer):
            def calculate_score(self, financials):
                return FundamentalsScore(
                    symbol=financials.symbol,
                    composite_score=99.0,
                    roe_score=99.0,
                    debt_score=99.0,
                    growth_score=99.0,
                    roe=None,
                    debt_to_equity=None,
                    revenue_growth=None,
                    eps_growth=None,
                    has_sufficient_data=True,
                )

        register_scorer("MyScorer", MyScorer)
        scorer = create_scorer("MyScorer")
        assert isinstance(scorer, MyScorer)
