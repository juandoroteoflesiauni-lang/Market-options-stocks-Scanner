"""Market Scanner dashboard context assembly."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerContextRequest,
    MarketScannerContextResponse,
    ScannerBriefBlock,
    ScannerBriefTone,
    ScannerNewsImpact,
    ScannerNewsItem,
    ScannerNewsSentiment,
)
from backend.layer_1_data.engines.regulatory_scanner import evaluate_regulatory_document
from backend.services.market_scanner_service import list_market_scanner_universes

logger = get_logger(__name__)

NewsProvider = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
SentimentProvider = Callable[[str, int], Awaitable[dict[str, Any]]]
CatalystProvider = Callable[[str], Awaitable[dict[str, Any]]]
SnapshotProvider = Callable[[], Awaitable[dict[str, Any] | None]]

_ARGENTINA_SYMBOLS = {
    "GGAL",
    "YPF",
    "PAM",
    "BMA",
    "SUPV",
    "TEO",
    "CEPU",
    "LOMA",
    "TGS",
    "MELI",
    "VIST",
    "IRS",
}
_GLOBAL_MARKET_NEWS_SYMBOL = "MARKET"
_SCANNER_CONTEXT_CATALYST_TIMEOUT_SECONDS = 8.0

_BULLISH_WORDS = (
    "beat",
    "accelerat",
    "record",
    "upgrade",
    "strong",
    "demand",
    "expansion",
    "buyback",
    "raises",
    "rally",
    "surge",
)
_BEARISH_WORDS = (
    "miss",
    "cut",
    "probe",
    "lawsuit",
    "default",
    "weak",
    "slow",
    "downgrade",
    "warning",
    "warn",
    "inflation",
    "delay",
    "slip",
    "selloff",
)


async def fetch_symbol_news(symbol: str, limit: int) -> list[dict[str, Any]]:
    """Fetch symbol news through the configured FMP client."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    fmp = FMPClient(timeout=8.0)
    items = (
        await fmp.get_latest_stock_news(limit=limit)
        if symbol.upper() == _GLOBAL_MARKET_NEWS_SYMBOL
        else await fmp.get_stock_news(symbol, limit=limit)
    )
    return [
        {
            "symbol": item.symbol or symbol.upper(),
            "published_date": item.publishedDate,
            "title": item.title,
            "source": item.site,
            "url": item.url,
            "summary": item.text,
        }
        for item in items
        if item.title
    ]


