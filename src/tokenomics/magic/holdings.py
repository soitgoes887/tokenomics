"""Parse fixed holdings lists and build equal-weight targets.

Shared by the Magic Formula loader job (`magic_job`) and the one-off initial-buy
script so there is a single source of truth for "which tickers, what weight".
"""

from pathlib import Path


def load_holdings(file_path: str) -> list[str]:
    """Load a holdings list from a text file (one ticker per line).

    Blank lines and lines starting with '#' are ignored. Tickers are upper-cased
    and de-duplicated while preserving first-seen order.

    Raises:
        FileNotFoundError: if the file does not exist.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Holdings list not found: {file_path}")

    seen: set[str] = set()
    symbols: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        ticker = stripped.upper()
        if ticker not in seen:
            seen.add(ticker)
            symbols.append(ticker)
    return symbols


def equal_weight_targets(symbols: list[str]) -> dict[str, float]:
    """Return equal target weights summing to 1.0 for the given symbols."""
    unique = list(dict.fromkeys(s.upper() for s in symbols))
    if not unique:
        return {}
    w = 1.0 / len(unique)
    return {s: w for s in unique}
