"""Provider factory — creates the right implementation based on config."""

from tokenomics.analysis.base import LLMProvider
from tokenomics.config import AppConfig, Secrets
from tokenomics.trading.base import BrokerProvider

LLM_PROVIDERS = {
    "gemini-flash": "tokenomics.analysis.sentiment:GeminiLLMProvider",
    "perplexity-sonar": "tokenomics.analysis.perplexity:PerplexityLLMProvider",
}

BROKER_PROVIDERS = {
    "alpaca-paper": "tokenomics.trading.broker:AlpacaBrokerProvider",
    "alpaca-live": "tokenomics.trading.broker:AlpacaBrokerProvider",
}


def _import_class(path: str):
    """Import a class from a 'module:ClassName' string."""
    module_path, class_name = path.split(":")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def create_llm_provider(config: AppConfig, secrets: Secrets) -> LLMProvider:
    """Create an LLM provider based on config.providers.llm."""
    name = config.providers.llm
    if name not in LLM_PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider: '{name}'. Available: {list(LLM_PROVIDERS.keys())}"
        )
    cls = _import_class(LLM_PROVIDERS[name])
    return cls(config, secrets)


def create_broker_provider(config: AppConfig, secrets: Secrets) -> BrokerProvider:
    """Create a broker provider based on config.providers.broker."""
    name = config.providers.broker
    if name not in BROKER_PROVIDERS:
        raise ValueError(
            f"Unknown broker provider: '{name}'. Available: {list(BROKER_PROVIDERS.keys())}"
        )
    cls = _import_class(BROKER_PROVIDERS[name])
    return cls(config, secrets)
