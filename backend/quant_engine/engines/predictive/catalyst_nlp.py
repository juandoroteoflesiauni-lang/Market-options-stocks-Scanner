"""
backend/engine/metrics/catalyst_nlp.py
Sector: Options / Catalyst NLP Engine
[ARCH-1, PD-4]

Theoretical basis:
    Scores event risk using rule-based NLP on earnings transcripts and news.
    Produces a jump_intensity_adj factor that is injected into MJD kernel.
    Purely stateless, synchronous, and offline.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.catalyst_nlp")

# Curated lexicons
_BULLISH_SIGNALS: Final[list[str]] = [
    "record",
    "accelerat",
    "exceed",
    "outperform",
    "strong demand",
    "margin expansion",
    "guidance raised",
    "ahead of expectations",
    "robust growth",
    "significant opportunity",
    "record revenue",
    "record earnings",
    "strong pipeline",
    "beat",
    "upside",
    "confident",
    "momentum",
    "ahead of plan",
    "double-digit growth",
    "new highs",
]

_BEARISH_SIGNALS: Final[list[str]] = [
    "headwind",
    "challeng",
    "uncertain",
    "slowing",
    "below expectations",
    "guidance lowered",
    "miss",
    "disappoint",
    "pressure",
    "margin compression",
    "macro concerns",
    "cautious",
    "difficult environment",
    "softening",
    "decelerat",
    "impairment",
    "write-down",
    "restructuring",
    "layoff",
    "cost cutting",
    "weaker than",
    "softer",
    "reduce guidance",
]

_ALARMING_SIGNALS: Final[list[str]] = [
    "going concern",
    "liquidity crisis",
    "debt covenant",
    "default risk",
    "regulatory investigation",
    "class action",
    "material weakness",
    "restatement",
    "auditor concern",
    "systemic risk",
    "force majeure",
    "insolvency",
    "bankruptcy",
    "fraud",
    "SEC investigation",
]


def _score_text(text: str) -> tuple[int, int, int]:
    """Return (bullish_hits, bearish_hits, alarming_hits) for a block of text."""
    text_lower = text.lower()
    bull = sum(1 for kw in _BULLISH_SIGNALS if kw in text_lower)
    bear = sum(1 for kw in _BEARISH_SIGNALS if kw in text_lower)
    alarm = sum(1 for kw in _ALARMING_SIGNALS if kw in text_lower)
    return bull, bear, alarm


# ── Input Contracts (Pydantic) ──────────────────────────────────────────────────


class TranscriptInput(BaseModel):
    """Earnings call transcript content."""

    model_config = ConfigDict(frozen=True)

    content: str


class NewsInput(BaseModel):
    """News headline and summary body text."""

    model_config = ConfigDict(frozen=True)

    title: str
    text: str


class CalendarInput(BaseModel):
    """Scheduled earnings event date."""

    model_config = ConfigDict(frozen=True)

    date: str


class SurpriseInput(BaseModel):
    """Historical EPS result and estimates."""

    model_config = ConfigDict(frozen=True)

    actual: float
    estimated: float


# ── Output Contracts (Pydantic) ─────────────────────────────────────────────────


class UpcomingCatalyst(BaseModel):
    """A scheduled event that may cause a price jump."""

    model_config = ConfigDict(frozen=True)

    event_type: str
    date: str | None
    days_until: int | None
    label: str


class EventRiskProfile(BaseModel):
    """Full NLP analysis result for a ticker."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    event_risk_score: float
    tone: str
    tone_confidence: float
    jump_intensity_adj: float
    transcript_summary: str | None = None
    bullish_hits: int = 0
    bearish_hits: int = 0
    alarming_hits: int = 0
    news_count: int = 0
    news_sentiment: float = 0.5
    upcoming_catalysts: list[UpcomingCatalyst] = Field(default_factory=list)
    last_eps_surprise: float | None = None
    avg_eps_surprise: float | None = None


# ── Engine ───────────────────────────────────────────────────────────────────────


