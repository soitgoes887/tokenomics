"""Abstract base class for news providers."""

from abc import ABC, abstractmethod

from tokenomics.models import NewsArticle


class NewsProvider(ABC):
    """Interface for fetching financial news from any source."""

    @abstractmethod
    def fetch_new_articles(self) -> list[NewsArticle]:
        """Fetch new articles since last poll. Returns only unseen articles."""
        ...

    @abstractmethod
    def get_seen_ids(self) -> set[str]:
        """Return seen article IDs for state persistence."""
        ...

    @abstractmethod
    def restore_seen_ids(self, ids: set[str]) -> None:
        """Restore seen article IDs from persisted state."""
        ...
