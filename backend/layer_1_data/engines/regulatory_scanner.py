from __future__ import annotations
"""
backend/layer_1_data/engines/regulatory_scanner.py
════════════════════════════════════════════════════════════════════════════════
Regulatory kill-switch scanner engine (Sector: DATA).
Stateless, deterministic, and fail-graceful adaptation for low-latency scanning.
════════════════════════════════════════════════════════════════════════════════
"""


import re
from datetime import UTC, datetime

# ── Domain Models (Internal) ──────────────────────────────────────────────────
from backend.domain.regulatory_models import (
    RegulatoryActionDirective,
    RegulatorySeverityLevel,
    RegulatoryVetoResult,
)

# ── Configuration ─────────────────────────────────────────────────────────────

_RISK_MATRIX: dict[RegulatorySeverityLevel, list[str]] = {
    RegulatorySeverityLevel.EXISTENTIAL: [
        r"chapter\s+11",
        r"chapter\s+7",
        r"bankrupt(?:cy|ed)?",
        r"fraud\s+investigation",
        r"securities\s+fraud",
        r"delisting",
        r"delist(?:ed|ing)?",
        r"sec\s+subpoena",
        r"grand\s+jury\s+subpoena",
        r"trading\s+suspended",
        r"suspension\s+of\s+trading",
        r"criminal\s+indictment",
        r"asset\s+freeze",
        r"receivership",
        r"fdic\s+takeover",
        r"going\s+concern",
        r"default\s+notice",
        r"event\s+of\s+default",
        r"insolvency",
        r"liquidat(?:ion|ed|ing)",
        r"cease\s+and\s+desist\s+order",
        r"permanent\s+injunction",
    ],
    RegulatorySeverityLevel.HIGH: [
        r"lawsuit",
        r"litigation",
        r"antitrust",
        r"class\s+action",
        r"derivative\s+action",
        r"downgrade",
        r"doj\s+investigation",
        r"regulatory\s+probe",
        r"whistleblower",
        r"restatement",
        r"material\s+weakness",
        r"sec\s+inquiry",
        r"sec\s+investigation",
        r"margin\s+call",
        r"credit\s+watch",
        r"negative\s+watch",
        r"sanctions",
        r"money\s+laundering",
        r"ponzi",
        r"insider\s+trading",
        r"market\s+manipulation",
        r"subpoena",
        r"grand\s+jury",
        r"notice\s+of\s+violation",
        r"consent\s+order",
        r"enforcement\s+action",
        r"civil\s+penalty",
        r"sec\s+charges",
        r"sec\s+complaint",
        r"accounting\s+fraud",
    ],
}

_COMPILED_PATTERNS: dict[RegulatorySeverityLevel, re.Pattern[str]] = {
    tier: re.compile(
        r"(?:" + r"|".join(keywords) + r")",
        flags=re.IGNORECASE | re.UNICODE,
    )
    for tier, keywords in _RISK_MATRIX.items()
}

_WHITESPACE_NORMALIZER: re.Pattern[str] = re.compile(r"\s+")


class RegulatoryScannerEngine:
    """Stateless low-latency scanner for regulatory veto signals."""

    _patterns: dict[RegulatorySeverityLevel, re.Pattern[str]] = _COMPILED_PATTERNS
    _normalizer: re.Pattern[str] = _WHITESPACE_NORMALIZER

    @staticmethod
    def scan_document(
        text: str | None,
        source: str = "UNKNOWN",
    ) -> RegulatoryVetoResult:
        """Scan a raw regulatory text and emit a deterministic veto result."""
        # Use UTC timestamp for consistency with Layer 5 Risk standards
        timestamp = datetime.now(UTC).timestamp()

        try:
            if text is None or not isinstance(text, str) or not text.strip():
                return RegulatoryVetoResult(
                    absolute_veto=False,
                    severity_level=RegulatorySeverityLevel.LOW,
                    action_directive=RegulatoryActionDirective.CLEAR,
                    matched_keywords=[],
                    source=source,
                    scan_timestamp=timestamp,
                    parse_error=True,
                )

            normalized_text = RegulatoryScannerEngine._normalizer.sub(" ", text.lower())

            # Check existential risks first (Absolute Veto)
            existential_hits = RegulatoryScannerEngine._scan_tier(
                normalized_text=normalized_text,
                tier=RegulatorySeverityLevel.EXISTENTIAL,
            )
            if existential_hits:
                return RegulatoryVetoResult(
                    absolute_veto=True,
                    severity_level=RegulatorySeverityLevel.EXISTENTIAL,
                    action_directive=RegulatoryActionDirective.LIQUIDATE,
                    matched_keywords=existential_hits,
                    source=source,
                    scan_timestamp=timestamp,
                    parse_error=False,
                )

            # Check high risks (Risk Weight Adjustment)
            high_hits = RegulatoryScannerEngine._scan_tier(
                normalized_text=normalized_text,
                tier=RegulatorySeverityLevel.HIGH,
            )
            if high_hits:
                return RegulatoryVetoResult(
                    absolute_veto=False,
                    severity_level=RegulatorySeverityLevel.HIGH,
                    action_directive=RegulatoryActionDirective.REDUCE_EXPOSURE,
                    matched_keywords=high_hits,
                    source=source,
                    scan_timestamp=timestamp,
                    parse_error=False,
                )

            return RegulatoryVetoResult(
                absolute_veto=False,
                severity_level=RegulatorySeverityLevel.NONE,
                action_directive=RegulatoryActionDirective.CLEAR,
                matched_keywords=[],
                source=source,
                scan_timestamp=timestamp,
                parse_error=False,
            )
        except Exception:
            return RegulatoryVetoResult(
                absolute_veto=False,
                severity_level=RegulatorySeverityLevel.LOW,
                action_directive=RegulatoryActionDirective.CLEAR,
                matched_keywords=[],
                source=source,
                scan_timestamp=timestamp,
                parse_error=True,
            )

    @staticmethod
    def _scan_tier(
        normalized_text: str,
        tier: RegulatorySeverityLevel,
    ) -> list[str]:
        """Return ordered unique matches for one severity tier."""
        pattern = RegulatoryScannerEngine._patterns.get(tier)
        if pattern is None:
            return []

        matches = pattern.findall(normalized_text)
        return list(dict.fromkeys(matches))


def evaluate_regulatory_document(
    text: str | None,
    source: str = "UNKNOWN",
) -> RegulatoryVetoResult:
    """Functional convenience API for regulatory scan execution."""
    return RegulatoryScannerEngine.scan_document(text=text, source=source)


__all__ = [
    "RegulatoryScannerEngine",
    "evaluate_regulatory_document",
]

# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : regulatory_scanner.py
# Sub-capa         : Engines
# Enfoque          : Escaneo determinista de riesgos regulatorios.
# Eliminado        : Encabezados legacy, imports V1, time.time() -> UTC.
# Preservado       : Diccionario de riesgos, lógica de priorización.
# Pendientes       : Integración con el DataLake para escaneo de noticias.
# ─────────────────────────────────────────────────────────────────────
