"""Entry point: python -m tokenomics"""

import sys
from pathlib import Path

from tokenomics.config import Secrets, load_config
from tokenomics.logging_config import configure_logging
from tokenomics.rebalancing.engine import RebalancingEngine


def main():
    config = load_config(Path("config/settings.yaml"))
    try:
        secrets = Secrets()
    except Exception as e:
        print(f"Failed to load secrets from .env: {e}")
        print("Ensure .env exists with ALPACA_API_KEY, ALPACA_SECRET_KEY")
        sys.exit(1)

    configure_logging(config.logging)

    engine = RebalancingEngine(config, secrets)
    sys.exit(engine.run())


if __name__ == "__main__":
    main()
