from __future__ import annotations
"""Specialist for Deep Transcript analysis using LLM agents."""


import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

TranscriptRunner = Callable[[str, str], Awaitable[str]]


@dataclass
class TranscriptAnalysis:
    symbol: str
    tone_score: float  # 0 to 10 (Bullishness)
    evasiveness_score: float  # 0 to 10 (High = more evasive)
    themes: list[str]
    summary: str
    conviction_bonus: float  # 0.0 to 1.0


class TranscriptSpecialist:
    """Uses LLM to analyze the emotional and narrative layer of earnings calls."""

    def __init__(
        self: TranscriptSpecialist, transcript_runner: TranscriptRunner | None = None
    ) -> None:
        """``transcript_runner`` must call Layer 4 (e.g. ``invoke_agent``); Layer 3 stays free of L4 imports."""
        self._transcript_runner = transcript_runner

    async def analyze(
        self: TranscriptSpecialist, transcript_text: str, symbol: str
    ) -> TranscriptAnalysis | None:
        if not transcript_text or len(transcript_text) < 100:
            return None

        if self._transcript_runner is None:
            logger.warning(
                "TranscriptSpecialist: no transcript_runner configured; skipping LLM transcript analysis",
            )
            return None

        prompt = (
            f"Analiza el siguiente transcript de la llamada de ganancias de {symbol}.\n\n"
            f"TRANSCRIPT:\n{transcript_text[:15000]}\n\n"
            "Devuelve un análisis en formato JSON estricto con las siguientes llaves:\n"
            "tone_score (numero 0-10),\n"
            "evasiveness_score (numero 0-10),\n"
            "themes (lista de strings),\n"
            "summary (string breve),\n"
            "conviction_bonus (fijo entre -1.0 y 1.0 según la calidad de las respuestas)."
        )

        try:
            raw_out = await self._transcript_runner("transcript_analyst", prompt)
            match = re.search(r"(\{.*\})", raw_out, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                return TranscriptAnalysis(
                    symbol=symbol,
                    tone_score=float(data.get("tone_score", 5.0)),
                    evasiveness_score=float(data.get("evasiveness_score", 5.0)),
                    themes=list(data.get("themes", [])),
                    summary=str(data.get("summary", "No summary provided.")),
                    conviction_bonus=float(data.get("conviction_bonus", 0.0)),
                )
        except Exception as e:
            logger.error("Error analyzing transcript with LLM: %s", e)

        return None
