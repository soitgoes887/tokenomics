"""LLM-based sentiment analysis using Perplexity Sonar."""

import json

import structlog
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tokenomics.analysis.base import LLMProvider
from tokenomics.analysis.sentiment import SENTIMENT_PROMPT
from tokenomics.config import AppConfig, Secrets
from tokenomics.logging_config import get_decision_logger
from tokenomics.models import (
    NewsArticle,
    Sentiment,
    SentimentResult,
    TimeHorizon,
)

logger = structlog.get_logger(__name__)


PERPLEXITY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "sentiment_analysis",
        "schema": {
            "type": "object",
            "properties": {
                "sentiment": {
                    "type": "string",
                    "enum": ["BULLISH", "NEUTRAL", "BEARISH"],
                },
                "conviction": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "time_horizon": {
                    "type": "string",
                    "enum": ["SHORT", "MEDIUM", "LONG"],
                },
                "reasoning": {"type": "string"},
                "key_factors": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "sentiment",
                "conviction",
                "time_horizon",
                "reasoning",
                "key_factors",
            ],
        },
    },
}


class PerplexityLLMProvider(LLMProvider):
    """Analyzes news articles for trading sentiment using Perplexity Sonar."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        if not secrets.perplexity_api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY is required when using perplexity-sonar provider. "
                "Set it in .env or as an environment variable."
            )
        self._config = config.sentiment
        self._client = OpenAI(
            api_key=secrets.perplexity_api_key,
            base_url="https://api.perplexity.ai",
        )
        self._decision_log = get_decision_logger()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def analyze(self, article: NewsArticle, symbol: str) -> SentimentResult | None:
        """Analyze a single article for a single symbol. Returns None on parse failure."""
        prompt = self._build_prompt(article, symbol)

        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._config.temperature,
                max_tokens=self._config.max_output_tokens,
                response_format=PERPLEXITY_RESPONSE_FORMAT,
            )

            result = self._parse_response(
                response.choices[0].message.content, article, symbol
            )

            self._decision_log.info(
                "decision.analyzed",
                article_id=article.id,
                symbol=symbol,
                sentiment=result.sentiment.value,
                conviction=result.conviction,
                time_horizon=result.time_horizon.value,
                reasoning=result.reasoning,
                provider="perplexity",
            )

            return result

        except (ConnectionError, TimeoutError):
            raise  # Let tenacity retry these
        except Exception as e:
            logger.error(
                "sentiment.analysis_failed",
                article_id=article.id,
                symbol=symbol,
                error=str(e),
                provider="perplexity",
            )
            return None

    def _build_prompt(self, article: NewsArticle, symbol: str) -> str:
        """Format the prompt template with article data."""
        content_section = ""
        if article.content:
            content = article.content[:3000]
            content_section = f"Full Content: {content}"

        return SENTIMENT_PROMPT.format(
            symbol=symbol,
            headline=article.headline,
            source=article.source,
            created_at=article.created_at.isoformat(),
            summary=article.summary,
            content_section=content_section,
        )

    def _parse_response(
        self, response_text: str, article: NewsArticle, symbol: str
    ) -> SentimentResult:
        """Parse and validate the JSON response from Perplexity."""
        data = json.loads(response_text)

        return SentimentResult(
            article_id=article.id,
            headline=article.headline,
            symbol=symbol,
            sentiment=Sentiment(data["sentiment"]),
            conviction=int(data["conviction"]),
            time_horizon=TimeHorizon(data["time_horizon"]),
            reasoning=data["reasoning"],
            key_factors=data.get("key_factors", []),
        )

    def analyze_batch(self, articles: list[NewsArticle]) -> list[SentimentResult]:
        """Analyze multiple articles. One result per (article, symbol) pair."""
        results = []
        for article in articles:
            for symbol in article.symbols:
                result = self.analyze(article, symbol)
                if result is not None:
                    results.append(result)
        return results
