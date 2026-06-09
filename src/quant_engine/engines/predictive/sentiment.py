"""
backend/engine/metrics/sentiment.py
Sector: Options / Sentiment Analysis Engine
[ARCH-1, PD-4]

Theoretical basis:
    Analyzes news summaries, social media indicators, and batched text feeds
    to produce unified sentiment scores, consensus signals, and reputation flags.
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore[import-untyped]

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.sentiment")

# ────────────────────────────────────────────────────────────────
# CONFIGURATION & CONSTANTS
# ────────────────────────────────────────────────────────────────

_SARCASM_PATTERNS: Final[list[str]] = [
    r"\bclaro\b.{0,30}\bsi\b",
    r"\bseguro\b.{0,20}\bjaja\b",
    r"\bcomo\s+no\b",
    r"\boh[,\s]+vaya\b",
    r"\bgenial\b.{0,15}\b(?:no|nunca)\b",
    r"(?:!{2,}|\?{2,})",
    r"\bjajaj+a\b",
]

_CRISIS_KEYWORDS: Final[list[str]] = [
    "boicot",
    "escandalo",
    "fraude",
    "estafa",
    "verguenza",
    "despido",
    "demanda",
    "ilegal",
    "cancelado",
    "corrupcion",
]

_SARCASM_REGEX: Final[re.Pattern[str]] = re.compile(
    "|".join(_SARCASM_PATTERNS), flags=re.IGNORECASE
)
_CRISIS_KW_REGEX: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(_CRISIS_KEYWORDS) + r")\b", flags=re.IGNORECASE
)
_WHITESPACE_REGEX: Final[re.Pattern[str]] = re.compile(r"\s+")
_VADER_ANALYZER: Final[SentimentIntensityAnalyzer] = SentimentIntensityAnalyzer()


class SocialMetricsInput(BaseModel):
    """Immutable input metrics for social sentiment analysis."""

    model_config = ConfigDict(frozen=True)

    twitter_posts: int = Field(ge=0)
    stocktwits_posts: int = Field(ge=0)
    twitter_sentiment: float = Field(ge=0.0, le=1.0)
    stocktwits_sentiment: float = Field(ge=0.0, le=1.0)


class SentimentResult(BaseModel):
    """Unified, immutable model output for sentiment consensus and confidence."""

    model_config = ConfigDict(frozen=True)

    score: float = 0.0
    consensus: str = "NEUTRAL"
    confidence: float = 0.0
    sentiment_score: float = 0.5
    news_count: int = 0
    top_themes: list[str] = Field(default_factory=list)
    buzz_score: float = 0.0
    twitter_impact: float = 0.0
    is_hot: bool = False


class SentimentAnalysisEngine:
    """Consolidated sentiment and reputation analysis engine.

    Purely stateless.
    """

    BULLISH_KEYWORDS: ClassVar[dict[str, float]] = {
        "upgrade": 0.5,
        "beating": 0.4,
        "outperform": 0.3,
        "growth": 0.2,
        "dovish": 0.6,
        "easing": 0.4,
        "buyback": 0.3,
        "breakout": 0.4,
    }

    BEARISH_KEYWORDS: ClassVar[dict[str, float]] = {
        "downgrade": 0.5,
        "missing": 0.4,
        "underperform": 0.3,
        "decline": 0.2,
        "hawkish": 0.6,
        "tightening": 0.4,
        "recession": 0.5,
        "breakdown": 0.4,
    }

    def __init__(self) -> None:
        pass

    def analyze(
        self, news_summaries: list[str], external_indicators: dict[str, Any] | None = None
    ) -> Result[SentimentResult]:
        """Computes a consolidated sentiment score between [-1, 1]."""
        if not news_summaries:
            return Result.failure(reason="news_summaries list cannot be empty")

        try:
            raw_score = 0.0
            themes = set()
            news_count = len(news_summaries)

            news_scores = []
            for news in news_summaries:
                s = 0.0
                content = news.lower()
                for word, weight in self.BULLISH_KEYWORDS.items():
                    if word in content:
                        s += weight
                        themes.add(word)
                for word, weight in self.BEARISH_KEYWORDS.items():
                    if word in content:
                        s -= weight
                        themes.add(word)
                news_scores.append(s)

            raw_score += sum(news_scores) / news_count

            # 2. External Indicators (e.g. Fear & Greed [0, 100])
            if external_indicators:
                fear_greed = external_indicators.get("fear_greed", 50)
                if not isinstance(fear_greed, int | float):
                    return Result.failure(reason="fear_greed indicator must be a numeric value")
                if not 0.0 <= fear_greed <= 100.0:
                    return Result.failure(reason="fear_greed indicator must be between 0 and 100")
                raw_score += (fear_greed - 50.0) / 100.0

            final_score = max(-1.0, min(1.0, raw_score))
            consensus = (
                "BULLISH"
                if final_score >= 0.25
                else "BEARISH"
                if final_score <= -0.25
                else "NEUTRAL"
            )
            sentiment_mic = (final_score + 1.0) / 2.0

            return Result.success(
                SentimentResult(
                    score=round(final_score, 4),
                    consensus=consensus,
                    confidence=0.7 if news_count > 2 else 0.4,
                    sentiment_score=round(sentiment_mic, 4),
                    news_count=news_count,
                    top_themes=list(themes)[:5],
                    buzz_score=min(10.0, news_count * 1.5),
                    twitter_impact=round(abs(final_score), 2),
                    is_hot=news_count > 5,
                )
            )
        except Exception as e:
            logger.error(f"News sentiment analysis failed: {e}")
            return Result.failure(reason=f"News sentiment analysis failed: {e}")

    def analyze_social(self, data: SocialMetricsInput) -> Result[SentimentResult]:
        """Analyzes social media activity and scales scores to [-1, 1]."""
        if data.twitter_posts < 0 or data.stocktwits_posts < 0:
            return Result.failure(reason="Post counts cannot be negative")
        if not (0.0 <= data.twitter_sentiment <= 1.0):
            return Result.failure(reason="twitter_sentiment must be in range [0.0, 1.0]")
        if not (0.0 <= data.stocktwits_sentiment <= 1.0):
            return Result.failure(reason="stocktwits_sentiment must be in range [0.0, 1.0]")

        try:
            avg_sent = (data.twitter_sentiment + data.stocktwits_sentiment) / 2.0
            total_buzz = data.twitter_posts + data.stocktwits_posts

            # Map [0, 1] to [-1, 1]
            score = avg_sent * 2.0 - 1.0
            consensus = (
                "BULLISH"
                if score >= 0.25
                else "BEARISH"
                if score <= -0.25
                else "NEUTRAL"
            )

            return Result.success(
                SentimentResult(
                    score=round(score, 4),
                    consensus=consensus,
                    confidence=0.8 if total_buzz > 500 else 0.5,
                    sentiment_score=round(avg_sent, 4),
                    news_count=int(total_buzz),
                    buzz_score=min(10.0, total_buzz / 100.0),
                    twitter_impact=float(data.twitter_sentiment),
                    is_hot=total_buzz > 1000,
                )
            )
        except Exception as e:
            logger.error(f"Social sentiment analysis failed: {e}")
            return Result.failure(reason=f"Social sentiment analysis failed: {e}")

    def analyze_reputation(self, texts: list[str]) -> Result[SentimentResult]:
        """Performs VADER-batched text analysis and detects sarcasm or crisis indicators."""
        if not texts:
            return Result.failure(reason="Text list cannot be empty")

        try:
            # Clean texts: lowercase and collapse whitespace
            cleaned_texts = [
                _WHITESPACE_REGEX.sub(" ", text.lower().strip())
                for text in texts
                if text is not None
            ]

            if not cleaned_texts:
                return Result.failure(reason="No valid text strings provided in list")

            # VADER Analysis
            scores = [_VADER_ANALYZER.polarity_scores(t)["compound"] for t in cleaned_texts]
            avg_score = float(sum(scores) / len(scores))

            # Reputation checks
            sarcasm_detected = any(_SARCASM_REGEX.search(t) is not None for t in cleaned_texts)
            crisis_detected = any(_CRISIS_KW_REGEX.search(t) is not None for t in cleaned_texts)

            consensus = (
                "BULLISH"
                if avg_score >= 0.05
                else "BEARISH"
                if avg_score <= -0.05
                else "NEUTRAL"
            )

            themes = []
            if crisis_detected:
                themes.append("crisis_keyword")
            if sarcasm_detected:
                themes.append("sarcastic_buzz")

            return Result.success(
                SentimentResult(
                    score=round(avg_score, 4),
                    consensus=consensus,
                    confidence=0.8 if len(texts) > 10 else 0.5,
                    sentiment_score=round((avg_score + 1.0) / 2.0, 4),
                    news_count=len(texts),
                    top_themes=themes,
                )
            )
        except Exception as e:
            logger.error(f"Reputation sentiment analysis failed: {e}")
            return Result.failure(reason=f"Reputation sentiment analysis failed: {e}")
