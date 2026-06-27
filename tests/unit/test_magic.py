"""Tests for the Magic Formula fixed-list profiles (v5/v6)."""

from pathlib import Path

import pytest

from tokenomics.config import load_config
from tokenomics.magic.holdings import equal_weight_targets, load_holdings
from tokenomics.rebalancing.portfolio import compute_target_weights

ROOT = Path(__file__).parent.parent.parent


class TestHoldings:
    def test_load_holdings_skips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "list.txt"
        f.write_text("# header\nADBE\n\n  MO \n# another\nyelp\n")
        assert load_holdings(str(f)) == ["ADBE", "MO", "YELP"]

    def test_load_holdings_dedupes_preserving_order(self, tmp_path):
        f = tmp_path / "list.txt"
        f.write_text("ADBE\nMO\nADBE\nMO\nCI\n")
        assert load_holdings(str(f)) == ["ADBE", "MO", "CI"]

    def test_load_holdings_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_holdings("/nonexistent/list.txt")

    def test_equal_weight_targets_sum_to_one(self):
        w = equal_weight_targets(["A", "B", "C", "D"])
        assert w == {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_equal_weight_empty(self):
        assert equal_weight_targets([]) == {}

    def test_real_lists_load_and_are_nonempty(self):
        v5 = load_holdings(str(ROOT / "config/magic/v5-100m.txt"))
        v6 = load_holdings(str(ROOT / "config/magic/v6-1b.txt"))
        assert len(v5) == len(set(v5)) and len(v5) > 0
        assert len(v6) == len(set(v6)) and len(v6) > 0


class TestMagicProfileConfig:
    def test_profiles_are_equal_weight_with_no_caps(self):
        config = load_config(ROOT / "config/settings.yaml")
        for name in ("tokenomics_v5_magic_100m", "tokenomics_v6_magic_1b"):
            p = config.scoring_profiles.profiles[name]
            assert p.holdings_list is not None
            assert p.rebalancing is not None
            assert p.rebalancing.weighting == "equal"
            assert p.rebalancing.min_score == 0.0
            assert p.rebalancing.max_position_pct == 100.0
            assert p.rebalancing.max_sector_pct == 100.0

    def test_top_n_exceeds_holdings_so_nothing_truncated(self):
        config = load_config(ROOT / "config/settings.yaml")
        for name, path in (
            ("tokenomics_v5_magic_100m", "config/magic/v5-100m.txt"),
            ("tokenomics_v6_magic_1b", "config/magic/v6-1b.txt"),
        ):
            p = config.scoring_profiles.profiles[name]
            holdings = load_holdings(str(ROOT / path))
            assert p.rebalancing.top_n_stocks >= len(holdings)


class TestEqualWeightThroughRebalancer:
    def test_uniform_scores_produce_equal_weights(self):
        """The loader writes uniform scores; the rebalancer must equal-weight them."""
        symbols = [f"S{i}" for i in range(46)]
        scores = [(s, 100.0) for s in symbols]
        target = compute_target_weights(
            scores=scores, top_n=60, weighting="equal",
            max_position_pct=100.0, min_score=0.0, max_sector_pct=100.0,
        )
        assert target.stock_count == 46
        weights = list(target.weights.values())
        assert all(abs(w - 1.0 / 46) < 1e-9 for w in weights)
        assert abs(target.total_weight - 1.0) < 1e-9
