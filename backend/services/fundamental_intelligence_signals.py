from __future__ import annotations
"""Assemble fundamentals intelligence block (Layer 3 specialists) for FMP mega-fetch."""


from backend.config.logger_setup import get_logger
from backend.quant_engine.engines.fundamental.insider_specialist import InsiderSpecialist
from backend.quant_engine.engines.fundamental.sentiment_specialist import SentimentSpecialist
from backend.quant_engine.engines.fundamental.transcript_specialist import TranscriptSpecialist
from backend.services.transcript_llm_bridge import default_transcript_agent_invoke

logger = get_logger(__name__)


async def build_social_insider_transcript_signals(
    fmp_client: object,
    sym: str,
    social_data: list[object],
    insider_data: list[object],
    transcript_list: list[object],
) -> tuple[object | None, object | None, object | None]:
    """Return (social_signal, insider_signal, transcript_analysis) for intelligence_v4."""
    social_signal = SentimentSpecialist().analyze(social_data, sym)
    insider_signal = InsiderSpecialist().analyze(insider_data, sym)

    transcript_data: object | None = None
    if transcript_list:
        try:
            latest = transcript_list[0]
            get_tr = getattr(fmp_client, "get_transcript", None)
            if not callable(get_tr):
                return social_signal, insider_signal, None
            year = int(getattr(latest, "year", 0) or 0)
            quarter = int(getattr(latest, "quarter", 0) or 0)
            full_transcript = await get_tr(sym, year, quarter)
            if full_transcript:
                specialist = TranscriptSpecialist(
                    transcript_runner=default_transcript_agent_invoke,
                )
                content = getattr(full_transcript, "content", "") or ""
                transcript_data = await specialist.analyze(str(content), sym)
        except Exception as exc:
            logger.warning("fundamental_intelligence.transcript_failed sym=%s err=%s", sym, exc)

    return social_signal, insider_signal, transcript_data