async def fetch_symbol_sentiment(symbol: str, limit: int) -> dict[str, Any]:
    """Fetch and compact social sentiment for one symbol."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    items = await FMPClient(timeout=8.0).get_social_sentiment(symbol, limit=limit)
    scores: list[float] = []
    impressions = 0
    for item in items:
        for score in (item.stocktwitsSentiment, item.twitterSentiment):
            if isinstance(score, int | float):
                scores.append(float(score))
        impressions += int(item.stocktwitsImpressions or 0)
        impressions += int(item.twitterImpressions or 0)
    if not scores:
        news_items = await fetch_symbol_news(symbol, limit=limit)
        if news_items:
            inferred = [
                _sentiment_score_from_label(
                    _infer_news_sentiment(f"{item.get('title', '')} {item.get('summary', '')}")
                )
                for item in news_items
            ]
            score = sum(inferred) / len(inferred)
            label = _sentiment_label_from_score(score)
            return {
                "status": "available",
                "score": round(score, 4),
                "label": label,
                "impressions": 0,
                "sample_size": len(inferred),
                "source": "FMP news inference",
            }
        return {
            "status": "source unavailable",
            "score": None,
            "label": "source unavailable",
            "source": "FMP social sentiment",
        }
    score = sum(scores) / len(scores)
    label = _sentiment_label_from_score(score)
    return {
        "status": "available",
        "score": round(score, 4),
        "label": label,
        "impressions": impressions,
        "sample_size": len(scores),
        "source": "FMP social sentiment",
    }


async def fetch_symbol_catalyst(symbol: str) -> dict[str, Any]:
    """Run the existing catalyst NLP engine for one symbol."""
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_3_specialists.ia_probabilistico.engines.catalyst_nlp_engine import (
        CatalystNLPEngine,
    )

    profile = await CatalystNLPEngine().analyze(symbol, FMPClient(timeout=8.0))
    return {
        "status": "available",
        "event_risk_score": profile.event_risk_score,
        "tone": profile.tone,
        "tone_confidence": profile.tone_confidence,
        "news_count": profile.news_count,
        "news_sentiment": profile.news_sentiment,
        "upcoming_catalysts": [
            {
                "event_type": item.event_type,
                "date": item.date,
                "days_until": item.days_until,
                "label": item.label,
            }
            for item in profile.upcoming_catalysts
        ],
    }


async def fetch_fear_greed_dashboard() -> dict[str, Any] | None:
    """Return the latest stored Fear & Greed dashboard snapshot."""
    from backend.layer_3_specialists.ia_probabilistico.engines.fear_greed_storage import (
        get_fg_storage,
    )

    storage = get_fg_storage()
    history = storage.get_history("SPY", days=1)
    latest = history[0] if history else None
    if latest is None:
        return await _compute_live_fear_greed_dashboard()
    statistics = storage.get_statistics("SPY", days=30)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "current": {
            "score": latest.score,
            "label": latest.label,
            "data_quality": latest.data_quality,
        },
        "statistics": _fear_greed_statistics_with_current_fallback(
            statistics,
            score=latest.score,
            factors=latest.factors,
        ),
        "factors": latest.factors,
    }


async def _compute_live_fear_greed_dashboard() -> dict[str, Any] | None:
    """Compute a live Fear & Greed snapshot when no persisted scanner snapshot exists."""
    try:
        from backend.layer_1_data.fetchers.fmp_client import FMPClient
        from backend.layer_3_specialists.ia_probabilistico.engines.fear_greed_engine import (
            FearGreedEngine,
        )
        from backend.layer_3_specialists.ia_probabilistico.engines.market_data_fetcher import (
            MarketDataFetcher,
        )

        fmp = FMPClient(timeout=8.0)
        market_data = await MarketDataFetcher(fmp).fetch_fear_greed_data()
        result = await FearGreedEngine().compute(symbol="SPY", market_data=market_data)
        factors = getattr(result, "factors", {}) or {}
        score = float(getattr(result, "score", 50.0))
        return {
            "timestamp": str(getattr(result, "timestamp", datetime.now(UTC).isoformat())),
            "current": {
                "score": score,
                "label": str(getattr(result, "label", "Neutral")),
                "data_quality": str(getattr(result, "data_quality", "poor")),
                "source": "live_fmp",
            },
            "statistics": _fear_greed_statistics_with_current_fallback(
                {},
                score=score,
                factors=factors,
            ),
            "factors": factors,
        }
    except Exception as exc:
        logger.warning("market_scanner.context.fear_greed_live_failed error=%s", exc)
        return None


def _fear_greed_statistics_with_current_fallback(
    statistics: dict[str, Any] | None,
    *,
    score: float | int | None,
    factors: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ensure the scanner desk has meaningful 30d cells even before history fills."""
    out = dict(statistics or {})
    numeric_score = float(score) if isinstance(score, int | float) else None
    factor_values = factors or {}
    volatility = factor_values.get("volatility")
    momentum = factor_values.get("momentum")
    event_risk = factor_values.get("event_risk")

    if numeric_score is not None:
        out["mean_score"] = _non_zero_number(out.get("mean_score"), numeric_score)
        out["min_score"] = _non_zero_number(out.get("min_score"), numeric_score)
        out["max_score"] = _non_zero_number(out.get("max_score"), numeric_score)
        out["reading_count"] = _non_zero_number(out.get("reading_count"), 1)
    if isinstance(momentum, int | float):
        out["avg_momentum"] = _non_zero_number(out.get("avg_momentum"), float(momentum))
    if isinstance(volatility, int | float):
        out["avg_volatility"] = _non_zero_number(out.get("avg_volatility"), float(volatility))
    if isinstance(event_risk, int | float):
        out["avg_event_risk"] = _non_zero_number(out.get("avg_event_risk"), float(event_risk))
    return out


def _non_zero_number(value: Any, fallback: float | int) -> float | int:
    if isinstance(value, int | float) and value != 0:
        return value
    return fallback


