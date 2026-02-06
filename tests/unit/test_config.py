"""Tests for configuration loading and validation."""

import pytest
import yaml
from pathlib import Path

from tokenomics.config import AppConfig, load_config


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