class CatalystNLPEngine:
    """Scores event risk using rule-based NLP on earnings transcripts and news.

    Produces a jump_intensity_adj factor that is injected into MJD kernel.
    Purely stateless.
    """

    PROXIMITY_THRESHOLD_DAYS: Final[int] = 14
    NEWS_WEIGHT: Final[float] = 0.3
    TRANSCRIPT_WEIGHT: Final[float] = 0.7

    def __init__(self) -> None:
        pass

    def analyze(
        self,
        symbol: str,
        transcript: TranscriptInput | None,
        news: list[NewsInput],
        calendar: list[CalendarInput],
        surprises: list[SurpriseInput],
    ) -> Result[EventRiskProfile]:
        """Runs the offline, stateless analysis pipeline on transcripts, news and surprises."""
        if not symbol:
            return Result.failure(reason="symbol cannot be empty")

        try:
            today_date = datetime.now().date()

            # 1. Transcript NLP
            bull_t = bear_t = alarm_t = 0
            transcript_summary: str | None = None

            if transcript is not None and transcript.content:
                bull_t, bear_t, alarm_t = _score_text(transcript.content)
                sentences = re.split(r"(?<=[.!?]) +", transcript.content)
                highlights = []
                for s in sentences:
                    s_l = s.lower()
                    if any(
                        kw in s_l for kw in _BULLISH_SIGNALS + _BEARISH_SIGNALS + _ALARMING_SIGNALS
                    ):
                        highlights.append(s.strip())
                    if len(highlights) >= 3:
                        break
                transcript_summary = " … ".join(highlights) or None

            # 2. News NLP
            news_count = len(news)
            news_sentiment = 0.5
            bull_n = bear_n = alarm_n = 0

            if news:
                for item in news:
                    headline = (item.title or "") + " " + (item.text or "")
                    b, br, al = _score_text(headline)
                    bull_n += b
                    bear_n += br
                    alarm_n += al

                total_n = bull_n + bear_n + alarm_n
                if total_n > 0:
                    news_sentiment = bull_n / total_n

            # 3. Upcoming Catalyst Detection
            catalysts: list[UpcomingCatalyst] = []
            if calendar:
                for e in calendar:
                    if e.date:
                        try:
                            e_date = datetime.strptime(e.date, "%Y-%m-%d").date()
                            days_until = (e_date - today_date).days
                            if 0 <= days_until <= 45:
                                catalysts.append(
                                    UpcomingCatalyst(
                                        event_type="EARNINGS",
                                        date=e.date,
                                        days_until=days_until,
                                        label=f"Q Earnings in {days_until}d",
                                    )
                                )
                        except ValueError:
                            pass

            # 4. Historical EPS Surprise calibration
            last_eps_surprise: float | None = None
            avg_eps_surprise: float | None = None

            if surprises:
                valid = [s for s in surprises if s.estimated != 0.0]
                if valid:
                    pcts = [(s.actual - s.estimated) / abs(s.estimated) for s in valid]
                    last_eps_surprise = round(pcts[0] * 100.0, 2)
                    avg_eps_surprise = round(sum(pcts) / len(pcts) * 100.0, 2)

            # 5. Aggregate scores
            total_t = bull_t + bear_t + alarm_t
            total_n_signals = bull_n + bear_n + alarm_n

            bear_ratio_t = (bear_t + 2.0 * alarm_t) / (total_t + 1.0)
            bear_ratio_n = (bear_n + 2.0 * alarm_n) / (total_n_signals + 1.0)

            composite_bear = self.TRANSCRIPT_WEIGHT * bear_ratio_t + self.NEWS_WEIGHT * bear_ratio_n

            # Proximity uplift: risk ↑ as earnings date approaches
            proximity_multiplier = 1.0
            if catalysts:
                valid_days = [c.days_until for c in catalysts if c.days_until is not None]
                if valid_days:
                    min_days = min(valid_days)
                    if min_days <= self.PROXIMITY_THRESHOLD_DAYS:
                        # Scale from 1.0 (14 days away) to 1.5 (0 days away)
                        proximity_multiplier = 1.0 + 0.5 * (
                            1.0 - min_days / self.PROXIMITY_THRESHOLD_DAYS
                        )

            raw_score = min(1.0, composite_bear * proximity_multiplier)

            # Alarming signals dominate → force high event risk
            if alarm_t >= 2 or alarm_n >= 1:
                raw_score = max(raw_score, 0.75)

            # 6. Tone determination
            total_all = bull_t + bull_n + bear_t + bear_n + alarm_t + alarm_n
            if alarm_t + alarm_n >= 2:
                tone = "ALARMING"
                tone_conf = min(1.0, (alarm_t + alarm_n) / (total_all + 1.0))
            elif bear_t + bear_n > bull_t + bull_n:
                tone = "BEARISH"
                tone_conf = (bear_t + bear_n - bull_t - bull_n) / (total_all + 1.0)
            elif bull_t + bull_n > bear_t + bear_n:
                tone = "BULLISH"
                tone_conf = (bull_t + bull_n - bear_t - bear_n) / (total_all + 1.0)
            else:
                tone = "NEUTRAL"
                tone_conf = 0.0

            # 7. jump_intensity adjustment
            if tone == "ALARMING":
                jump_adj = 1.6
            elif tone == "BEARISH" and raw_score > 0.5:
                jump_adj = 1.0 + raw_score * 0.6
            elif catalysts:
                jump_adj = 1.15
            else:
                jump_adj = 1.0

            return Result.success(
                EventRiskProfile(
                    symbol=symbol,
                    event_risk_score=round(raw_score, 4),
                    tone=tone,
                    tone_confidence=round(min(1.0, tone_conf), 4),
                    jump_intensity_adj=round(jump_adj, 4),
                    transcript_summary=transcript_summary,
                    bullish_hits=bull_t + bull_n,
                    bearish_hits=bear_t + bear_n,
                    alarming_hits=alarm_t + alarm_n,
                    news_count=news_count,
                    news_sentiment=round(news_sentiment, 4),
                    upcoming_catalysts=catalysts,
                    last_eps_surprise=last_eps_surprise,
                    avg_eps_surprise=avg_eps_surprise,
                )
            )
        except Exception as e:
            logger.error(f"Catalyst NLP analysis failed: {e}")
            return Result.failure(reason=f"Catalyst NLP analysis failed: {e}")