async def _enrich_fear_greed_snapshot(fear_greed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach live ^VIX quote and 50d MA into fear_greed.current for scanner desk UI."""
    if not isinstance(fear_greed, dict):
        return fear_greed
    out = dict(fear_greed)
    inner = out.get("current")
    inner = {} if not isinstance(inner, dict) else dict(inner)
    try:
        from backend.layer_1_data.fetchers.fmp_client import FMPClient

        fmp = FMPClient(timeout=8.0)
        quote = await fmp.get_quote("^VIX")
        px = getattr(quote, "price", None) if quote is not None else None
        if isinstance(px, int | float) and float(px) > 0:
            inner["vix"] = round(float(px), 4)
            chg = getattr(quote, "changesPercentage", None)
            if isinstance(chg, int | float):
                inner["vix_change_pct"] = round(float(chg), 4)
            ts = getattr(quote, "timestamp", None)
            if ts is not None:
                inner["vix_quote_ts"] = ts
            hist = await fmp.get_historical_prices(
                "^VIX",
                date_from=(datetime.now(UTC) - timedelta(days=130)).strftime("%Y-%m-%d"),
                date_to=datetime.now(UTC).strftime("%Y-%m-%d"),
            )
            if hist:
                closes = [p.close for p in hist if p.close is not None]
                if len(closes) >= 50:
                    inner["vix_ma50"] = round(
                        float(sum(closes[:50]) / 50.0),
                        4,
                    )
            out["current"] = inner
    except Exception as exc:
        logger.debug("scanner context: VIX enrichment skipped: %s", str(exc)[:120])
    return out


def _aggregate_regulatory_scan(news: list[ScannerNewsItem]) -> dict[str, Any]:
    """Scan recent headlines for regulatory kill-switch language (advisory)."""
    if not news:
        return {"status": "no_headlines"}
    parts: list[str] = []
    for item in news[:24]:
        parts.append(f"{item.title} {item.summary or ''}")
    blob = " ".join(parts)[:12_000]
    result = evaluate_regulatory_document(blob, source="scanner_news_aggregate")
    return {
        "status": "ok",
        "absolute_veto": bool(result.absolute_veto),
        "severity": result.severity_level.value,
        "action_directive": result.action_directive.value,
        "matched_keywords": result.matched_keywords[:8],
        "parse_error": bool(result.parse_error),
    }


async def fetch_argentina_summary() -> dict[str, Any] | None:
    """Reuse the Argentina market-summary endpoint implementation."""
    from backend.routers.argentina_router import get_argentina_summary

    result = await get_argentina_summary()
    return result if isinstance(result, dict) else None


async def build_market_scanner_context(
    request: MarketScannerContextRequest,
    *,
    news_provider: NewsProvider = fetch_symbol_news,
    sentiment_provider: SentimentProvider = fetch_symbol_sentiment,
    catalyst_provider: CatalystProvider = fetch_symbol_catalyst,
    fear_greed_provider: SnapshotProvider = fetch_fear_greed_dashboard,
    argentina_provider: SnapshotProvider = fetch_argentina_summary,
) -> MarketScannerContextResponse:
    """Assemble an institutional scanner briefing with per-source degradation."""
    symbols = _symbols_for_context(request)
    nlp_engine = _scanner_nlp_engine()
    fear_greed_task = asyncio.create_task(_safe_snapshot("fear_greed", fear_greed_provider))
    argentina_task = (
        asyncio.create_task(_safe_snapshot("argentina", argentina_provider))
        if _needs_argentina_context(request.universe, symbols)
        else None
    )

    per_symbol = await asyncio.gather(
        *(
            _build_symbol_context(
                symbol,
                request.limit_per_symbol,
                news_provider,
                sentiment_provider,
                catalyst_provider,
                nlp_engine,
            )
            for symbol in symbols
        )
    )
    global_news = await _safe_news(
        _GLOBAL_MARKET_NEWS_SYMBOL,
        max(6, request.limit_per_symbol * 2),
        news_provider,
    )
    fear_greed, fear_status = await fear_greed_task
    fear_greed = await _enrich_fear_greed_snapshot(fear_greed)
    argentina_summary: dict[str, Any] | None = None
    argentina_status = "not requested"
    if argentina_task is not None:
        argentina_summary, argentina_status = await argentina_task

    news: list[ScannerNewsItem] = []
    sentiment_by_symbol: dict[str, dict[str, Any]] = {}
    catalysts_by_symbol: dict[str, dict[str, Any]] = {}
    source_counts = {"news": 0, "sentiment": 0, "catalysts": 0}

    news.extend(global_news)
    for item in per_symbol:
        news.extend(item["news"])
        sentiment = item["sentiment"]
        catalyst = item["catalyst"]
        symbol = str(item["symbol"])
        sentiment_by_symbol[symbol] = sentiment
        catalysts_by_symbol[symbol] = catalyst
        if item["news"]:
            source_counts["news"] += 1
        if sentiment.get("status") != "source unavailable":
            source_counts["sentiment"] += 1
        if catalyst.get("status") != "source unavailable":
            source_counts["catalysts"] += 1

    source_counts["news"] += 1 if global_news else 0
    news = sorted(
        news,
        key=lambda item: (_impact_rank(item.impact), item.published_date or ""),
        reverse=True,
    )[: max(8, request.limit_per_symbol * max(1, len(symbols)))]

    sources = {
        "fear_greed": fear_status,
        "news": "available" if source_counts["news"] else "source unavailable",
        "sentiment": "available" if source_counts["sentiment"] else "source unavailable",
        "catalysts": "available" if source_counts["catalysts"] else "source unavailable",
        "argentina": argentina_status,
    }

    return MarketScannerContextResponse(
        market_brief=_build_market_brief(
            symbols=symbols,
            fear_greed=fear_greed,
            news=news,
            catalysts_by_symbol=catalysts_by_symbol,
            argentina_summary=argentina_summary,
            sources=sources,
        ),
        fear_greed=fear_greed,
        news=news,
        sentiment_by_symbol=sentiment_by_symbol,
        catalysts_by_symbol=catalysts_by_symbol,
        argentina_summary=argentina_summary,
        sources=sources,
        regulatory_scan_summary=_aggregate_regulatory_scan(news),
    )


async def _build_symbol_context(
    symbol: str,
    limit: int,
    news_provider: NewsProvider,
    sentiment_provider: SentimentProvider,
    catalyst_provider: CatalystProvider,
    nlp_engine: str,
) -> dict[str, Any]:
    if nlp_engine == "fingpt":
        news_result = await _safe_news(symbol, limit, news_provider)
        sentiment_result, catalyst_result = await asyncio.gather(
            _safe_fingpt_sentiment(symbol, news_result),
            _safe_fingpt_catalyst(symbol, news_result),
        )
        return {
            "symbol": symbol,
            "news": news_result,
            "sentiment": sentiment_result,
            "catalyst": catalyst_result,
        }
    if nlp_engine == "hybrid":
        news_task = asyncio.create_task(_safe_news(symbol, limit, news_provider))
        fmp_sentiment_task = asyncio.create_task(_safe_sentiment(symbol, sentiment_provider))
        fmp_catalyst_task = asyncio.create_task(_safe_catalyst(symbol, catalyst_provider))
        news_result, fmp_sentiment, fmp_catalyst = await asyncio.gather(
            news_task,
            fmp_sentiment_task,
            fmp_catalyst_task,
        )
        fingpt_sentiment, fingpt_catalyst = await asyncio.gather(
            _safe_fingpt_sentiment(symbol, news_result),
            _safe_fingpt_catalyst(symbol, news_result),
        )
        return {
            "symbol": symbol,
            "news": news_result,
            "sentiment": _merge_sentiment_payloads(fmp_sentiment, fingpt_sentiment),
            "catalyst": _merge_catalyst_payloads(fmp_catalyst, fingpt_catalyst),
        }

    news_result, sentiment_result, catalyst_result = await asyncio.gather(
        _safe_news(symbol, limit, news_provider),
        _safe_sentiment(symbol, sentiment_provider),
        _safe_catalyst(symbol, catalyst_provider),
    )
    return {
        "symbol": symbol,
        "news": news_result,
        "sentiment": sentiment_result,
        "catalyst": catalyst_result,
    }


async def _safe_news(
    symbol: str,
    limit: int,
    provider: NewsProvider,
) -> list[ScannerNewsItem]:
    try:
        raw_items = await provider(symbol, limit)
    except Exception as exc:
        logger.warning("market_scanner.context.news_failed symbol=%s error=%s", symbol, exc)
        return []
    normalized: list[ScannerNewsItem] = []
    for raw in raw_items[:limit]:
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        summary = str(raw.get("summary") or raw.get("text") or "").strip() or None
        sentiment = _infer_news_sentiment(f"{title} {summary or ''}")
        normalized.append(
            ScannerNewsItem(
                symbol=str(raw.get("symbol") or symbol).upper(),
                published_date=(
                    str(raw.get("published_date") or raw.get("publishedDate"))
                    if raw.get("published_date") or raw.get("publishedDate")
                    else None
                ),
                title=title,
                source=str(raw.get("source") or raw.get("site") or "unknown"),
                url=str(raw.get("url")) if raw.get("url") else None,
                summary=summary,
                impact=_infer_news_impact(title, summary),
                sentiment=sentiment,
            )
        )
    return normalized


async def _safe_sentiment(symbol: str, provider: SentimentProvider) -> dict[str, Any]:
    try:
        payload = await provider(symbol, 5)
    except Exception as exc:
        logger.warning("market_scanner.context.sentiment_failed symbol=%s error=%s", symbol, exc)
        return _unavailable_sentiment_payload(source="unknown")
    if not payload:
        return _unavailable_sentiment_payload(source="unknown")
    payload = dict(payload)
    payload.setdefault("status", "available")
    if payload.get("label") in {"positive", "negative"}:
        payload["label"] = "bullish" if payload["label"] == "positive" else "bearish"
    payload.setdefault("source", "unknown")
    payload["confidence"] = _sentiment_confidence(payload)
    return payload


async def _safe_fingpt_sentiment(symbol: str, news: list[ScannerNewsItem]) -> dict[str, Any]:
    try:
        from backend.services import fingpt_scanner_context

        payload = await asyncio.to_thread(
            fingpt_scanner_context.score_news,
            symbol,
            _news_items_as_dicts(news),
        )
    except Exception as exc:
        logger.warning(
            "market_scanner.context.fingpt_sentiment_failed symbol=%s error=%s", symbol, exc
        )
        return _unavailable_sentiment_payload(source="fingpt", reason="provider_error")
    if not payload:
        return _unavailable_sentiment_payload(source="fingpt")
    payload = dict(payload)
    payload.setdefault("status", "available")
    payload.setdefault("source", "fingpt")
    payload["confidence"] = _sentiment_confidence(payload)
    if payload.get("label") in {"positive", "negative"}:
        payload["label"] = "bullish" if payload["label"] == "positive" else "bearish"
    return payload


async def _safe_catalyst(symbol: str, provider: CatalystProvider) -> dict[str, Any]:
    try:
        payload = await asyncio.wait_for(
            provider(symbol),
            timeout=_SCANNER_CONTEXT_CATALYST_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "market_scanner.context.catalyst_timeout symbol=%s timeout=%.1fs",
            symbol,
            _SCANNER_CONTEXT_CATALYST_TIMEOUT_SECONDS,
        )
        return {"status": "source unavailable", "event_risk_score": None, "tone": "UNAVAILABLE"}
    except Exception as exc:
        logger.warning("market_scanner.context.catalyst_failed symbol=%s error=%s", symbol, exc)
        return {"status": "source unavailable", "event_risk_score": None, "tone": "UNAVAILABLE"}
    if not payload:
        return {"status": "source unavailable", "event_risk_score": None, "tone": "UNAVAILABLE"}
    payload.setdefault("status", "available")
    return payload


async def _safe_fingpt_catalyst(symbol: str, news: list[ScannerNewsItem]) -> dict[str, Any]:
    try:
        from backend.services import fingpt_scanner_context

        payload = await asyncio.to_thread(
            fingpt_scanner_context.extract_catalysts,
            symbol,
            _news_items_as_dicts(news),
        )
    except Exception as exc:
        logger.warning(
            "market_scanner.context.fingpt_catalyst_failed symbol=%s error=%s", symbol, exc
        )
        return _unavailable_catalyst_payload(source="fingpt", reason="provider_error")
    if not payload:
        return _unavailable_catalyst_payload(source="fingpt")
    payload = dict(payload)
    payload.setdefault("status", "available")
    payload.setdefault("source", "fingpt")
    payload.setdefault("confidence", _sentiment_confidence(payload))
    return payload


async def _safe_snapshot(
    label: str,
    provider: SnapshotProvider,
) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = await provider()
    except Exception as exc:
        logger.warning("market_scanner.context.%s_failed error=%s", label, exc)
        return None, "source unavailable"
    if not payload:
        return None, "source unavailable"
    return payload, "available"


def _scanner_nlp_engine() -> str:
    raw = os.getenv("SCANNER_NLP_ENGINE", "fmp").strip().lower()
    if raw in {"fmp", "fingpt", "hybrid"}:
        return raw
    logger.warning("market_scanner.context.invalid_nlp_engine value=%s fallback=fmp", raw)
    return "fmp"


def _news_items_as_dicts(news: list[ScannerNewsItem]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in news]


def _unavailable_sentiment_payload(
    *,
    source: str,
    reason: str = "source unavailable",
) -> dict[str, Any]:
    return {
        "status": "source unavailable",
        "label": "source unavailable",
        "score": None,
        "source": source,
        "confidence": 0.0,
        "reason": reason,
    }


def _unavailable_catalyst_payload(
    *,
    source: str,
    reason: str = "source unavailable",
) -> dict[str, Any]:
    return {
        "status": "source unavailable",
        "event_risk_score": None,
        "tone": "UNAVAILABLE",
        "source": source,
        "confidence": 0.0,
        "reason": reason,
        "upcoming_catalysts": [],
    }


def _sentiment_confidence(payload: dict[str, Any]) -> float:
    raw = payload.get("confidence")
    if isinstance(raw, int | float):
        return max(0.0, min(1.0, float(raw)))
    if payload.get("status") == "source unavailable":
        return 0.0
    sample_size = payload.get("sample_size") or payload.get("news_count")
    if isinstance(sample_size, int | float) and sample_size > 0:
        return round(max(0.25, min(0.95, 0.35 + 0.08 * float(sample_size))), 4)
    return 0.5


def _payload_available(payload: dict[str, Any] | None) -> bool:
    return bool(payload) and payload.get("status") != "source unavailable"


def _score_01(payload: dict[str, Any]) -> float | None:
    raw = payload.get("score") if "score" in payload else payload.get("sentiment_score")
    if not isinstance(raw, int | float):
        return None
    score = float(raw)
    if -1.0 <= score < 0.0:
        return (score + 1.0) / 2.0
    if 0.0 <= score <= 1.0:
        return score
    if 1.0 < score <= 100.0:
        return score / 100.0
    return max(0.0, min(1.0, score))


def _merge_sentiment_payloads(
    fmp_payload: dict[str, Any],
    fingpt_payload: dict[str, Any],
) -> dict[str, Any]:
    fmp_available = _payload_available(fmp_payload)
    fingpt_available = _payload_available(fingpt_payload)
    if fmp_available and fingpt_available:
        fmp_score = _score_01(fmp_payload)
        fingpt_score = _score_01(fingpt_payload)
        scores = [
            (score, _sentiment_confidence(payload))
            for score, payload in (
                (fmp_score, fmp_payload),
                (fingpt_score, fingpt_payload),
            )
            if score is not None
        ]
        if scores:
            weight_sum = sum(max(confidence, 0.1) for _, confidence in scores)
            score = sum(score * max(confidence, 0.1) for score, confidence in scores) / weight_sum
        else:
            score = 0.5
        confidence = max(_sentiment_confidence(fmp_payload), _sentiment_confidence(fingpt_payload))
        return {
            "status": "available",
            "score": round(score, 4),
            "label": _sentiment_label_from_score(score),
            "source": "hybrid:fmp+fingpt",
            "confidence": round(confidence, 4),
            "components": {"fmp": fmp_payload, "fingpt": fingpt_payload},
        }
    if fmp_available:
        payload = dict(fmp_payload)
        payload["source"] = f"hybrid:{payload.get('source', 'fmp')}"
        payload["components"] = {"fmp": fmp_payload, "fingpt": fingpt_payload}
        return payload
    if fingpt_available:
        payload = dict(fingpt_payload)
        payload["source"] = "hybrid:fingpt"
        payload["components"] = {"fmp": fmp_payload, "fingpt": fingpt_payload}
        return payload
    return _unavailable_sentiment_payload(
        source="hybrid:fmp+fingpt", reason="all_sources_unavailable"
    )


def _merge_catalyst_payloads(
    fmp_payload: dict[str, Any],
    fingpt_payload: dict[str, Any],
) -> dict[str, Any]:
    fmp_available = _payload_available(fmp_payload)
    fingpt_available = _payload_available(fingpt_payload)
    if not fmp_available and not fingpt_available:
        return _unavailable_catalyst_payload(
            source="hybrid:fmp+fingpt", reason="all_sources_unavailable"
        )
    if not fmp_available:
        payload = dict(fingpt_payload)
        payload["source"] = "hybrid:fingpt"
        payload["components"] = {"fmp": fmp_payload, "fingpt": fingpt_payload}
        return payload
    if not fingpt_available:
        payload = dict(fmp_payload)
        payload["source"] = f"hybrid:{payload.get('source', 'fmp')}"
        payload["components"] = {"fmp": fmp_payload, "fingpt": fingpt_payload}
        return payload

    fmp_risk = _unit_number(fmp_payload.get("event_risk_score"))
    fingpt_risk = _unit_number(fingpt_payload.get("event_risk_score"))
    risk_values = [value for value in (fmp_risk, fingpt_risk) if value is not None]
    event_risk = max(risk_values) if risk_values else 0.0
    fmp_tone_confidence = _unit_number(fmp_payload.get("tone_confidence")) or _sentiment_confidence(
        fmp_payload
    )
    fingpt_tone_confidence = _unit_number(
        fingpt_payload.get("tone_confidence")
    ) or _sentiment_confidence(fingpt_payload)
    tone_source = fingpt_payload if fingpt_tone_confidence >= fmp_tone_confidence else fmp_payload
    return {
        "status": "available",
        "event_risk_score": round(event_risk, 4),
        "tone": tone_source.get("tone") or "NEUTRAL",
        "tone_confidence": round(max(fmp_tone_confidence, fingpt_tone_confidence), 4),
        "upcoming_catalysts": _merge_catalyst_lists(
            fmp_payload.get("upcoming_catalysts"),
            fingpt_payload.get("upcoming_catalysts"),
        ),
        "source": "hybrid:fmp+fingpt",
        "confidence": round(
            max(_sentiment_confidence(fmp_payload), _sentiment_confidence(fingpt_payload)), 4
        ),
        "components": {"fmp": fmp_payload, "fingpt": fingpt_payload},
    }


def _unit_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return None


def _merge_catalyst_lists(*values: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("event_type") or ""),
                str(item.get("date") or ""),
                str(item.get("label") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    return merged[:12]


def _symbols_for_context(request: MarketScannerContextRequest) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in [*request.leaders, *request.symbols]:
        symbol = raw.upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if symbols:
        return symbols[:12]
    universe = list_market_scanner_universes().get(request.universe)
    return list(universe.symbols[:8]) if universe else ["SPY", "QQQ", "AAPL", "MSFT"]


def _needs_argentina_context(universe: str, symbols: list[str]) -> bool:
    return "arg" in universe or bool(_ARGENTINA_SYMBOLS.intersection(symbols))


def _infer_news_sentiment(text: str) -> ScannerNewsSentiment:
    lower = text.lower()
    bull = sum(1 for word in _BULLISH_WORDS if word in lower)
    bear = sum(1 for word in _BEARISH_WORDS if word in lower)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _sentiment_score_from_label(label: ScannerNewsSentiment) -> float:
    if label == "bullish":
        return 0.72
    if label == "bearish":
        return 0.28
    return 0.5


def _sentiment_label_from_score(score: float) -> ScannerNewsSentiment:
    return "bullish" if score >= 0.58 else "bearish" if score <= 0.42 else "neutral"


def _infer_news_impact(title: str, summary: str | None) -> ScannerNewsImpact:
    lower = f"{title} {summary or ''}".lower()
    high_terms = ("earnings", "guidance", "fed", "cpi", "sec", "lawsuit", "default")
    medium_terms = ("upgrade", "downgrade", "launch", "partnership", "demand", "margin")
    if any(term in lower for term in high_terms):
        return "high"
    if any(term in lower for term in medium_terms):
        return "medium"
    return "low"


def _impact_rank(impact: ScannerNewsImpact) -> int:
    return {"low": 1, "medium": 2, "high": 3}[impact]


def _tone_from_score(score: float | int | None) -> ScannerBriefTone:
    if not isinstance(score, int | float):
        return "unavailable"
    if score >= 65:
        return "bullish"
    if score <= 35:
        return "bearish"
    return "neutral"


def _build_market_brief(
    *,
    symbols: list[str],
    fear_greed: dict[str, Any] | None,
    news: list[ScannerNewsItem],
    catalysts_by_symbol: dict[str, dict[str, Any]],
    argentina_summary: dict[str, Any] | None,
    sources: dict[str, str],
) -> list[ScannerBriefBlock]:
    current = fear_greed.get("current") if isinstance(fear_greed, dict) else None
    fg_score = current.get("score") if isinstance(current, dict) else None
    fg_label = current.get("label") if isinstance(current, dict) else None

    fg_detail = str(fg_label or "source unavailable")
    fg_value: str
    if isinstance(fg_score, int | float) and isinstance(current, dict):
        vx = current.get("vix")
        vma = current.get("vix_ma50")
        if isinstance(vx, int | float):
            fg_value = f"{float(fg_score):.0f} · VIX {float(vx):.2f}"
            if isinstance(vma, int | float):
                fg_detail = f"{fg_detail} · vs 50d MA {float(vma):.2f}"
        else:
            fg_value = f"{float(fg_score):.0f}"
    else:
        fg_value = "unavailable"

    catalyst_scores = [
        float(payload["event_risk_score"])
        for payload in catalysts_by_symbol.values()
        if isinstance(payload.get("event_risk_score"), int | float)
    ]
    max_catalyst = max(catalyst_scores) if catalyst_scores else None
    high_impact = sum(1 for item in news if item.impact == "high")

    blocks = [
        ScannerBriefBlock(
            key="coverage",
            title="Coverage",
            value=f"{len(symbols)} symbols",
            detail=", ".join(symbols[:6]) + ("..." if len(symbols) > 6 else ""),
            tone="neutral",
            source="market-scanner/context",
            status="available",
        ),
        ScannerBriefBlock(
            key="fear_greed",
            title="Fear & Greed (CNN-style composite)",
            value=fg_value,
            detail=fg_detail,
            tone=_tone_from_score(fg_score),
            source="probabilistic/fear-greed/dashboard + FMP ^VIX",
            status=sources.get("fear_greed", "source unavailable"),
        ),
        ScannerBriefBlock(
            key="news_flow",
            title="News Flow",
            value=f"{len(news)} headlines",
            detail=f"{high_impact} high impact headlines" if news else "source unavailable",
            tone="warning" if high_impact else "neutral",
            source="probabilistic/news",
            status=sources.get("news", "source unavailable"),
        ),
        ScannerBriefBlock(
            key="catalyst_risk",
            title="Catalyst Risk",
            value=(
                f"{max_catalyst * 100:.0f}%" if isinstance(max_catalyst, float) else "unavailable"
            ),
            detail="highest event risk across selected symbols",
            tone=(
                "warning" if isinstance(max_catalyst, float) and max_catalyst >= 0.55 else "neutral"
            ),
            source="probabilistic/catalyst",
            status=sources.get("catalysts", "source unavailable"),
        ),
    ]
    if argentina_summary is not None:
        blocks.append(
            ScannerBriefBlock(
                key="argentina",
                title="Argentina Tape",
                value=str(argentina_summary.get("status") or "available"),
                detail=f"riesgo pais {argentina_summary.get('risk_country', 'n/a')}",
                tone="warning",
                source="argentina/market-summary",
                status=sources.get("argentina", "available"),
            )
        )
    return blocks
