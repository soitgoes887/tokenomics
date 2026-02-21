"""Configuration loading and validation using Pydantic."""

import os
from pathlib import Path

from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class StrategyConfig(BaseModel):
    name: str
    capital_usd: float = Field(gt=0)
    position_size_min_usd: float = Field(gt=0)
    position_size_max_usd: float = Field(gt=0)
    max_open_positions: int = Field(ge=1, le=100)
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


class RebalancingConfig(BaseModel):
    """Configuration for score-based portfolio rebalancing."""

    top_n_stocks: int = Field(default=50, ge=1, le=500)
    weighting: Literal["score", "equal"] = "score"
    max_position_pct: float = Field(default=5.0, gt=0, le=100)
    max_sector_pct: float = Field(default=25.0, gt=0, le=100)
    min_score: float = Field(default=50.0, ge=0, le=100)
    rebalance_threshold_pct: float = Field(default=20.0, ge=0, le=100)
    min_trade_usd: float = Field(default=100.0, ge=0)


class ScoringProfileConfig(BaseModel):
    """Configuration for a single scoring profile."""

    scorer_class: str
    redis_namespace: str
    alpaca_api_key_env: str
    alpaca_secret_key_env: str
    description: str = ""
    scorer_kwargs: dict[str, float] = Field(default_factory=dict)


class ScoringProfilesConfig(BaseModel):
    """Container for all scoring profiles."""

    profiles: dict[str, ScoringProfileConfig]
    default_profile: str

    @model_validator(mode="after")
    def default_must_exist(self):
        if self.default_profile not in self.profiles:
            raise ValueError(
                f"default_profile '{self.default_profile}' not found in profiles: "
                f"{list(self.profiles.keys())}"
            )
        return self


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
    sentiment: Optional[SentimentConfig] = None
    risk: Optional[RiskConfig] = None
    news: Optional[NewsConfig] = None
    trading: TradingConfig
    logging: LoggingConfig
    rebalancing: RebalancingConfig = Field(default_factory=RebalancingConfig)
    scoring_profiles: Optional[ScoringProfilesConfig] = None


class Secrets(BaseSettings):
    """Loaded from .env file automatically."""

    alpaca_api_key: str
    alpaca_secret_key: str
    gemini_api_key: str
    finnhub_api_key: str = ""
    perplexity_api_key: str = ""
    marketaux_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def load_config(config_path: Path = Path("config/settings.yaml")) -> AppConfig:
    """Load and validate application configuration from YAML."""
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    # Transform scoring_profiles YAML structure into Pydantic model format
    if "scoring_profiles" in raw:
        sp = raw["scoring_profiles"]
        default_profile = sp.pop("default_profile", None)
        # Remaining keys are profile names
        if default_profile and sp:
            raw["scoring_profiles"] = {
                "profiles": sp,
                "default_profile": default_profile,
            }
        else:
            # Malformed section — drop it so AppConfig uses None
            del raw["scoring_profiles"]

    return AppConfig(**raw)


# Synthetic default profile for backward compatibility
_SYNTHETIC_DEFAULT = ScoringProfileConfig(
    scorer_class="FundamentalsScorer",
    redis_namespace="fundamentals",
    alpaca_api_key_env="ALPACA_API_KEY",
    alpaca_secret_key_env="ALPACA_SECRET_KEY",
    description="Default profile (legacy)",
)


def resolve_profile(config: AppConfig) -> tuple[str, ScoringProfileConfig]:
    """Resolve the active scoring profile.

    Priority:
    1. SCORING_PROFILE env var
    2. default_profile from config
    3. Synthetic default (backward compatible)

    Returns:
        Tuple of (profile_name, ScoringProfileConfig)

    Raises:
        ValueError: If SCORING_PROFILE env var references an unknown profile
    """
    if config.scoring_profiles is None:
        return ("default", _SYNTHETIC_DEFAULT)

    profiles = config.scoring_profiles
    env_profile = os.getenv("SCORING_PROFILE")

    if env_profile:
        if env_profile not in profiles.profiles:
            raise ValueError(
                f"SCORING_PROFILE='{env_profile}' not found in configured profiles: "
                f"{list(profiles.profiles.keys())}"
            )
        return (env_profile, profiles.profiles[env_profile])

    return (profiles.default_profile, profiles.profiles[profiles.default_profile])


class ProfileSecrets:
    """Resolves Alpaca API keys from env var names specified in a profile."""

    def __init__(self, profile: ScoringProfileConfig):
        self.alpaca_api_key = os.getenv(profile.alpaca_api_key_env, "")
        self.alpaca_secret_key = os.getenv(profile.alpaca_secret_key_env, "")
