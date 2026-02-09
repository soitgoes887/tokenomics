"""Configuration loading and validation using Pydantic."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class StrategyConfig(BaseModel):
    name: str
    capital_usd: float = Field(gt=0)
    position_size_min_usd: float = Field(gt=0)
    position_size_max_usd: float = Field(gt=0)
    max_open_positions: int = Field(ge=1, le=20)
    target_new_positions_per_month: int = Field(ge=1)

    @field_validator("position_size_max_usd")
    @classmethod
    def max_gte_min(cls, v, info):
        if "position_size_min_usd" in info.data and v < info.data["position_size_min_usd"]:
            raise ValueError("position_size_max_usd must be >= position_size_min_usd")
        return v


class SentimentConfig(BaseModel):
    model: str
    min_conviction: int = Field(ge=0, le=100)
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(gt=0)


class RiskConfig(BaseModel):
    stop_loss_pct: float = Field(gt=0, lt=1)
    take_profit_pct: float = Field(gt=0, lt=1)
    max_hold_trading_days: int = Field(gt=0)
    daily_loss_limit_pct: float = Field(gt=0, lt=1)
    monthly_loss_limit_pct: float = Field(gt=0, lt=1)


class NewsConfig(BaseModel):
    poll_interval_seconds: int = Field(ge=10)
    symbols: list[str] = Field(default_factory=list)
    include_content: bool = True
    exclude_contentless: bool = True
    lookback_minutes: int = Field(ge=1, le=60)


class TradingConfig(BaseModel):
    paper: bool = True
    market_hours_only: bool = True
    order_type: str = "market"
    time_in_force: str = "day"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    trade_log: str
    decision_log: str
    app_log: str
    max_bytes: int = 10485760
    backup_count: int = 5


class ProvidersConfig(BaseModel):
    news: str = "alpaca"
    llm: str = "gemini-flash"
    broker: str = "alpaca-paper"


class AppConfig(BaseModel):
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    strategy: StrategyConfig
    sentiment: SentimentConfig
    risk: RiskConfig
    news: NewsConfig
    trading: TradingConfig
    logging: LoggingConfig


class Secrets(BaseSettings):
    """Loaded from .env file automatically."""

    alpaca_api_key: str
    alpaca_secret_key: str
    gemini_api_key: str
    finnhub_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def load_config(config_path: Path = Path("config/settings.yaml")) -> AppConfig:
    """Load and validate application configuration from YAML."""
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return AppConfig(**raw)
