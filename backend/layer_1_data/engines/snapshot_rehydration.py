"""
backend/layer_1_data/engines/snapshot_rehydration.py
════════════════════════════════════════════════════════════════════════════════
Deterministic trade snapshot rehydration engine (Sector: DATA).
Stateless and fail-graceful adaptation for high-fidelity visualization.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

# ── Domain Models (Internal) ──────────────────────────────────────────────────
from backend.domain.snapshot_models import (
    AnnotationType,
    BoxAnnotation,
    ChartMetadata,
    ChartViewModel,
    DynamicLine,
    FrozenOHLCVBar,
    HLineAnnotation,
    MarkerAnnotation,
    MarkerPosition,
    MarkerShape,
    SMCStructureType,
    SnapshotGEX,
    SnapshotIndicators,
    SnapshotOHLCVBar,
    SnapshotOrderSide,
    SnapshotRisk,
    SnapshotSMCStructure,
    SnapshotVSASignal,
    TradeDNARecord,
    TradeExecutionMarker,
    VSASignalType,
)


class _ForensicPalette:
    """Institutional-grade color palette for forensic visualization."""

    OB_BULLISH_FILL: Final[str] = "rgba(0, 180, 100, 0.15)"
    OB_BULLISH_BORDER: Final[str] = "#00B464"
    OB_BEARISH_FILL: Final[str] = "rgba(220, 50, 50, 0.15)"
    OB_BEARISH_BORDER: Final[str] = "#DC3232"

    FVG_BULLISH_FILL: Final[str] = "rgba(80, 160, 255, 0.12)"
    FVG_BULLISH_BORDER: Final[str] = "#50A0FF"
    FVG_BEARISH_FILL: Final[str] = "rgba(255, 140, 40, 0.12)"
    FVG_BEARISH_BORDER: Final[str] = "#FF8C28"

    ZERO_GAMMA: Final[str] = "#A855F7"
    CALL_WALL: Final[str] = "#22C55E"
    PUT_WALL: Final[str] = "#EF4444"
    HV_TRIGGER: Final[str] = "#F59E0B"

    VWAP: Final[str] = "#F59E0B"
    VWAP_UPPER: Final[str] = "rgba(245, 158, 11, 0.5)"
    VWAP_LOWER: Final[str] = "rgba(245, 158, 11, 0.5)"

    EMA_FAST: Final[str] = "#60A5FA"
    EMA_SLOW: Final[str] = "#F87171"

    STOP_LOSS: Final[str] = "#EF4444"
    TAKE_PROFIT: Final[str] = "#22C55E"
    INVALIDATION: Final[str] = "#F97316"

    VSA_BULLISH: Final[str] = "#00D4AA"
    VSA_BEARISH: Final[str] = "#FF6B6B"
    VSA_NEUTRAL: Final[str] = "#94A3B8"

    TRADE_BUY: Final[str] = "#00FF88"
    TRADE_SELL: Final[str] = "#FF4466"


_PALETTE = _ForensicPalette()


class SnapshotRehydrationEngine:
    """Stateless rehydration engine from immutable TradeDNA records."""

    @staticmethod
    def build_visual_snapshot(dna_record: TradeDNARecord) -> ChartViewModel | None:
        """Reconstruct a deterministic chart view model from one trade DNA record."""
        try:
            frozen_bars = SnapshotRehydrationEngine._build_frozen_bars(dna_record.historical_bars)

            smc_annotations = SnapshotRehydrationEngine._translate_smc_structures(
                dna_record.smc_structures,
                dna_record.historical_bars,
            )
            risk_hlines = SnapshotRehydrationEngine._translate_risk_hlines(dna_record.risk_snapshot)
            vsa_markers = SnapshotRehydrationEngine._translate_vsa_signals(
                dna_record.vsa_signals,
                dna_record.historical_bars,
            )

            dynamic_lines = {}
            dynamic_lines.update(
                SnapshotRehydrationEngine._translate_gex_to_lines(dna_record.gex_snapshot)
            )
            dynamic_lines.update(
                SnapshotRehydrationEngine._translate_indicators_to_lines(dna_record.indicators)
            )

            static_annotations = tuple([*smc_annotations, *risk_hlines, *vsa_markers])
            trade_marker = SnapshotRehydrationEngine._build_trade_execution_marker(dna_record)

            metadata = ChartMetadata(
                trade_id=str(dna_record.trade_id),
                trade_hash=dna_record.trade_hash,
                symbol=dna_record.symbol,
                timeframe=dna_record.timeframe.value,
                execution_timestamp=dna_record.timestamp_utc.isoformat(),
                execution_price=float(dna_record.execution_price),
                order_side=dna_record.order_side.value,
                hash_integrity_status=SnapshotRehydrationEngine._verify_hash_integrity(dna_record),
                total_bars=len(frozen_bars),
                total_annotations=len(static_annotations),
                session_at_trade=dna_record.macro_snapshot.session,
                market_regime=dna_record.macro_snapshot.market_regime.value,
            )

            return ChartViewModel(
                metadata=metadata,
                historical_bars=frozen_bars,
                dynamic_lines=dynamic_lines,
                static_annotations=static_annotations,
                trade_marker=trade_marker,
            )
        except Exception:
            return None

    @staticmethod
    def _verify_hash_integrity(dna: TradeDNARecord) -> str:
        canonical_payload = {
            "trade_id": str(dna.trade_id),
            "symbol": dna.symbol,
            "timestamp_utc": dna.timestamp_utc.isoformat(),
            "execution_price": str(dna.execution_price),
            "order_side": dna.order_side.value,
        }
        recomputed = hashlib.sha256(
            json.dumps(canonical_payload, sort_keys=True).encode()
        ).hexdigest()
        return "VERIFIED" if recomputed == dna.trade_hash else "TAMPERED"

    @staticmethod
    def _build_frozen_bars(
        historical_bars: tuple[SnapshotOHLCVBar, ...],
    ) -> tuple[FrozenOHLCVBar, ...]:
        return tuple(
            FrozenOHLCVBar(
                time=int(bar.timestamp_utc.timestamp()),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )
            for bar in historical_bars
        )

    @staticmethod
    def _bar_index_to_unix(
        historical_bars: tuple[SnapshotOHLCVBar, ...],
        bar_index: int,
    ) -> int:
        index = max(0, min(bar_index, len(historical_bars) - 1))
        return int(historical_bars[index].timestamp_utc.timestamp())

    @staticmethod
    def _last_bar_unix(historical_bars: tuple[SnapshotOHLCVBar, ...]) -> int:
        return int(historical_bars[-1].timestamp_utc.timestamp())

    @staticmethod
    def _translate_smc_structures(
        smc_structures: tuple[SnapshotSMCStructure, ...],
        historical_bars: tuple[SnapshotOHLCVBar, ...],
    ) -> list[BoxAnnotation]:
        annotations: list[BoxAnnotation] = []

        for idx, structure in enumerate(smc_structures):
            if structure.structure_type not in {
                SMCStructureType.BULLISH_ORDER_BLOCK,
                SMCStructureType.BEARISH_ORDER_BLOCK,
                SMCStructureType.BULLISH_FVG,
                SMCStructureType.BEARISH_FVG,
            }:
                continue

            is_bullish = structure.structure_type in {
                SMCStructureType.BULLISH_ORDER_BLOCK,
                SMCStructureType.BULLISH_FVG,
            }
            is_ob = structure.structure_type in {
                SMCStructureType.BULLISH_ORDER_BLOCK,
                SMCStructureType.BEARISH_ORDER_BLOCK,
            }

            if is_ob and is_bullish:
                fill_color = _PALETTE.OB_BULLISH_FILL
                border_color = _PALETTE.OB_BULLISH_BORDER
            elif is_ob:
                fill_color = _PALETTE.OB_BEARISH_FILL
                border_color = _PALETTE.OB_BEARISH_BORDER
            elif is_bullish:
                fill_color = _PALETTE.FVG_BULLISH_FILL
                border_color = _PALETTE.FVG_BULLISH_BORDER
            else:
                fill_color = _PALETTE.FVG_BEARISH_FILL
                border_color = _PALETTE.FVG_BEARISH_BORDER

            time_start = SnapshotRehydrationEngine._bar_index_to_unix(
                historical_bars,
                structure.bar_index_start,
            )
            time_end = (
                SnapshotRehydrationEngine._bar_index_to_unix(
                    historical_bars,
                    structure.bar_index_end,
                )
                if structure.bar_index_end is not None
                else SnapshotRehydrationEngine._last_bar_unix(historical_bars)
            )

            label_short = ("OB" if is_ob else "FVG") + (" Bull" if is_bullish else " Bear")
            label = structure.label or f"{label_short} @ {float(structure.price_high):.2f}"

            annotations.append(
                BoxAnnotation(
                    annotation_id=f"smc_{idx:04d}_{structure.structure_type.value}",
                    annotation_type=AnnotationType.BOX,
                    label=label,
                    label_short=label_short,
                    time_start=time_start,
                    time_end=time_end,
                    price_top=float(structure.price_high),
                    price_bottom=float(structure.price_low),
                    fill_color=fill_color,
                    border_color=border_color,
                    is_mitigated=structure.is_mitigated,
                    opacity=0.08 if structure.is_mitigated else 0.2,
                    source_snapshot="smc_structures",
                )
            )

        return annotations

    @staticmethod
    def _translate_gex_to_lines(gex: SnapshotGEX) -> dict[str, DynamicLine]:
        lines: dict[str, DynamicLine] = {
            "zero_gamma": DynamicLine(
                line_id="zero_gamma",
                label=f"Zero Gamma: {float(gex.zero_gamma_level):.2f}",
                color=_PALETTE.ZERO_GAMMA,
                line_style="dotted",
                line_width=2,
                price_value=float(gex.zero_gamma_level),
                z_index=10,
                tooltip=(
                    f"GEX regime: {gex.gex_regime.upper()} | "
                    f"Net GEX: ${float(gex.net_gex_usd):,.0f}"
                ),
            ),
            "call_wall": DynamicLine(
                line_id="call_wall",
                label=f"Call Wall: {float(gex.call_wall):.2f}",
                color=_PALETTE.CALL_WALL,
                line_style="dashed",
                line_width=1,
                price_value=float(gex.call_wall),
                z_index=8,
            ),
            "put_wall": DynamicLine(
                line_id="put_wall",
                label=f"Put Wall: {float(gex.put_wall):.2f}",
                color=_PALETTE.PUT_WALL,
                line_style="dashed",
                line_width=1,
                price_value=float(gex.put_wall),
                z_index=8,
            ),
        }

        if gex.hv_trigger is not None:
            lines["hv_trigger"] = DynamicLine(
                line_id="hv_trigger",
                label=f"HV Trigger: {float(gex.hv_trigger):.2f}",
                color=_PALETTE.HV_TRIGGER,
                line_style="dotted",
                line_width=1,
                price_value=float(gex.hv_trigger),
                z_index=7,
            )

        return lines

    @staticmethod
    def _translate_indicators_to_lines(
        indicators: SnapshotIndicators,
    ) -> dict[str, DynamicLine]:
        lines: dict[str, DynamicLine] = {}

        if indicators.vwap is not None:
            lines["vwap"] = DynamicLine(
                line_id="vwap",
                label=f"VWAP: {float(indicators.vwap):.2f}",
                color=_PALETTE.VWAP,
                line_style="solid",
                line_width=2,
                price_value=float(indicators.vwap),
                z_index=9,
            )
        if indicators.vwap_upper_band is not None:
            lines["vwap_upper"] = DynamicLine(
                line_id="vwap_upper",
                label="VWAP +1sigma",
                color=_PALETTE.VWAP_UPPER,
                line_style="dashed",
                line_width=1,
                price_value=float(indicators.vwap_upper_band),
                z_index=5,
            )
        if indicators.vwap_lower_band is not None:
            lines["vwap_lower"] = DynamicLine(
                line_id="vwap_lower",
                label="VWAP -1sigma",
                color=_PALETTE.VWAP_LOWER,
                line_style="dashed",
                line_width=1,
                price_value=float(indicators.vwap_lower_band),
                z_index=5,
            )
        if indicators.ema_fast is not None:
            lines["ema_fast"] = DynamicLine(
                line_id="ema_fast",
                label=f"EMA Fast: {float(indicators.ema_fast):.2f}",
                color=_PALETTE.EMA_FAST,
                line_style="solid",
                line_width=1,
                price_value=float(indicators.ema_fast),
                z_index=6,
            )
        if indicators.ema_slow is not None:
            lines["ema_slow"] = DynamicLine(
                line_id="ema_slow",
                label=f"EMA Slow: {float(indicators.ema_slow):.2f}",
                color=_PALETTE.EMA_SLOW,
                line_style="solid",
                line_width=1,
                price_value=float(indicators.ema_slow),
                z_index=6,
            )

        for key, value in indicators.custom_lines.items():
            lines[f"custom_{key}"] = DynamicLine(
                line_id=f"custom_{key}",
                label=f"{key}: {float(value):.2f}",
                color="#94A3B8",
                line_style="dashed",
                line_width=1,
                price_value=float(value),
                z_index=4,
            )

        return lines

    @staticmethod
    def _translate_risk_hlines(risk: SnapshotRisk) -> list[HLineAnnotation]:
        hlines: list[HLineAnnotation] = [
            HLineAnnotation(
                annotation_id="risk_stop_loss",
                annotation_type=AnnotationType.HLINE,
                price=float(risk.stop_loss_price),
                label=f"SL: {float(risk.stop_loss_price):.2f}",
                color=_PALETTE.STOP_LOSS,
                line_style="dashed",
                line_width=2,
                source_snapshot="risk_snapshot.stop_loss_price",
            ),
            HLineAnnotation(
                annotation_id="risk_take_profit",
                annotation_type=AnnotationType.HLINE,
                price=float(risk.take_profit_price),
                label=f"TP: {float(risk.take_profit_price):.2f} | R:R {risk.risk_reward_ratio:.1f}x",
                color=_PALETTE.TAKE_PROFIT,
                line_style="dashed",
                line_width=2,
                source_snapshot="risk_snapshot.take_profit_price",
            ),
        ]

        if risk.invalidation_price is not None:
            hlines.append(
                HLineAnnotation(
                    annotation_id="risk_invalidation",
                    annotation_type=AnnotationType.HLINE,
                    price=float(risk.invalidation_price),
                    label=f"Invalidation: {float(risk.invalidation_price):.2f}",
                    color=_PALETTE.INVALIDATION,
                    line_style="dotted",
                    line_width=1,
                    source_snapshot="risk_snapshot.invalidation_price",
                )
            )

        return hlines

    @staticmethod
    def _translate_vsa_signals(
        vsa_signals: tuple[SnapshotVSASignal, ...],
        historical_bars: tuple[SnapshotOHLCVBar, ...],
    ) -> list[MarkerAnnotation]:
        shape_map: dict[VSASignalType, tuple[MarkerShape, MarkerPosition, str]] = {
            VSASignalType.CLIMACTIC_ACTION: (
                MarkerShape.DIAMOND,
                MarkerPosition.ABOVE_BAR,
                _PALETTE.VSA_NEUTRAL,
            ),
            VSASignalType.NO_SUPPLY: (
                MarkerShape.ARROW_UP,
                MarkerPosition.BELOW_BAR,
                _PALETTE.VSA_BULLISH,
            ),
            VSASignalType.NO_DEMAND: (
                MarkerShape.ARROW_DOWN,
                MarkerPosition.ABOVE_BAR,
                _PALETTE.VSA_BEARISH,
            ),
            VSASignalType.EFFORT_VS_RESULT: (
                MarkerShape.SQUARE,
                MarkerPosition.ABOVE_BAR,
                _PALETTE.VSA_NEUTRAL,
            ),
            VSASignalType.STOPPING_VOLUME: (
                MarkerShape.CIRCLE,
                MarkerPosition.BELOW_BAR,
                _PALETTE.VSA_BULLISH,
            ),
            VSASignalType.TEST: (
                MarkerShape.FLAG_UP,
                MarkerPosition.BELOW_BAR,
                _PALETTE.VSA_BULLISH,
            ),
        }

        markers: list[MarkerAnnotation] = []
        for idx, signal in enumerate(vsa_signals):
            shape, position, color = shape_map[signal.signal_type]
            bar_unix = SnapshotRehydrationEngine._bar_index_to_unix(
                historical_bars,
                signal.bar_index,
            )
            markers.append(
                MarkerAnnotation(
                    annotation_id=f"vsa_{idx:04d}_{signal.signal_type.value}",
                    annotation_type=AnnotationType.MARKER,
                    time=bar_unix,
                    price=float(signal.price),
                    shape=shape,
                    position=position,
                    color=color,
                    size=1,
                    label=signal.signal_type.value.replace("_", " ").upper()[:4],
                    tooltip=(
                        f"{signal.signal_type.value} | "
                        f"price={float(signal.price):.4f} | "
                        f"volume={float(signal.volume):,.0f}"
                    ),
                    source_snapshot="vsa_signals",
                )
            )
        return markers

    @staticmethod
    def _build_trade_execution_marker(dna: TradeDNARecord) -> TradeExecutionMarker:
        bar_unix = SnapshotRehydrationEngine._bar_index_to_unix(
            dna.historical_bars,
            dna.execution_bar_index,
        )
        is_buy = dna.order_side == SnapshotOrderSide.BUY
        return TradeExecutionMarker(
            time=bar_unix,
            price=float(dna.execution_price),
            side=dna.order_side,
            shape=MarkerShape.ARROW_UP if is_buy else MarkerShape.ARROW_DOWN,
            position=MarkerPosition.BELOW_BAR if is_buy else MarkerPosition.ABOVE_BAR,
            color=_PALETTE.TRADE_BUY if is_buy else _PALETTE.TRADE_SELL,
            label=(f"{'BUY' if is_buy else 'SELL'} @ {float(dna.execution_price):.4f}"),
            risk_reward=dna.risk_snapshot.risk_reward_ratio,
            stop_loss=float(dna.risk_snapshot.stop_loss_price),
            take_profit=float(dna.risk_snapshot.take_profit_price),
        )


def rehydrate_trade_snapshot(dna_record: TradeDNARecord) -> ChartViewModel | None:
    """Functional convenience API for deterministic snapshot rehydration."""
    return SnapshotRehydrationEngine.build_visual_snapshot(dna_record)


__all__ = [
    "SnapshotRehydrationEngine",
    "rehydrate_trade_snapshot",
]

# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : snapshot_rehydration.py
# Sub-capa         : Engines
# Enfoque          : Rehidratación funcional de TradeDNA a ChartViewModel.
# Eliminado        : Encabezados legacy, imports V1, noise.
# Preservado       : Lógica de traducción visual, paleta institucional.
# Pendientes       : Integración con la capa de persistencia (Phase 8).
# ─────────────────────────────────────────────────────────────────────
