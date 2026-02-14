"""Abstract base class for financials data providers."""

from abc import ABC, abstractmethod

from tokenomics.models import BasicFinancials


class FinancialsProvider(ABC):
    """Interface for fetching company financial metrics."""

    @abstractmethod
    def get_basic_financials(self, symbol: str) -> BasicFinancials:
        """Fetch basic financial metrics for a company.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")

        Returns:
            BasicFinancials object with valuation, profitability,
            growth, and financial health metrics.

        Raises:
            FinancialsFetchError: If the API call fails.
        """
        ...

    @abstractmethod
    def get_basic_financials_batch(
        self, symbols: list[str]
    ) -> dict[str, BasicFinancials]:
        """Fetch basic financials for multiple symbols.

        Args:
            symbols: List of stock ticker symbols

        Returns:
            Dict mapping symbol to BasicFinancials.
            Failed symbols are omitted from results.
        """
        ...


class FinancialsFetchError(Exception):
    """Raised when fetching financials data fails."""

    pass
