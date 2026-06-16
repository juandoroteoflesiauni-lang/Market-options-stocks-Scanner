from __future__ import annotations
"""Social sentiment scoring from FMP social-sentiment rows (no LLM)."""


from dataclasses import dataclass


@dataclass
class SocialSentimentSignal:
    symbol: str
    composite_score: float  # -1..1 aggregate
    bias: str  # BULLISH / BEARISH / NEUTRAL
    detail: str


class SentimentSpecialist:
    """Aggregates Stocktwits/Twitter sentiment scores from FMP social feed."""

    def analyze(
        self: SentimentSpecialist, data: list[object], symbol: str
    ) -> SocialSentimentSignal | None:
        if not data:
            return None
        scores: list[float] = []
        for row in data:
            for attr in ("stocktwitsSentiment", "twitterSentiment"):
                v = getattr(row, attr, None)
                if v is None and isinstance(row, dict):
                    v = row.get(attr)
                if v is not None:
                    try:
                        scores.append(float(v))
                    except (TypeError, ValueError):
                        continue
        if not scores:
            return SocialSentimentSignal(
                symbol=symbol,
                composite_score=0.0,
                bias="NEUTRAL",
                detail="No numeric sentiment fields in social feed.",
            )
        avg = sum(scores) / len(scores)
        if avg > 0.15:
            bias = "BULLISH"
        elif avg < -0.15:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"
        return SocialSentimentSignal(
            symbol=symbol,
            composite_score=round(avg, 4),
            bias=bias,
            detail=f"Aggregated {len(scores)} sentiment readings.",
        )
