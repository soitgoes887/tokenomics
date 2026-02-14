"""Finnhub API provider for company basic financials."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import finnhub
import structlog

from tokenomics.fundamentals.base import FinancialsFetchError, FinancialsProvider
from tokenomics.models import BasicFinancials, MetricDataPoint

logger = structlog.get_logger(__name__)


class HasFinnhubApiKey(Protocol):
    """Protocol for any object with finnhub_api_key attribute."""

    finnhub_api_key: str


@dataclass
class CompanySymbol:
    """A company symbol from Finnhub stock symbols endpoint."""

    symbol: str
    description: str
    display_symbol: str
    type: str
    mic: str


class FinnhubFinancialsProvider(FinancialsProvider):
    """Fetches basic financial metrics from Finnhub API.

    Uses the /stock/metric endpoint with metric=all to retrieve
    comprehensive financial data including valuation ratios,
    profitability metrics, growth rates, and financial health indicators.
    """

    def __init__(self, secrets: HasFinnhubApiKey):
        """Initialize the Finnhub financials provider.

        Args:
            secrets: Any object with finnhub_api_key attribute.
        """
        self._client = finnhub.Client(api_key=secrets.finnhub_api_key)
        self._max_retries = 3
        self._retry_delay_base = 2.0

    def get_us_symbols(self, limit: int = 1250) -> list[CompanySymbol]:
        """Fetch US stock symbols from Finnhub.

        Filters to Common Stock type and returns up to `limit` symbols.

        Args:
            limit: Maximum number of symbols to return (default 1250)

        Returns:
            List of CompanySymbol objects for US common stocks.

        Raises:
            FinancialsFetchError: If the API call fails.
        """
        try:
            logger.info("symbols.fetching", exchange="US", limit=limit)

            response = self._client.stock_symbols("US")

            if not response:
                raise FinancialsFetchError("No symbols returned from Finnhub")

            # Filter to Common Stock only and take first `limit`
            common_stocks = [
                CompanySymbol(
                    symbol=item.get("symbol", ""),
                    description=item.get("description", ""),
                    display_symbol=item.get("displaySymbol", ""),
                    type=item.get("type", ""),
                    mic=item.get("mic", ""),
                )
                for item in response
                if item.get("type") == "Common Stock"
                and item.get("symbol")
                and not self._is_special_symbol(item.get("symbol", ""))
            ]

            # Sort by symbol for consistent ordering
            common_stocks.sort(key=lambda x: x.symbol)
            result = common_stocks[:limit]

            logger.info(
                "symbols.fetched",
                total_raw=len(response),
                common_stocks=len(common_stocks),
                returned=len(result),
            )

            return result

        except finnhub.FinnhubAPIException as e:
            logger.error("symbols.api_error", error=str(e))
            raise FinancialsFetchError(f"Failed to fetch US symbols: {e}") from e

    def _is_special_symbol(self, symbol: str) -> bool:
        """Check if symbol is a special/derivative symbol to exclude.

        Excludes warrants, units, preferred shares, etc.
        """
        # Exclude symbols with special suffixes
        special_suffixes = [".W", ".U", ".R", "-P", "-A", "-B", "-C", "-D"]
        for suffix in special_suffixes:
            if symbol.endswith(suffix):
                return True

        # Exclude symbols with special characters typically indicating derivatives
        if any(char in symbol for char in [".", "-", "+"]):
            # Allow simple symbols that might have legit dots (rare)
            if len(symbol) > 5:
                return True

        return False

    def get_basic_financials(self, symbol: str) -> BasicFinancials:
        """Fetch basic financial metrics for a single company.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL")

        Returns:
            BasicFinancials object with all available metrics.

        Raises:
            FinancialsFetchError: If the API call fails after retries.
        """
        last_error = None

        for attempt in range(self._max_retries):
            try:
                logger.debug(
                    "financials.fetching",
                    symbol=symbol,
                    attempt=attempt + 1,
                )

                response = self._client.company_basic_financials(
                    symbol=symbol, metric="all"
                )

                if not response or not response.get("metric"):
                    raise FinancialsFetchError(
                        f"No financials data returned for {symbol}"
                    )

                financials = self._parse_response(symbol, response)

                logger.info(
                    "financials.fetched",
                    symbol=symbol,
                    pe_ratio=financials.pe_ratio,
                    market_cap=financials.market_cap,
                    roe=financials.roe,
                )

                return financials

            except finnhub.FinnhubAPIException as e:
                last_error = e
                logger.warning(
                    "financials.api_error",
                    symbol=symbol,
                    attempt=attempt + 1,
                    error=str(e),
                )

                if attempt < self._max_retries - 1:
                    delay = self._retry_delay_base * (2**attempt)
                    time.sleep(delay)

            except Exception as e:
                last_error = e
                logger.error(
                    "financials.unexpected_error",
                    symbol=symbol,
                    error=str(e),
                )
                break

        raise FinancialsFetchError(
            f"Failed to fetch financials for {symbol}: {last_error}"
        ) from last_error

    def get_basic_financials_batch(
        self, symbols: list[str]
    ) -> dict[str, BasicFinancials]:
        """Fetch basic financials for multiple symbols.

        Note: Finnhub API doesn't support batch requests, so this
        iterates through symbols with rate limiting (30 req/sec max).

        Args:
            symbols: List of stock ticker symbols

        Returns:
            Dict mapping symbol to BasicFinancials.
            Failed symbols are logged and omitted from results.
        """
        results: dict[str, BasicFinancials] = {}
        failed: list[str] = []

        for i, symbol in enumerate(symbols):
            try:
                results[symbol] = self.get_basic_financials(symbol)

                # Rate limiting: ~25 requests per second to stay under limit
                if i < len(symbols) - 1:
                    time.sleep(0.04)

            except FinancialsFetchError as e:
                logger.warning(
                    "financials.batch_item_failed",
                    symbol=symbol,
                    error=str(e),
                )
                failed.append(symbol)

        if failed:
            logger.warning(
                "financials.batch_complete_with_failures",
                total=len(symbols),
                success=len(results),
                failed=len(failed),
                failed_symbols=failed[:10],  # Limit logged symbols
            )
        else:
            logger.info(
                "financials.batch_complete",
                total=len(symbols),
                success=len(results),
            )

        return results

    def _parse_response(self, symbol: str, response: dict) -> BasicFinancials:
        """Parse Finnhub API response into BasicFinancials model.

        Args:
            symbol: The stock ticker symbol
            response: Raw API response dict

        Returns:
            BasicFinancials object with mapped fields
        """
        metric = response.get("metric", {})
        series = response.get("series", {})
        annual = series.get("annual", {}) if series else {}

        # Build kwargs with all metrics from the response
        kwargs = {
            "symbol": symbol,
            "fetched_at": datetime.now(timezone.utc),
            # Valuation
            "pe_ratio": metric.get("peBasicExclExtraTTM"),
            "pe_ratio_annual": metric.get("peExclExtraAnnual"),
            "pb_ratio": metric.get("pbAnnual"),
            "ps_ratio": metric.get("psAnnual"),
            "price_to_cash_flow": metric.get("pcfShareTTM"),
            "enterprise_value": metric.get("enterpriseValue"),
            "ev_to_ebitda": metric.get("evToEbitdAnnual"),
            "ev_to_revenue": metric.get("evToRevenue"),
            # Profitability (prefer fiscal year/quarterly over TTM for accuracy)
            "gross_margin": metric.get("grossMarginTTM"),
            "operating_margin": metric.get("operatingMarginTTM"),
            "net_margin": metric.get("netProfitMarginTTM"),
            "roe": metric.get("roeRfy") or metric.get("roeTTM"),  # Fiscal year first, fallback to TTM
            "roa": metric.get("roaRfy") or metric.get("roaTTM"),
            "roic": metric.get("roicTTM"),
            # Growth (prefer TTM YoY for most current data)
            "revenue_growth_3y": metric.get("revenueGrowth3Y"),
            "revenue_growth_5y": metric.get("revenueGrowth5Y"),
            "revenue_growth_ttm": metric.get("revenueGrowthTTMYoy"),  # Most current
            "eps_growth_3y": metric.get("epsGrowth3Y"),
            "eps_growth_5y": metric.get("epsGrowth5Y"),
            "eps_growth_ttm": metric.get("epsGrowthTTMYoy"),  # Most current
            # Financial health (prefer quarterly for most current)
            "current_ratio": metric.get("currentRatioQuarterly") or metric.get("currentRatioAnnual"),
            "quick_ratio": metric.get("quickRatioQuarterly") or metric.get("quickRatioAnnual"),
            "debt_to_equity": metric.get("totalDebt/totalEquityQuarterly") or metric.get("totalDebt/totalEquityAnnual"),
            "debt_to_assets": metric.get("totalDebt/totalAssetsQuarterly") or metric.get("totalDebt/totalAssetsAnnual"),
            "long_term_debt_to_equity": metric.get("longTermDebt/equityQuarterly") or metric.get("longTermDebt/equityAnnual"),
            "interest_coverage": metric.get("interestCoverageAnnual"),
            # Per-share
            "eps_ttm": metric.get("epsBasicExclExtraItemsTTM"),
            "eps_annual": metric.get("epsExclExtraItemsAnnual"),
            "book_value_per_share": metric.get("bookValuePerShareAnnual"),
            "revenue_per_share": metric.get("revenuePerShareAnnual"),
            "cash_per_share": metric.get("cashPerSharePerShareAnnual"),
            # Dividends
            "dividend_yield": metric.get("dividendYieldIndicatedAnnual"),
            "dividend_per_share": metric.get("dividendPerShareAnnual"),
            "payout_ratio": metric.get("payoutRatioAnnual"),
            # Market data
            "market_cap": metric.get("marketCapitalization"),
            "beta": metric.get("beta"),
            "high_52_week": metric.get("52WeekHigh"),
            "low_52_week": metric.get("52WeekLow"),
            "price_return_52_week": metric.get("52WeekPriceReturnDaily"),
            "avg_volume_10_day": metric.get("10DayAverageTradingVolume"),
            "avg_volume_3_month": metric.get("3MonthAverageTradingVolume"),
            # Time series
            "current_ratio_history": self._parse_series(annual.get("currentRatio")),
            "net_margin_history": self._parse_series(annual.get("netMargin")),
            "sales_per_share_history": self._parse_series(annual.get("salesPerShare")),
        }

        return BasicFinancials(**kwargs)

    def _parse_series(self, series_data: list[dict] | None) -> list[MetricDataPoint]:
        """Parse time series data into MetricDataPoint list.

        Args:
            series_data: List of dicts with 'period' and 'v' keys

        Returns:
            List of MetricDataPoint objects
        """
        if not series_data:
            return []

        points = []
        for item in series_data:
            period = item.get("period")
            value = item.get("v")
            if period and value is not None:
                points.append(MetricDataPoint(period=period, value=value))

        return points
