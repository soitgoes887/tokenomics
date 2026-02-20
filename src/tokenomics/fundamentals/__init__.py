"""Fundamentals data providers for company financial metrics."""

from tokenomics.fundamentals.base import FinancialsFetchError, FinancialsProvider, NoFinancialsDataError
from tokenomics.fundamentals.finnhub import CompanySymbol, FinnhubFinancialsProvider
from tokenomics.fundamentals.scorer import BaseScorer, FundamentalsScore, FundamentalsScorer
from tokenomics.fundamentals.scorer_registry import create_scorer, register_scorer
from tokenomics.fundamentals.store import FundamentalsStore

# Import composite_scorer to trigger its register_scorer() call
import tokenomics.fundamentals.composite_scorer  # noqa: F401

__all__ = [
    "FinancialsProvider",
    "FinancialsFetchError",
    "NoFinancialsDataError",
    "FinnhubFinancialsProvider",
    "CompanySymbol",
    "BaseScorer",
    "FundamentalsScorer",
    "FundamentalsScore",
    "FundamentalsStore",
    "create_scorer",
    "register_scorer",
]
