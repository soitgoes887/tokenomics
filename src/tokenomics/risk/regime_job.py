"""Daily risk regime job: CGRS-lite from VIX + Finnhub sentiment.

Runs as a K8s CronJob (daily) to:
  1. Fetch VIX from yfinance
  2. Fetch market news sentiment from Finnhub over a basket of proxy symbols
  3. Compute CGRS-lite composite score (0-100)
  4. Classify into LOW / MODERATE / HIGH / EXTREME
  5. Persist the snapshot to Redis for the rebalancer to read

CGRS formula:  0.6 * vix_score + 0.4 * sentiment_risk_score
  vix_score        = min(100, vix / 50 * 100)          — VIX 50 = max risk
  sentiment_risk   = (1 - sentiment) * 50               — bullish=0 risk, bearish=100 risk

Thresholds: < 25 LOW | < 50 MODERATE | < 75 HIGH | >= 75 EXTREME

Usage:
    PYTHONPATH=src python -m tokenomics.risk.regime_job

Environment variables:
    FINNHUB_API_KEY        Finnhub API key (required)
    REDIS_HOST/PORT/PW     Redis connection
    SENTIMENT_SYMBOLS      Comma-separated proxy tickers (default: AAPL,MSFT,AMZN,GOOGL,JPM)
    VIX_TICKERS            Comma-separated VIX tickers (default: ^VIX)
    REGIME_NAMESPACE       Redis key namespace (default: risk:regime)
"""

import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

import finnhub
import structlog
import yfinance as yf
from pydantic_settings import BaseSettings

from tokenomics.risk.regime import RegimeSnapshot, RegimeStore, RiskRegime


class RegimeSecrets(BaseSettings):
    finnhub_api_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def _setup_logging() -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=shared,
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for noisy in ["urllib3", "httpcore", "httpx", "yfinance", "peewee"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)


_setup_logging()
logger = structlog.get_logger(__name__)


def fetch_vix(tickers: list[str]) -> tuple[float, str]:
    """Download VIX from yfinance. Returns (value, source_ticker).

    Tries each ticker in order and returns the first successful reading.
    Returns (nan, "unavailable") if all tickers fail.
    """
    for ticker in tickers:
        try:
            hist = yf.download(ticker, period="5d", interval="1d", progress=False, auto_adjust=True)
            if not hist.empty:
                val = float(hist["Close"].iloc[-1])
                logger.info("regime_job.vix_fetched", ticker=ticker, vix=round(val, 2))
                return val, ticker
        except Exception as e:
            logger.warning("regime_job.vix_fetch_error", ticker=ticker, error=str(e))
    logger.warning("regime_job.vix_unavailable")
    return float("nan"), "unavailable"


def fetch_sentiment(client: finnhub.Client, symbols: list[str]) -> float:
    """Average market sentiment over proxy symbols via Finnhub news_sentiment.

    Finnhub returns bullishPercent / bearishPercent under the 'sentiment' key.
    Score = bullishPercent - bearishPercent, range [-1, +1].

    Returns 0.0 (neutral) if no data is available.
    """
    scores: list[float] = []
    for symbol in symbols:
        try:
            resp = client.news_sentiment(symbol)
            s = resp.get("sentiment", {})
            bullish = s.get("bullishPercent")
            bearish = s.get("bearishPercent")
            if bullish is not None and bearish is not None:
                score = float(bullish) - float(bearish)
                scores.append(score)
                logger.info(
                    "regime_job.sentiment_fetched",
                    symbol=symbol,
                    bullish=round(float(bullish), 3),
                    bearish=round(float(bearish), 3),
                    score=round(score, 3),
                )
        except Exception as e:
            logger.warning("regime_job.sentiment_fetch_error", symbol=symbol, error=str(e))

    if not scores:
        logger.warning("regime_job.sentiment_unavailable", fallback=0.0)
        return 0.0

    avg = sum(scores) / len(scores)
    logger.info("regime_job.sentiment_averaged", symbols_scored=len(scores), avg=round(avg, 4))
    return avg


def compute_cgrs(vix: float, sentiment: float) -> float:
    """Compute CGRS-lite: 60% VIX risk component + 40% inverted sentiment.

    vix_score:     0 → 0 risk, 50 → 100 risk (linear, clamped at 100)
    sent_score:    +1 → 0 risk, -1 → 100 risk
    """
    vix_score = 50.0 if math.isnan(vix) else min(100.0, max(0.0, (vix / 50.0) * 100.0))
    sent_score = max(0.0, min(100.0, (1.0 - sentiment) * 50.0))
    return round(0.6 * vix_score + 0.4 * sent_score, 2)


