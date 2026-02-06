"""Verify connectivity to all external APIs."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tokenomics.config import Secrets


def check_alpaca(secrets: Secrets) -> bool:
    """Verify Alpaca API credentials."""
    print("Checking Alpaca API...")
    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
            paper=True,
        )
        account = client.get_account()
        print(f"  Account status: {account.status}")
        print(f"  Equity: ${float(account.equity):,.2f}")
        print(f"  Buying power: ${float(account.buying_power):,.2f}")
        print(f"  Paper trading: Yes")
        print("  Alpaca: OK")
        return True
    except Exception as e:
        print(f"  Alpaca: FAILED - {e}")
        return False


def check_alpaca_news(secrets: Secrets) -> bool:
    """Verify Alpaca News API access."""
    print("\nChecking Alpaca News API...")
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest

        client = NewsClient(
            api_key=secrets.alpaca_api_key,
            secret_key=secrets.alpaca_secret_key,
        )
        request = NewsRequest(limit=3)
        news = client.get_news(request)
        print(f"  Fetched {len(news.news)} articles")
        if news.news:
            print(f"  Latest: {news.news[0].headline[:80]}...")
        print("  Alpaca News: OK")
        return True
    except Exception as e:
        print(f"  Alpaca News: FAILED - {e}")
        return False


def check_gemini(secrets: Secrets) -> bool:
    """Verify Google Gemini API credentials."""
    print("\nChecking Gemini API...")
    try:
        from google import genai

        client = genai.Client(api_key=secrets.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents="Respond with exactly: OK",
        )
        print(f"  Response: {response.text.strip()}")
        print("  Gemini: OK")
        return True
    except Exception as e:
        print(f"  Gemini: FAILED - {e}")
        return False


def main():
    print("=" * 50)
    print("Tokenomics - API Connectivity Check")
    print("=" * 50)

    try:
        secrets = Secrets()
    except Exception as e:
        print(f"\nFailed to load .env file: {e}")
        print("Make sure .env exists with ALPACA_API_KEY, ALPACA_SECRET_KEY, GEMINI_API_KEY")
        sys.exit(1)

    results = [
        check_alpaca(secrets),
        check_alpaca_news(secrets),
        check_gemini(secrets),
    ]

    print("\n" + "=" * 50)
    if all(results):
        print("All checks passed. Ready to trade.")
    else:
        print("Some checks failed. Fix the issues above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
