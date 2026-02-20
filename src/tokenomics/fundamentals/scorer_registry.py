"""Registry for scorer classes, mapping string names to implementations."""

from tokenomics.fundamentals.scorer import BaseScorer, FundamentalsScorer

_REGISTRY: dict[str, type[BaseScorer]] = {}


def register_scorer(name: str, cls: type[BaseScorer]) -> None:
    """Register a scorer class under a string name."""
    _REGISTRY[name] = cls


def get_scorer_class(name: str) -> type[BaseScorer]:
    """Look up a scorer class by name.

    Raises:
        KeyError: If the scorer name is not registered
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown scorer '{name}'. Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def create_scorer(name: str, **kwargs) -> BaseScorer:
    """Create a scorer instance by name.

    Args:
        name: Registered scorer class name
        **kwargs: Passed to the scorer constructor

    Returns:
        Instantiated scorer
    """
    cls = get_scorer_class(name)
    return cls(**kwargs)


# Register built-in scorers
register_scorer("FundamentalsScorer", FundamentalsScorer)
