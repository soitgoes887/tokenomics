"""OHLCV data loader using Alpaca historical bars, with Parquet cache."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import structlog
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logger = structlog.get_logger(__name__)

# Minimum rows of price data required to run a backtest for a symbol
MIN_BARS = 20

# Alpaca returns these column names (lowercase) — we rename to backtesting.py conventions
_RENAME = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


class OHLCVLoader:
    """Fetches daily OHLCV bars from Alpaca and caches them as Parquet files.

    Cache lives at `cache_dir/{symbol}.parquet`.  A cached file is reused if it
    already covers the requested date range; otherwise data is re-fetched and the
    cache is overwritten.

    In K8s (no persistent volume) the cache directory is ephemeral — that is fine;
    the job simply fetches fresh data on every run.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        cache_dir: str = "data/backtest_cache",
    ):
        self._client = StockHistoricalDataClient(
            api_key=api_key, secret_key=secret_key
        )
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # --- Public API ---

    def load(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        chunk_size: int = 50,
    ) -> dict[str, pd.DataFrame]:
        """Return {symbol: OHLCV DataFrame} for all requested symbols.

        Symbols with insufficient data (< MIN_BARS) are skipped with a warning.
        """
        results: dict[str, pd.DataFrame] = {}
        to_fetch: list[str] = []

        for sym in symbols:
            cached = self._load_cache(sym, start, end)
            if cached is not None:
                results[sym] = cached
            else:
                to_fetch.append(sym)

        if to_fetch:
            logger.info("ohlcv_loader.fetching", count=len(to_fetch), total=len(symbols))
            # Chunk to avoid Alpaca request size limits
            for i in range(0, len(to_fetch), chunk_size):
                batch = to_fetch[i : i + chunk_size]
                fetched = self._fetch(batch, start, end)
                for sym, df in fetched.items():
                    self._save_cache(sym, df)
                    results[sym] = df

        # Drop symbols with too little data
        filtered = {}
        for sym, df in results.items():
            if len(df) >= MIN_BARS:
                filtered[sym] = df
            else:
                logger.warning("ohlcv_loader.insufficient_data", symbol=sym, bars=len(df))

        logger.info(
            "ohlcv_loader.loaded",
            requested=len(symbols),
            available=len(filtered),
            from_cache=len(symbols) - len(to_fetch),
        )
        return filtered

    # --- Cache helpers ---

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol}.parquet"

    def _load_cache(
        self, symbol: str, start: datetime, end: datetime
    ) -> Optional[pd.DataFrame]:
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            # Check coverage
            if df.index.min() <= pd.Timestamp(start, tz="UTC") and df.index.max() >= pd.Timestamp(end, tz="UTC"):
                return df.loc[str(start.date()) : str(end.date())]
        except Exception as e:
            logger.debug("ohlcv_loader.cache_miss", symbol=symbol, reason=str(e))
        return None

    def _save_cache(self, symbol: str, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(self._cache_path(symbol))
        except Exception as e:
            logger.debug("ohlcv_loader.cache_write_error", symbol=symbol, error=str(e))

    # --- Alpaca fetch ---

    def _fetch(
        self, symbols: list[str], start: datetime, end: datetime
    ) -> dict[str, pd.DataFrame]:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                adjustment="all",  # corp-action adjusted
            )
            bars = self._client.get_stock_bars(req)
            raw: pd.DataFrame = bars.df
        except Exception as e:
            logger.error("ohlcv_loader.alpaca_error", symbols=symbols[:5], error=str(e))
            return {}

        if raw.empty:
            return {}

        # MultiIndex (symbol, timestamp) → split per symbol
        results: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = raw.loc[sym].copy()
            except KeyError:
                logger.debug("ohlcv_loader.no_data", symbol=sym)
                continue

            # Normalise index to UTC date
            df.index = pd.to_datetime(df.index, utc=True)
            df = df.rename(columns=_RENAME)
            # backtesting.py needs exactly these columns
            keep = [c for c in _RENAME.values() if c in df.columns]
            df = df[keep].dropna()

            if len(df) >= MIN_BARS:
                results[sym] = df

        return results
