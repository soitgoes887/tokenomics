"""Entry point: python -m tokenomics"""

import asyncio
import sys
from pathlib import Path

from tokenomics.config import Secrets, load_config
from tokenomics.engine import TokenomicsEngine
from tokenomics.logging_config import configure_logging


def main():
    config = load_config(Path("config/settings.yaml"))
    try:
        secrets = Secrets()
    except Exception as e:
        print(f"Failed to load secrets from .env: {e}")
        print("Ensure .env exists with ALPACA_API_KEY, ALPACA_SECRET_KEY, GEMINI_API_KEY")
        sys.exit(1)

    configure_logging(config.logging)

    engine = TokenomicsEngine(config, secrets)
    asyncio.run(engine.start())


if __name__ == "__main__":
    main()
