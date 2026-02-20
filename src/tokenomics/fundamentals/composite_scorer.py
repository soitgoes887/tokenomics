"""Placeholder composite scorer for tokenomics_v3_composite profile."""

from tokenomics.fundamentals.scorer import BaseScorer, FundamentalsScore
from tokenomics.fundamentals.scorer_registry import register_scorer
from tokenomics.models import BasicFinancials


class CompositeScorer(BaseScorer):
    """Extended composite scorer (placeholder).

    Currently delegates to the same logic as FundamentalsScorer.
    Will be extended with additional factors in the future.
    """

    def calculate_score(self, financials: BasicFinancials) -> FundamentalsScore:
        # Placeholder: use same logic as FundamentalsScorer for now
        from tokenomics.fundamentals.scorer import FundamentalsScorer

        return FundamentalsScorer().calculate_score(financials)


register_scorer("CompositeScorer", CompositeScorer)
