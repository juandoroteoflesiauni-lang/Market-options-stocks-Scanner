"""
backend/layer_3_specialists/ia_probabilistico/engines/catalyst_nlp_engine.py
════════════════════════════════════════════════════════════════════════════════
Catalyst NLP Engine — Event Impact Scoring from earnings transcripts & news.

Strategy (no external LLM required):
  1. Fetch latest earnings call transcript (FMP get_transcript).
  2. Scan the text with a curated lexicon of power-words that indicate
     management tone: aggressive/bullish vs cautious/defensive vs alarming.
  3. Produce a composite EventRiskScore [0..1] that modulates jump_intensity
     in the probabilistic engine.
  4. Detect upcoming catalysts (earnings date, ex-dividend, macro events).
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Global semaphore to throttle concurrent FMP requests across the system
_CATALYST_SEMAPHORE = None


def _get_semaphore() -> Any:
    global _CATALYST_SEMAPHORE
    if _CATALYST_SEMAPHORE is None:
        import asyncio

        _CATALYST_SEMAPHORE = asyncio.Semaphore(3)
    return _CATALYST_SEMAPHORE


# ── Lexicon ───────────────────────────────────────────────────────────────────

# Words that signal management confidence / accelerating fundamentals
_BULLISH_SIGNALS: list[str] = [
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

# Words that signal caution, uncertainty, or deteriorating conditions
_BEARISH_SIGNALS: list[str] = [
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

# Words that signal elevated tail risk or systemic stress
_ALARMING_SIGNALS: list[str] = [
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


# ── Domain objects ─────────────────────────────────────────────────────────────


@dataclass
class UpcomingCatalyst:
    """A scheduled event that may cause a price jump."""

    event_type: str  # "EARNINGS" | "DIVIDEND" | "MACRO" | "NEWS_SPIKE"
    date: str | None  # ISO date or None if unknown
    days_until: int | None
    label: str


@dataclass
class EventRiskProfile:
    """Full NLP analysis result for a ticker."""

    symbol: str
    event_risk_score: float  # [0..1], 0 = no event risk, 1 = extreme
    tone: str  # "BULLISH" | "BEARISH" | "NEUTRAL" | "ALARMING"
    tone_confidence: float  # |bull - bear| / total signals
    jump_intensity_adj: float  # Multiplicative factor for jump kernel (1.0 = no change)
    transcript_summary: str | None = None
    bullish_hits: int = 0
    bearish_hits: int = 0
    alarming_hits: int = 0
    news_count: int = 0
    news_sentiment: float = 0.5  # 0=negative, 1=positive
    upcoming_catalysts: list[UpcomingCatalyst] = field(default_factory=list)
    last_eps_surprise: float | None = None  # % surprise (positive = beat)
    avg_eps_surprise: float | None = None


# ── Engine ────────────────────────────────────────────────────────────────────


class CatalystNLPEngine:
    """
    Scores event risk using rule-based NLP on earnings transcripts and news.
    Produces a jump_intensity_adj factor that is injected into MJD kernel.
    """

    # If an earnings date is within this many days, elevate risk
    PROXIMITY_THRESHOLD_DAYS = 14

    # Weight of news sentiment relative to transcript
    NEWS_WEIGHT = 0.3
    TRANSCRIPT_WEIGHT = 0.7

    async def analyze(self, symbol: str, fmp_client: Any) -> EventRiskProfile:
        """
        Full pipeline:
          1. Fetch latest transcript and score tone.
          2. Fetch recent news headlines and score.
          3. Detect upcoming earnings date and time-decay risk.
          4. Fetch historical EPS surprises to calibrate baseline jump expectation.
        """
        today = datetime.now()

        import asyncio

        # Define tasks for concurrent execution
        today_str = today.strftime("%Y-%m-%d")
        ahead_str = (today + timedelta(days=45)).strftime("%Y-%m-%d")
        yesterday_str = (today - timedelta(days=5)).strftime(
            "%Y-%m-%d"
        )  # for rates if needed elsewhere

        tasks = {
            "transcript_list": fmp_client.get_transcript_list(symbol),
            "news": fmp_client.get_stock_news(symbol, limit=15),
            "calendar": fmp_client.get_earnings_calendar(today_str, ahead_str),
            "surprises": fmp_client.get_earnings_surprises(symbol),
        }

        # Execute concurrently
        task_names = list(tasks.keys())
        task_coros = list(tasks.values())
        # Execute concurrently with safety timeout and throttling
        try:
            sem = _get_semaphore()

            async def _run_with_timeout() -> Any:
                async with sem:
                    return await asyncio.wait_for(
                        asyncio.gather(*task_coros, return_exceptions=True), timeout=30.0
                    )

            task_results = await _run_with_timeout()
        except TimeoutError:
            logger.warning(f"Catalyst NLP ingestion for {symbol} timed out after 30s.")
            task_results = [Exception("Timeout") for _ in range(len(task_coros))]

        results = {
            name: res
            for name, res in zip(task_names, task_results, strict=False)
            if not isinstance(res, Exception)
        }

        # ── 1. Transcript NLP ─────────────────────────────────────────────
        bull_t = bear_t = alarm_t = 0
        transcript_summary: str | None = None
        transcript_list = results.get("transcript_list")

        if transcript_list:
            try:
                latest = transcript_list[0]
                transcript = await fmp_client.get_transcript(
                    symbol, year=latest.year, quarter=latest.quarter
                )
                if transcript and transcript.content:
                    bull_t, bear_t, alarm_t = _score_text(transcript.content)
                    sentences = re.split(r"(?<=[.!?]) +", transcript.content)
                    highlights = []
                    for s in sentences:
                        s_l = s.lower()
                        if any(
                            kw in s_l
                            for kw in _BULLISH_SIGNALS + _BEARISH_SIGNALS + _ALARMING_SIGNALS
                        ):
                            highlights.append(s.strip())
                        if len(highlights) >= 3:
                            break
                    transcript_summary = " … ".join(highlights) or None
            except Exception as ex:
                logger.debug(f"Transcript deep fetch failed for {symbol}: {ex}")

        # ── 2. News NLP ───────────────────────────────────────────────────
        news_count = 0
        news_sentiment = 0.5
        bull_n = bear_n = alarm_n = 0
        news_items = results.get("news")

        if news_items:
            news_count = len(news_items)
            for item in news_items:
                headline = (item.title or "") + " " + (item.text or "")
                b, br, al = _score_text(headline)
                bull_n += b
                bear_n += br
                alarm_n += al

            total_n = bull_n + bear_n + alarm_n
            if total_n > 0:
                news_sentiment = bull_n / total_n

        # ── 3. Upcoming Catalyst Detection ────────────────────────────────
        catalysts: list[UpcomingCatalyst] = []
        earnings_cal = results.get("calendar")

        if earnings_cal:
            for e in earnings_cal:
                if e.symbol and e.symbol.upper() == symbol.upper() and e.date:
                    try:
                        e_date = datetime.strptime(e.date, "%Y-%m-%d")
                        days_until = (e_date - today).days
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

        # ── 4. Historical EPS Surprise calibration ────────────────────────
        last_eps_surprise: float | None = None
        avg_eps_surprise: float | None = None
        surprises = results.get("surprises")

        if surprises:
            valid = [
                s
                for s in surprises
                if s.actualEarningResult is not None and s.estimatedEarning not in (None, 0)
            ]
            if valid:
                pcts = [
                    (s.actualEarningResult - s.estimatedEarning) / abs(s.estimatedEarning)
                    for s in valid
                ]
                last_eps_surprise = round(pcts[0] * 100, 2)
                avg_eps_surprise = round(sum(pcts) / len(pcts) * 100, 2)

        # ── 5. Aggregate scores ───────────────────────────────────────────
        total_t = bull_t + bear_t + alarm_t
        total_n_signals = bull_n + bear_n + alarm_n

        # Weighted composite bearishness
        bear_ratio_t = (bear_t + 2 * alarm_t) / (total_t + 1)  # alarm counts double
        bear_ratio_n = (bear_n + 2 * alarm_n) / (total_n_signals + 1)

        composite_bear = self.TRANSCRIPT_WEIGHT * bear_ratio_t + self.NEWS_WEIGHT * bear_ratio_n

        # Proximity uplift: risk ↑ as earnings date approaches
        proximity_multiplier = 1.0
        if catalysts:
            min_days = min(c.days_until for c in catalysts if c.days_until is not None)
            if min_days <= self.PROXIMITY_THRESHOLD_DAYS:
                # Scale from 1.0 (14 days away) to 1.5 (0 days away)
                proximity_multiplier = 1.0 + 0.5 * (1.0 - min_days / self.PROXIMITY_THRESHOLD_DAYS)

        raw_score = min(1.0, composite_bear * proximity_multiplier)

        # Alarming signals dominate → force high event risk
        if alarm_t >= 2 or alarm_n >= 1:
            raw_score = max(raw_score, 0.75)

        # ── 6. Tone determination ─────────────────────────────────────────
        total_all = bull_t + bull_n + bear_t + bear_n + alarm_t + alarm_n
        if alarm_t + alarm_n >= 2:
            tone = "ALARMING"
            tone_conf = min(1.0, (alarm_t + alarm_n) / (total_all + 1))
        elif bear_t + bear_n > bull_t + bull_n:
            tone = "BEARISH"
            tone_conf = (bear_t + bear_n - bull_t - bull_n) / (total_all + 1)
        elif bull_t + bull_n > bear_t + bear_n:
            tone = "BULLISH"
            tone_conf = (bull_t + bull_n - bear_t - bear_n) / (total_all + 1)
        else:
            tone = "NEUTRAL"
            tone_conf = 0.0

        # ── 7. jump_intensity adjustment ─────────────────────────────────
        # - ALARMING: +60% jump intensity
        # - BEARISH + high risk: +20–40%
        # - BULLISH: no increase (momentum, not risk)
        # - Proximity multiplier already baked into raw_score
        if tone == "ALARMING":
            jump_adj = 1.6
        elif tone == "BEARISH" and raw_score > 0.5:
            jump_adj = 1.0 + raw_score * 0.6
        elif catalysts:  # upcoming event even if neutral → widen distribution
            jump_adj = 1.15
        else:
            jump_adj = 1.0

        return EventRiskProfile(
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
