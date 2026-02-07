"""Abstract base class for LLM sentiment analysis providers."""

from abc import ABC, abstractmethod

from tokenomics.models import NewsArticle, SentimentResult


class LLMProvider(ABC):
    """Interface for LLM-based sentiment analysis."""

    @abstractmethod
    def analyze(self, article: NewsArticle, symbol: str) -> SentimentResult | None:
        """Analyze a single article for a single symbol. Returns None on failure."""
        ...

    @abstractmethod
    def analyze_batch(self, articles: list[NewsArticle]) -> list[SentimentResult]:
        """Analyze multiple articles. One result per (article, symbol) pair."""
        ...
