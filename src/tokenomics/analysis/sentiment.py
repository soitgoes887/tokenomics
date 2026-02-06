"""LLM-based sentiment analysis using Google Gemini."""

import json

import structlog
from google import genai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tokenomics.config import AppConfig, Secrets
from tokenomics.logging_config import get_decision_logger
from tokenomics.models import (
    NewsArticle,
    Sentiment,
    SentimentResult,
    TimeHorizon,
)

logger = structlog.get_logger(__name__)

SENTIMENT_PROMPT = """You are a financial analyst specializing in news-driven equity trading. \
Analyze the following news article and assess its impact on the stock {symbol}.

Title: {headline}
Source: {source}
Published: {created_at}
Summary: {summary}
{content_section}

Provide your analysis as JSON with these exact fields:
- "sentiment": exactly one of "BULLISH", "NEUTRAL", or "BEARISH"
- "conviction": integer 0-100 representing confidence in the sentiment direction
- "time_horizon": "SHORT" (1-5 days), "MEDIUM" (1-4 weeks), or "LONG" (1-3 months)
- "reasoning": 2-3 sentence explanation
- "key_factors": list of 2-4 key factors driving your assessment

Rules:
- Only output BULLISH if the news clearly favors price appreciation
- Only output BEARISH if the news clearly suggests price decline
- Use NEUTRAL for ambiguous, mixed, or irrelevant news
- Conviction below 50 means you are uncertain -- prefer NEUTRAL in that case
- Consider the source credibility and whether this is new information or already priced in
- Focus on material impact, not noise"""


class SentimentAnalyzer:
    """Analyzes news articles for trading sentiment using Gemini."""

    def __init__(self, config: AppConfig, secrets: Secrets):
        self._config = config.sentiment
        self._client = genai.Client(api_key=secrets.gemini_api_key)
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
            response = self._client.models.generate_content(
                model=self._config.model,
                contents=prompt,
                config={
                    "temperature": self._config.temperature,
                    "max_output_tokens": self._config.max_output_tokens,
                    "response_mime_type": "application/json",
                },
            )

            result = self._parse_response(response.text, article, symbol)

            self._decision_log.info(
                "decision.analyzed",
                article_id=article.id,
                symbol=symbol,
                sentiment=result.sentiment.value,
                conviction=result.conviction,
                time_horizon=result.time_horizon.value,
                reasoning=result.reasoning,
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
            )
            return None

    def _build_prompt(self, article: NewsArticle, symbol: str) -> str:
        """Format the prompt template with article data."""
        content_section = ""
        if article.content:
            # Truncate long content to avoid exceeding token limits
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
        """Parse and validate the JSON response from Gemini."""
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
