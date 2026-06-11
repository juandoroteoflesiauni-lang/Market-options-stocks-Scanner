"""
backend/layer_3_specialists/ia_probabilistico/engines/sentiment_engine.py
════════════════════════════════════════════════════════════════════════════════
Sentiment Engine — NLP Pipeline for News, Market & Social Signals.
Refined Aggregation: Keyword-based, VADER-batched, and Reputation metrics.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

import pandas as pd  # type: ignore[import-untyped]
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore[import-untyped]

from ..domain.multimodal_models import SentimentResult

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


class SentimentEngine:
    """Institutional scoring of news summaries and external indicators."""

    BULLISH_KEYWORDS = {
        "upgrade": 0.5,
        "beating": 0.4,
        "outperform": 0.3,
        "growth": 0.2,
        "dovish": 0.6,
        "easing": 0.4,
        "buyback": 0.3,
        "breakout": 0.4,
    }

    BEARISH_KEYWORDS = {
        "downgrade": 0.5,
        "missing": 0.4,
        "underperform": 0.3,
        "decline": 0.2,
        "hawkish": 0.6,
        "tightening": 0.4,
        "recession": 0.5,
        "breakdown": 0.4,
    }

    @staticmethod
    def analyze(
        news_summaries: list[str], external_indicators: dict[str, Any] | None = None
    ) -> SentimentResult:
        """Consolidated sentiment score [-1, 1]."""
        if not news_summaries and not external_indicators:
            return SentimentResult(consensus="NEUTRAL", confidence=0.0)

        raw_score = 0.0
        themes = set()

        # 1. News Analysis
        news_count = len(news_summaries)
        if news_count > 0:
            news_scores = []
            for news in news_summaries:
                s = 0.0
                content = news.lower()
                for word, weight in SentimentEngine.BULLISH_KEYWORDS.items():
                    if word in content:
                        s += weight
                        themes.add(word)
                for word, weight in SentimentEngine.BEARISH_KEYWORDS.items():
                    if word in content:
                        s -= weight
                        themes.add(word)
                news_scores.append(s)

            raw_score += sum(news_scores) / news_count

        # 2. External Indicators (e.g. Fear & Greed [0, 100])
        if external_indicators:
            fear_greed = external_indicators.get("fear_greed", 50)
            raw_score += (fear_greed - 50) / 100.0

        final_score = max(-1.0, min(1.0, raw_score))
        consensus = (
            "BULLISH" if final_score >= 0.25 else "BEARISH" if final_score <= -0.25 else "NEUTRAL"
        )
        sentiment_mic = (final_score + 1.0) / 2.0

        return SentimentResult(
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

    def analyze_social(self, data: list[Any], symbol: str) -> SentimentResult | None:
        """Backward compatible social sentiment wrapper."""
        if not data:
            return None
        try:
            latest = data[0]
            tw_posts = getattr(latest, "twitterPosts", 0) or 0
            st_posts = getattr(latest, "stocktwitsPosts", 0) or 0
            tw_sent = getattr(latest, "twitterSentiment", 0.0) or 0.0
            st_sent = getattr(latest, "stocktwitsSentiment", 0.0) or 0.0

            avg_sent = (tw_sent + st_sent) / 2.0
            total_buzz = tw_posts + st_posts

            # [0, 1] range to [-1, 1]
            score = avg_sent * 2.0 - 1.0
            consensus = "BULLISH" if score >= 0.25 else "BEARISH" if score <= -0.25 else "NEUTRAL"

            return SentimentResult(
                score=round(score, 4),
                consensus=consensus,
                confidence=0.8 if total_buzz > 500 else 0.5,
                sentiment_score=round(avg_sent, 4),
                news_count=int(total_buzz),
                buzz_score=min(10.0, total_buzz / 100.0),
                twitter_impact=float(tw_sent),
                is_hot=total_buzz > 1000,
            )
        except Exception as e:
            logger.error(f"Social sentiment failed for {symbol}: {e}")
            return None


class SentimentReputationEngine:
    """Advanced dataframe-based reputation and sarcasm analysis."""

    @staticmethod
    def analyze_dataframe(df: pd.DataFrame) -> pd.DataFrame | None:
        """Analyzes a batch of messages and returns an enriched dataframe."""
        try:
            res_df = df.copy()
            res_df["_clean"] = (
                res_df["text"].astype(str).str.lower().map(lambda x: _WHITESPACE_REGEX.sub(" ", x))
            )

            # VADER Analysis
            res_df["sentiment_score"] = res_df["_clean"].map(
                lambda x: _VADER_ANALYZER.polarity_scores(str(x))["compound"] if x else 0.0
            )

            # Reputation Metrics
            res_df["sarcasm_detected"] = res_df["_clean"].str.contains(_SARCASM_REGEX, na=False)
            res_df["crisis_detected"] = res_df["_clean"].str.contains(_CRISIS_KW_REGEX, na=False)

            return res_df
        except Exception as e:
            logger.error(f"Reputation analyzer failed: {e}")
            return None

    @staticmethod
    def summarize(df: pd.DataFrame) -> SentimentResult:
        """Aggregates a reputation-enriched dataframe into a SentimentResult."""
        if len(df) == 0:
            return SentimentResult()

        avg_score = float(df["sentiment_score"].mean())
        consensus = (
            "BULLISH" if avg_score >= 0.05 else "BEARISH" if avg_score <= -0.05 else "NEUTRAL"
        )

        themes = []
        if df["crisis_detected"].any():
            themes.append("crisis_keyword")
        if df["sarcasm_detected"].any():
            themes.append("sarcastic_buzz")

        return SentimentResult(
            score=round(avg_score, 4),
            consensus=consensus,
            confidence=0.8 if len(df) > 10 else 0.5,
            sentiment_score=round((avg_score + 1.0) / 2.0, 4),
            news_count=len(df),
            top_themes=themes,
        )


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : sentiment_engine.py
# Sub-capa       : Engine (NLP Sentiment Aggregator)
# Framework ML   : vaderSentiment | numpy | pandas
# Descripcion    : Integración institutional de keywords y VADER.
# Preservado     : analyze_social compatibility.
# ────────────────────────────────────────────────────────────────
