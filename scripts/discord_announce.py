"""Post a one-off Discord announcement about the V5/V6 Magic Formula launch.

Prints the message to the console first, then posts it to DISCORD_WEBHOOK_URL.

Usage:
    source .venv/bin/activate
    python scripts/discord_announce.py            # print + post
    python scripts/discord_announce.py --dry-run   # print only, no post
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# Holdings lists attached to the post as separate files.
LIST_FILES = [
    ROOT / "config" / "magic" / "v5-100m.txt",
    ROOT / "config" / "magic" / "v6-1b.txt",
]

MESSAGE = """\
🆕 **New Strategies Live (Jun 19)**

Launched two **Magic Formula** (Joel Greenblatt) strategies on paper trading, $100k each:

📊 **V5 Magic 100M** — top 50 screener, min market cap $100M
📊 **V6 Magic 1B** — top 50 screener, min market cap $1B

⚖️ Both held **equal-weight** (V5: 46 names @ ~2.17%, V6: 48 names @ ~2.08%) after dropping untradeable OTC tickers.

🛒 Initial buys placed: **46 + 48 orders** accepted (0 failures). Market is closed for Juneteenth, so they're queued to fill at **Monday's open (Jun 22)**.

🔁 Monthly cadence: a "magic loader" writes the screener list to Redis, then the standard rebalancer trades into it equal-weight. Refreshing the list = re-pasting the screener output.

📈 They'll appear in the weekly performance update vs SPY alongside V2/V3/V4.

📎 Full holdings lists attached below."""


def _build_files() -> list[tuple]:
    """Build the multipart `files` list from the holdings lists."""
    files = []
    for i, path in enumerate(LIST_FILES):
        if not path.is_file():
            print(f"WARNING: holdings list not found, skipping: {path}")
            continue
        files.append(
            (f"file{i}", (path.name, path.read_bytes(), "text/plain"))
        )
    return files


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print(MESSAGE)
    print("=" * 60)
    print("Attachments: " + ", ".join(p.name for p in LIST_FILES if p.is_file()))

    if dry_run:
        print("\n(dry run — not posting to Discord)")
        return 0

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("\nERROR: DISCORD_WEBHOOK_URL not set in .env")
        return 1

    files = _build_files()
    # First try honoring any configured proxy; if that proxy forbids Discord
    # (some corporate egress proxies do), retry with a direct connection.
    attempts = [
        ("proxy/env", {"timeout": 30}),
        ("direct", {"timeout": 30, "trust_env": False}),
    ]
    last_error = None
    for label, kwargs in attempts:
        try:
            with httpx.Client(**kwargs) as client:
                response = client.post(
                    webhook_url, data={"content": MESSAGE}, files=files
                )
            if response.status_code in (200, 204):
                print(f"\nPosted to Discord successfully ({label}) "
                      f"with {len(files)} attachment(s).")
                return 0
            print(f"\nDiscord post failed via {label} "
                  f"({response.status_code}): {response.text}")
            return 1
        except httpx.HTTPError as e:
            print(f"\n{label} attempt failed: {type(e).__name__}: {e}")
            last_error = e

    print(f"\nCould not reach Discord from this environment ({last_error}). "
          f"The webhook works from the cluster / an unproxied network — "
          f"re-run this script there.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
