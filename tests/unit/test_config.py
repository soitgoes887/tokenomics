"""Tests for configuration loading and validation."""

import os

import pytest
import yaml
from pathlib import Path

from tokenomics.config import (
    AppConfig,
    ProfileSecrets,
    ScoringProfileConfig,
    ScoringProfilesConfig,
    load_config,
    resolve_profile,
)


class TestAppConfig:
    def test_valid_config(self, test_config):
        """A valid config should load without errors."""
        assert test_config.strategy.name == "test-strategy"
        assert test_config.strategy.capital_usd == 10000
        assert test_config.sentiment.min_conviction == 70
        assert test_config.risk.stop_loss_pct == 0.025

    def test_position_size_max_must_be_gte_min(self):
        """position_size_max_usd must be >= position_size_min_usd."""
        with pytest.raises(ValueError, match="position_size_max_usd must be >= position_size_min_usd"):
            AppConfig(
                strategy={
                    "name": "test",
                    "capital_usd": 10000,
                    "position_size_min_usd": 1000,
                    "position_size_max_usd": 500,  # less than min
                    "max_open_positions": 10,
                    "target_new_positions_per_month": 15,
                },
                sentiment={
                    "model": "test",
                    "min_conviction": 70,
                    "temperature": 0.1,
                    "max_output_tokens": 512,
                },
                risk={
                    "stop_loss_pct": 0.025,
                    "take_profit_pct": 0.06,
                    "max_hold_trading_days": 65,
                    "daily_loss_limit_pct": 0.05,
                    "monthly_loss_limit_pct": 0.10,
                },
                news={
                    "poll_interval_seconds": 30,
                    "lookback_minutes": 5,
                },
                trading={},
                logging={
                    "trade_log": "t.log",
                    "decision_log": "d.log",
                    "app_log": "a.log",
                },
            )

    def test_capital_must_be_positive(self):
        """capital_usd must be > 0."""
        with pytest.raises(ValueError):
            AppConfig(
                strategy={
                    "name": "test",
                    "capital_usd": 0,
                    "position_size_min_usd": 500,
                    "position_size_max_usd": 1000,
                    "max_open_positions": 10,
                    "target_new_positions_per_month": 15,
                },
                sentiment={
                    "model": "test",
                    "min_conviction": 70,
                    "temperature": 0.1,
                    "max_output_tokens": 512,
                },
                risk={
                    "stop_loss_pct": 0.025,
                    "take_profit_pct": 0.06,
                    "max_hold_trading_days": 65,
                    "daily_loss_limit_pct": 0.05,
                    "monthly_loss_limit_pct": 0.10,
                },
                news={
                    "poll_interval_seconds": 30,
                    "lookback_minutes": 5,
                },
                trading={},
                logging={
                    "trade_log": "t.log",
                    "decision_log": "d.log",
                    "app_log": "a.log",
                },
            )

    def test_conviction_range(self):
        """min_conviction must be 0-100."""
        with pytest.raises(ValueError):
            AppConfig(
                strategy={
                    "name": "test",
                    "capital_usd": 10000,
                    "position_size_min_usd": 500,
                    "position_size_max_usd": 1000,
                    "max_open_positions": 10,
                    "target_new_positions_per_month": 15,
                },
                sentiment={
                    "model": "test",
                    "min_conviction": 150,  # out of range
                    "temperature": 0.1,
                    "max_output_tokens": 512,
                },
                risk={
                    "stop_loss_pct": 0.025,
                    "take_profit_pct": 0.06,
                    "max_hold_trading_days": 65,
                    "daily_loss_limit_pct": 0.05,
                    "monthly_loss_limit_pct": 0.10,
                },
                news={
                    "poll_interval_seconds": 30,
                    "lookback_minutes": 5,
                },
                trading={},
                logging={
                    "trade_log": "t.log",
                    "decision_log": "d.log",
                    "app_log": "a.log",
                },
            )

    def test_load_config_from_yaml(self, tmp_path):
        """load_config should parse a YAML file correctly."""
        config_data = {
            "strategy": {
                "name": "yaml-test",
                "capital_usd": 5000,
                "position_size_min_usd": 250,
                "position_size_max_usd": 500,
                "max_open_positions": 5,
                "target_new_positions_per_month": 10,
            },
            "sentiment": {
                "model": "gemini-2.5-flash-lite",
                "min_conviction": 60,
                "temperature": 0.2,
                "max_output_tokens": 256,
            },
            "risk": {
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.05,
                "max_hold_trading_days": 30,
                "daily_loss_limit_pct": 0.04,
                "monthly_loss_limit_pct": 0.08,
            },
            "news": {
                "poll_interval_seconds": 60,
                "symbols": ["AAPL", "MSFT"],
                "lookback_minutes": 10,
            },
            "trading": {
                "paper": True,
            },
            "logging": {
                "level": "DEBUG",
                "trade_log": "logs/trades.log",
                "decision_log": "logs/decisions.log",
                "app_log": "logs/app.log",
            },
        }

        config_file = tmp_path / "settings.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = load_config(config_file)
        assert config.strategy.name == "yaml-test"
        assert config.strategy.capital_usd == 5000
        assert config.news.symbols == ["AAPL", "MSFT"]
        assert config.sentiment.min_conviction == 60

    def test_load_config_with_scoring_profiles(self, tmp_path):
        """load_config should parse scoring_profiles from YAML."""
        config_data = {
            "strategy": {
                "name": "yaml-test",
                "capital_usd": 5000,
                "position_size_min_usd": 250,
                "position_size_max_usd": 500,
                "max_open_positions": 5,
                "target_new_positions_per_month": 10,
            },
            "trading": {"paper": True},
            "logging": {
                "level": "DEBUG",
                "trade_log": "t.log",
                "decision_log": "d.log",
                "app_log": "a.log",
            },
            "scoring_profiles": {
                "v2_base": {
                    "scorer_class": "FundamentalsScorer",
                    "redis_namespace": "fundamentals:v2_base",
                    "alpaca_api_key_env": "ALPACA_API_KEY",
                    "alpaca_secret_key_env": "ALPACA_SECRET_KEY",
                },
                "v3_comp": {
                    "scorer_class": "CompositeScorer",
                    "redis_namespace": "fundamentals:v3_comp",
                    "alpaca_api_key_env": "ALPACA_API_KEY_V3",
                    "alpaca_secret_key_env": "ALPACA_SECRET_KEY_V3",
                },
                "default_profile": "v2_base",
            },
        }

        config_file = tmp_path / "settings.yaml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = load_config(config_file)
        assert config.scoring_profiles is not None
        assert "v2_base" in config.scoring_profiles.profiles
        assert "v3_comp" in config.scoring_profiles.profiles
        assert config.scoring_profiles.default_profile == "v2_base"
        assert config.scoring_profiles.profiles["v2_base"].scorer_class == "FundamentalsScorer"

    def test_no_scoring_profiles_backward_compat(self, test_config):
        """Config without scoring_profiles should have None."""
        assert test_config.scoring_profiles is None