def classify_regime(cgrs: float) -> RiskRegime:
    if cgrs < 25:
        return RiskRegime.LOW
    if cgrs < 50:
        return RiskRegime.MODERATE
    if cgrs < 75:
        return RiskRegime.HIGH
    return RiskRegime.EXTREME


def main() -> int:
    start = datetime.now(timezone.utc)

    print("=" * 70)
    print("TOKENOMICS RISK REGIME JOB")
    print("=" * 70)
    print(f"Start Time: {start.isoformat()}")
    print()

    try:
        secrets = RegimeSecrets()

        sentiment_symbols = [
            s.strip()
            for s in os.getenv("SENTIMENT_SYMBOLS", "AAPL,MSFT,AMZN,GOOGL,JPM").split(",")
            if s.strip()
        ]
        vix_tickers = [
            t.strip()
            for t in os.getenv("VIX_TICKERS", "^VIX").split(",")
            if t.strip()
        ]
        regime_namespace = os.getenv("REGIME_NAMESPACE", "risk:regime")

        print("Configuration:")
        print(f"  VIX tickers:       {', '.join(vix_tickers)}")
        print(f"  Sentiment symbols: {', '.join(sentiment_symbols)}")
        print(f"  Regime namespace:  {regime_namespace}")
        print()

        fh_client = finnhub.Client(api_key=secrets.finnhub_api_key)
        store = RegimeStore(namespace=regime_namespace)

        # --- Fetch inputs ---
        print("Fetching VIX...")
        vix, vix_source = fetch_vix(vix_tickers)
        if math.isnan(vix):
            print("  VIX: unavailable (using neutral fallback in CGRS)")
        else:
            print(f"  VIX ({vix_source}): {vix:.2f}")
        print()

        print(f"Fetching Finnhub sentiment ({len(sentiment_symbols)} symbols)...")
        sentiment = fetch_sentiment(fh_client, sentiment_symbols)
        sentiment_label = "+bullish" if sentiment > 0.05 else ("-bearish" if sentiment < -0.05 else "neutral")
        print(f"  Avg sentiment: {sentiment:+.4f}  ({sentiment_label})")
        print()

        # --- Compute CGRS ---
        cgrs = compute_cgrs(vix, sentiment)
        regime = classify_regime(cgrs)

        vix_component = 50.0 if math.isnan(vix) else min(100.0, (vix / 50.0) * 100.0)
        sent_component = max(0.0, min(100.0, (1.0 - sentiment) * 50.0))

        print("CGRS Computation:")
        print(f"  VIX score:      {vix_component:6.1f}/100  × 60% = {0.6 * vix_component:.1f}")
        print(f"  Sentiment risk: {sent_component:6.1f}/100  × 40% = {0.4 * sent_component:.1f}")
        print(f"  CGRS-lite:      {cgrs:6.2f}/100")
        print(f"  Regime:         {regime.value}")
        print()

        # Position multiplier reference table
        _MULTIPLIERS = {
            "LOW":      (1.00, 1.00),
            "MODERATE": (1.00, 0.75),
            "HIGH":     (0.80, 0.50),
            "EXTREME":  (0.60, 0.00),
        }
        print("Position size multipliers (default / cyclical):")
        print(f"  {'Regime':<12} {'Default':>8} {'Cyclical':>10}")
        print(f"  {'-' * 32}")
        for name, (dflt, cyc) in _MULTIPLIERS.items():
            marker = "  ◄ current" if name == regime.value else ""
            print(f"  {name:<12} {dflt:>7.0%} {cyc:>9.0%}{marker}")
        print()

        # --- Store ---
        snapshot = RegimeSnapshot(
            date=date.today().isoformat(),
            vix=round(vix, 4) if not math.isnan(vix) else -1.0,
            sentiment=round(sentiment, 4),
            cgrs=cgrs,
            regime=regime,
            updated_at=start.isoformat(),
        )
        store.save(snapshot)
        store.close()

        end = datetime.now(timezone.utc)
        duration = (end - start).total_seconds()

        print("=" * 70)
        print("JOB SUMMARY")
        print("=" * 70)
        print(f"  VIX:       {vix:.2f}" if not math.isnan(vix) else "  VIX:       unavailable")
        print(f"  Sentiment: {sentiment:+.4f}")
        print(f"  CGRS:      {cgrs:.2f}")
        print(f"  Regime:    {regime.value}")
        print(f"  Duration:  {duration:.1f}s")
        print()
        print("Job completed successfully!")
        print("=" * 70)

        logger.info(
            "regime_job.completed",
            vix=round(vix, 2) if not math.isnan(vix) else None,
            sentiment=round(sentiment, 4),
            cgrs=cgrs,
            regime=regime.value,
            duration_seconds=round(duration, 1),
        )
        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        logger.error("regime_job.fatal_error", error=str(e), exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
