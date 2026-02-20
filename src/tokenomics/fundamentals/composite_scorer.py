"""4-factor cross-sectional composite scorer (Value/Quality/Momentum/LowVol).

Computes percentile ranks across the full universe for each sub-score,
then combines them with configurable weights (default 25/25/25/25).
"""

import numpy as np
import pandas as pd
import structlog

from tokenomics.fundamentals.scorer import BaseScorer, FundamentalsScore
from tokenomics.fundamentals.scorer_registry import register_scorer
from tokenomics.models import BasicFinancials

logger = structlog.get_logger(__name__)


class CompositeScorer(BaseScorer):
    """4-factor composite scorer: Value, Quality, Momentum, LowVol.

    Cross-sectional model — requires the full universe to compute
    z-scores and percentile ranks. Use calculate_scores_batch().
    """

    def __init__(
        self,
        value_weight: float = 0.25,
        quality_weight: float = 0.25,
        momentum_weight: float = 0.25,
        lowvol_weight: float = 0.25,
    ):
        self._weights = {
            "value": value_weight,
            "quality": quality_weight,
            "momentum": momentum_weight,
            "lowvol": lowvol_weight,
        }

    def calculate_score(self, financials: BasicFinancials) -> FundamentalsScore:
        """Single-symbol fallback — returns neutral score.

        Cross-sectional scoring requires the full universe; use
        calculate_scores_batch() instead.
        """
        return FundamentalsScore(
            symbol=financials.symbol,
            composite_score=50.0,
            has_sufficient_data=False,
        )

    def calculate_scores_batch(
        self, financials_list: list[BasicFinancials]
    ) -> list[FundamentalsScore]:
        """Score the full universe cross-sectionally.

        Algorithm:
            1. Derive value/quality/momentum/lowvol metrics
            2. Z-score each metric across the universe
            3. Average z-scores within each factor → percentile rank (1-100)
            4. Composite = weighted sum of factor ranks (re-weight on NaN)
            5. Final score = percentile rank of composite (1-100)
        """
        if not financials_list:
            return []

        # Build DataFrame from financials
        rows = []
        for f in financials_list:
            rows.append({
                "symbol": f.symbol,
                "pe_ratio": f.pe_ratio,
                "price_to_cash_flow": f.price_to_cash_flow,
                "pb_ratio": f.pb_ratio,
                "roe": f.roe,
                "roic": f.roic,
                "gross_margin": f.gross_margin,
                "debt_to_equity": f.debt_to_equity,
                "price_return_52_week": f.price_return_52_week,
                "beta": f.beta,
                "high_52_week": f.high_52_week,
                "low_52_week": f.low_52_week,
            })

        df = pd.DataFrame(rows).set_index("symbol")

        # --- Derived metrics ---
        # Value: higher = cheaper
        df["earnings_yield"] = self._safe_inverse(df["pe_ratio"])
        df["fcfy"] = self._safe_inverse(df["price_to_cash_flow"])
        df["bp"] = self._safe_inverse(df["pb_ratio"])

        # Quality
        df["leverage_score"] = 1.0 / (1.0 + df["debt_to_equity"].clip(lower=0))

        # LowVol
        df["inv_beta"] = self._safe_inverse(df["beta"])
        midprice = (df["high_52_week"] + df["low_52_week"]) / 2.0
        price_range = (df["high_52_week"] - df["low_52_week"]) / midprice
        df["inv_range_vol"] = self._safe_inverse(price_range)

        # --- Sub-scores via z-score → percentile ---
        value_rank = self._avg_z_then_percentile(
            df, ["earnings_yield", "fcfy", "bp"]
        )
        quality_rank = self._avg_z_then_percentile(
            df, ["roe", "roic", "gross_margin", "leverage_score"]
        )
        momentum_rank = self._avg_z_then_percentile(
            df, ["price_return_52_week"]
        )
        lowvol_rank = self._avg_z_then_percentile(
            df, ["inv_beta", "inv_range_vol"]
        )

        # Combine into result DataFrame
        result = pd.DataFrame({
            "value": value_rank,
            "quality": quality_rank,
            "momentum": momentum_rank,
            "lowvol": lowvol_rank,
        }, index=df.index)

        # --- Composite: weighted sum with NaN re-weighting ---
        composite = pd.Series(np.nan, index=result.index)
        non_nan_count = result.notna().sum(axis=1)

        for idx in result.index:
            row = result.loc[idx]
            available = row.dropna()
            if len(available) < 2:
                composite[idx] = np.nan
                continue

            total_w = sum(self._weights[col] for col in available.index)
            if total_w == 0:
                composite[idx] = np.nan
                continue

            composite[idx] = sum(
                available[col] * (self._weights[col] / total_w)
                for col in available.index
            )

        # Final score = percentile rank of composite
        final_rank = self._percentile_rank(composite)

        # --- Build FundamentalsScore objects ---
        scores: list[FundamentalsScore] = []
        for f in financials_list:
            sym = f.symbol
            sufficient = non_nan_count.get(sym, 0) >= 2

            fs = final_rank.get(sym, np.nan)
            score_val = round(float(fs), 2) if pd.notna(fs) else 50.0

            v = result.at[sym, "value"] if pd.notna(result.at[sym, "value"]) else None
            q = result.at[sym, "quality"] if pd.notna(result.at[sym, "quality"]) else None
            m = result.at[sym, "momentum"] if pd.notna(result.at[sym, "momentum"]) else None
            l = result.at[sym, "lowvol"] if pd.notna(result.at[sym, "lowvol"]) else None

            scores.append(FundamentalsScore(
                symbol=sym,
                composite_score=score_val,
                has_sufficient_data=bool(sufficient),
                value_score=round(float(v), 2) if v is not None else None,
                quality_score=round(float(q), 2) if q is not None else None,
                momentum_score=round(float(m), 2) if m is not None else None,
                lowvol_score=round(float(l), 2) if l is not None else None,
            ))

        logger.info(
            "composite_scorer.batch_scored",
            universe_size=len(financials_list),
            scored=sum(1 for s in scores if s.has_sufficient_data),
            insufficient_data=sum(1 for s in scores if not s.has_sufficient_data),
        )

        return scores

    # --- Helpers ---

    @staticmethod
    def _safe_inverse(series: pd.Series) -> pd.Series:
        """Compute 1/x, returning NaN for zero or negative values."""
        safe = series.where(series > 0, other=np.nan)
        return 1.0 / safe

    @staticmethod
    def _zscore(series: pd.Series) -> pd.Series:
        """Standard z-score, ignoring NaNs. NaN inputs stay NaN."""
        mean = series.mean()
        std = series.std()
        if pd.isna(std) or std == 0:
            # No variance — return 0 for non-NaN, NaN for NaN
            return series.where(series.isna(), other=0.0)
        return (series - mean) / std

    @staticmethod
    def _percentile_rank(series: pd.Series) -> pd.Series:
        """Rank as percentile 1-100, NaN stays NaN."""
        ranked = series.rank(pct=True, na_option="keep")
        return (ranked * 100).round(2)

    def _avg_z_then_percentile(
        self, df: pd.DataFrame, cols: list[str]
    ) -> pd.Series:
        """Z-score each column, average, then percentile rank."""
        z_cols = []
        for col in cols:
            if col in df.columns:
                z = self._zscore(df[col])
                z_cols.append(z)

        if not z_cols:
            return pd.Series(np.nan, index=df.index)

        z_df = pd.concat(z_cols, axis=1)
        avg_z = z_df.mean(axis=1, skipna=True)
        # If all components are NaN for a row, mean returns NaN — correct.
        # If only some are NaN, mean of available — correct (partial data).
        # Mark as NaN if ALL components were NaN
        all_nan = z_df.isna().all(axis=1)
        avg_z[all_nan] = np.nan

        return self._percentile_rank(avg_z)


register_scorer("CompositeScorer", CompositeScorer)