class TestScoringProfilesConfig:
    def test_valid_profiles(self):
        """Valid profiles should parse correctly."""
        profiles = ScoringProfilesConfig(
            profiles={
                "v2": ScoringProfileConfig(
                    scorer_class="FundamentalsScorer",
                    redis_namespace="fundamentals:v2",
                    alpaca_api_key_env="ALPACA_API_KEY",
                    alpaca_secret_key_env="ALPACA_SECRET_KEY",
                ),
            },
            default_profile="v2",
        )
        assert profiles.default_profile == "v2"

    def test_default_must_exist_in_profiles(self):
        """default_profile must reference an existing profile."""
        with pytest.raises(ValueError, match="not found in profiles"):
            ScoringProfilesConfig(
                profiles={
                    "v2": ScoringProfileConfig(
                        scorer_class="FundamentalsScorer",
                        redis_namespace="fundamentals:v2",
                        alpaca_api_key_env="ALPACA_API_KEY",
                        alpaca_secret_key_env="ALPACA_SECRET_KEY",
                    ),
                },
                default_profile="nonexistent",
            )


class TestResolveProfile:
    def _make_config_with_profiles(self):
        return AppConfig(
            strategy={
                "name": "test",
                "capital_usd": 10000,
                "position_size_min_usd": 500,
                "position_size_max_usd": 1000,
                "max_open_positions": 10,
                "target_new_positions_per_month": 15,
            },
            trading={},
            logging={
                "trade_log": "t.log",
                "decision_log": "d.log",
                "app_log": "a.log",
            },
            scoring_profiles=ScoringProfilesConfig(
                profiles={
                    "v2_base": ScoringProfileConfig(
                        scorer_class="FundamentalsScorer",
                        redis_namespace="fundamentals:v2_base",
                        alpaca_api_key_env="ALPACA_API_KEY",
                        alpaca_secret_key_env="ALPACA_SECRET_KEY",
                    ),
                    "v3_comp": ScoringProfileConfig(
                        scorer_class="CompositeScorer",
                        redis_namespace="fundamentals:v3_comp",
                        alpaca_api_key_env="ALPACA_API_KEY_V3",
                        alpaca_secret_key_env="ALPACA_SECRET_KEY_V3",
                    ),
                },
                default_profile="v2_base",
            ),
        )

    def test_resolve_no_profiles_returns_synthetic(self, test_config):
        """No scoring_profiles section -> synthetic default."""
        name, profile = resolve_profile(test_config)
        assert name == "default"
        assert profile.scorer_class == "FundamentalsScorer"
        assert profile.redis_namespace == "fundamentals"

    def test_resolve_uses_default_profile(self, monkeypatch):
        """Without SCORING_PROFILE env var, use default_profile."""
        monkeypatch.delenv("SCORING_PROFILE", raising=False)
        config = self._make_config_with_profiles()
        name, profile = resolve_profile(config)
        assert name == "v2_base"
        assert profile.redis_namespace == "fundamentals:v2_base"

    def test_resolve_uses_env_var(self, monkeypatch):
        """SCORING_PROFILE env var overrides default."""
        monkeypatch.setenv("SCORING_PROFILE", "v3_comp")
        config = self._make_config_with_profiles()
        name, profile = resolve_profile(config)
        assert name == "v3_comp"
        assert profile.scorer_class == "CompositeScorer"
        assert profile.redis_namespace == "fundamentals:v3_comp"

    def test_resolve_unknown_env_var_raises(self, monkeypatch):
        """SCORING_PROFILE with unknown value should raise."""
        monkeypatch.setenv("SCORING_PROFILE", "nonexistent")
        config = self._make_config_with_profiles()
        with pytest.raises(ValueError, match="not found in configured profiles"):
            resolve_profile(config)


class TestProfileSecrets:
    def test_resolves_keys_from_env(self, monkeypatch):
        """ProfileSecrets should read env vars named in the profile."""
        monkeypatch.setenv("MY_API_KEY", "key123")
        monkeypatch.setenv("MY_SECRET_KEY", "secret456")
        profile = ScoringProfileConfig(
            scorer_class="FundamentalsScorer",
            redis_namespace="test",
            alpaca_api_key_env="MY_API_KEY",
            alpaca_secret_key_env="MY_SECRET_KEY",
        )
        secrets = ProfileSecrets(profile)
        assert secrets.alpaca_api_key == "key123"
        assert secrets.alpaca_secret_key == "secret456"

    def test_missing_env_returns_empty(self, monkeypatch):
        """Missing env vars should return empty strings."""
        monkeypatch.delenv("NONEXISTENT_KEY", raising=False)
        profile = ScoringProfileConfig(
            scorer_class="FundamentalsScorer",
            redis_namespace="test",
            alpaca_api_key_env="NONEXISTENT_KEY",
            alpaca_secret_key_env="NONEXISTENT_KEY",
        )
        secrets = ProfileSecrets(profile)
        assert secrets.alpaca_api_key == ""
        assert secrets.alpaca_secret_key == ""
