"""Tests for post-scoring filters in refresh_job."""

import pytest

from tokenomics.config import PostFiltersConfig
from tokenomics.fundamentals.refresh_job import (
    _issuer_key,
    apply_post_filters,
)
from tokenomics.fundamentals.scorer import FundamentalsScore
from tokenomics.models import BasicFinancials


def _make_score(
    symbol: str,
    composite: float = 60.0,
    quality: float | None = 70.0,
    value: float | None = 50.0,
    momentum: float | None = 50.0,
    lowvol: float | None = 50.0,
) -> FundamentalsScore:
    return FundamentalsScore(
        symbol=symbol,
        composite_score=composite,
        has_sufficient_data=True,
        value_score=value,
        quality_score=quality,
        momentum_score=momentum,
        lowvol_score=lowvol,
    )


def _make_financials(symbol: str) -> BasicFinancials:
    return BasicFinancials(symbol=symbol)


def _make_batch_item(symbol: str, **kwargs):
    return (_make_financials(symbol), _make_score(symbol, **kwargs))


class TestQualityFilter:
    def test_removes_low_quality(self):
        filters = PostFiltersConfig(min_quality=50.0)
        batch = [
            _make_batch_item("GOOD", quality=75.0),
            _make_batch_item("BAD", quality=30.0),
            _make_batch_item("EDGE", quality=50.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 2
        assert {s.symbol for _, s in filtered} == {"GOOD", "EDGE"}
        assert "BAD" in removed
        assert "quality_score" in removed["BAD"]

    def test_none_quality_not_filtered(self):
        """Stocks without quality sub-score (v2 scorer) are not filtered."""
        filters = PostFiltersConfig(min_quality=50.0)
        batch = [_make_batch_item("NOQA", quality=None)]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1
        assert len(removed) == 0

    def test_disabled_when_none(self):
        filters = PostFiltersConfig(min_quality=None)
        batch = [_make_batch_item("LOW", quality=10.0)]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1
        assert len(removed) == 0


class TestSpeculativeFilter:
    def test_removes_speculative(self):
        filters = PostFiltersConfig(speculative_lowvol=10.0, speculative_value=30.0)
        batch = [
            _make_batch_item("SPEC", lowvol=5.0, value=20.0),
            _make_batch_item("SAFE", lowvol=60.0, value=50.0),
            _make_batch_item("LOWVOL_ONLY", lowvol=5.0, value=50.0),
            _make_batch_item("VALUE_ONLY", lowvol=60.0, value=10.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 3
        assert {s.symbol for _, s in filtered} == {"SAFE", "LOWVOL_ONLY", "VALUE_ONLY"}
        assert "SPEC" in removed
        assert "speculative" in removed["SPEC"]

    def test_both_thresholds_required(self):
        """Filter only activates when both lowvol and value thresholds are set."""
        filters = PostFiltersConfig(speculative_lowvol=10.0, speculative_value=None)
        batch = [_make_batch_item("SPEC", lowvol=5.0, value=20.0)]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1
        assert len(removed) == 0

    def test_none_subscores_not_filtered(self):
        """Stocks without both sub-scores are not caught by speculative filter."""
        filters = PostFiltersConfig(speculative_lowvol=10.0, speculative_value=30.0)
        batch = [_make_batch_item("MISS", lowvol=None, value=None)]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1


class TestDeduplicateShareClasses:
    def test_keeps_higher_scored_class(self):
        filters = PostFiltersConfig(deduplicate_share_classes=True)
        batch = [
            _make_batch_item("GOOG", composite=80.0),
            _make_batch_item("GOOGL", composite=75.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1
        assert filtered[0][1].symbol == "GOOG"
        assert "GOOGL" in removed
        assert "duplicate share class" in removed["GOOGL"]

    def test_dot_suffix_dedup(self):
        filters = PostFiltersConfig(deduplicate_share_classes=True)
        batch = [
            _make_batch_item("BF.A", composite=65.0),
            _make_batch_item("BF.B", composite=70.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 1
        assert filtered[0][1].symbol == "BF.B"
        assert "BF.A" in removed

    def test_no_false_dedup_unrelated(self):
        """Unrelated symbols are not deduplicated."""
        filters = PostFiltersConfig(deduplicate_share_classes=True)
        batch = [
            _make_batch_item("AAPL", composite=80.0),
            _make_batch_item("MSFT", composite=75.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 2
        assert len(removed) == 0

    def test_dedup_with_cached_scores(self):
        """Dedup considers cached scores not in current batch."""
        filters = PostFiltersConfig(deduplicate_share_classes=True)
        # Only GOOGL is in the current batch
        batch = [_make_batch_item("GOOGL", composite=75.0)]
        # But GOOG is cached with a higher score
        scores = {
            "GOOGL": _make_score("GOOGL", composite=75.0),
            "GOOG": _make_score("GOOG", composite=85.0),
        }

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 0
        assert "GOOGL" in removed

    def test_disabled_by_default(self):
        filters = PostFiltersConfig()
        batch = [
            _make_batch_item("GOOG", composite=80.0),
            _make_batch_item("GOOGL", composite=75.0),
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        assert len(filtered) == 2
        assert len(removed) == 0


class TestIssuerKey:
    def test_known_classes(self):
        assert _issuer_key("GOOG") == "GOOG"
        assert _issuer_key("GOOGL") == "GOOG"
        assert _issuer_key("BRK.A") == "BRK"
        assert _issuer_key("BRK.B") == "BRK"
        assert _issuer_key("BF.A") == "BF"
        assert _issuer_key("BF.B") == "BF"
        assert _issuer_key("FOX") == "FOX"
        assert _issuer_key("FOXA") == "FOX"

    def test_unknown_dot_suffix_stripped(self):
        assert _issuer_key("BIO.B") == "BIO"
        assert _issuer_key("XYZ.A") == "XYZ"

    def test_plain_symbol_unchanged(self):
        assert _issuer_key("AAPL") == "AAPL"
        assert _issuer_key("MSFT") == "MSFT"


class TestCombinedFilters:
    def test_all_filters_applied(self):
        """Quality, speculative, and dedup all apply in sequence."""
        filters = PostFiltersConfig(
            min_quality=50.0,
            speculative_lowvol=10.0,
            speculative_value=30.0,
            deduplicate_share_classes=True,
        )
        batch = [
            _make_batch_item("GOOD", composite=80.0, quality=70.0, lowvol=50.0, value=60.0),
            _make_batch_item("LOW_Q", composite=70.0, quality=30.0),  # filtered by quality
            _make_batch_item("SPEC", composite=60.0, quality=60.0, lowvol=5.0, value=20.0),  # speculative
            _make_batch_item("GOOG", composite=85.0, quality=80.0),
            _make_batch_item("GOOGL", composite=75.0, quality=70.0),  # dedup
        ]
        scores = {s.symbol: s for _, s in batch}

        filtered, removed = apply_post_filters(batch, scores, filters)

        kept = {s.symbol for _, s in filtered}
        assert kept == {"GOOD", "GOOG"}
        assert "LOW_Q" in removed
        assert "SPEC" in removed
        assert "GOOGL" in removed

    def test_empty_batch(self):
        filters = PostFiltersConfig(min_quality=50.0, deduplicate_share_classes=True)
        filtered, removed = apply_post_filters([], {}, filters)
        assert filtered == []
        assert removed == {}


class TestPostFiltersConfig:
    def test_defaults(self):
        config = PostFiltersConfig()
        assert config.min_quality is None
        assert config.speculative_lowvol is None
        assert config.speculative_value is None
        assert config.deduplicate_share_classes is False

    def test_from_dict(self):
        config = PostFiltersConfig(
            min_quality=50.0,
            speculative_lowvol=10.0,
            speculative_value=30.0,
            deduplicate_share_classes=True,
        )
        assert config.min_quality == 50.0
        assert config.speculative_lowvol == 10.0
        assert config.speculative_value == 30.0
        assert config.deduplicate_share_classes is True

    def test_validation_range(self):
        with pytest.raises(Exception):
            PostFiltersConfig(min_quality=101.0)
        with pytest.raises(Exception):
            PostFiltersConfig(min_quality=-1.0)
