"""Matcher de playbooks MVP del módulo Options Strategy. # [PD-3][TH]"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    PlaybookConfig,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsStrategyCandidate,
    OptionsStrategyInput,
    OptionsStructure,
    TradeDirection,
)

_MvpStructure = str


class PlaybookMatch(BaseModel):
    """Playbook elegido con score y razones."""

    model_config = ConfigDict(frozen=True)

    playbook_family: str | None = None
    score: float = 0.0
    reason_codes: tuple[str, ...] = ()


def direction_from_bias(bias: float) -> TradeDirection:
    if bias > 0.12:
        return "bullish"
    if bias < -0.12:
        return "bearish"
    return "neutral"


def _structure_matches_allowed(
    structure: OptionsStructure,
    allowed: tuple[_MvpStructure, ...],
) -> bool:
    if structure == OptionsStructure.NO_TRADE:
        return False
    if structure.value in allowed:
        return True
    if structure == OptionsStructure.BULL_CALL_SPREAD and "call_debit_spread" in allowed:
        return True
    return False


def _score_bull_call_momentum(
    cfg: PlaybookConfig,
    features: NormalizedFeatures,
    candidate: OptionsStrategyCandidate,
) -> tuple[float, tuple[str, ...]]:
    if candidate.selection.structure not in {
        OptionsStructure.BULL_CALL_SPREAD,
        OptionsStructure.CALL_DEBIT_SPREAD,
    }:
        return 0.0, ()
    if not _structure_matches_allowed(candidate.selection.structure, cfg.allowed_structures):
        return 0.0, ()
    score = features.trend_quality_score + abs(features.global_bias) * 0.5
    if cfg.min_trend_quality is not None and features.trend_quality_score < cfg.min_trend_quality:
        return 0.0, ()
    return score, ("bull_call_momentum",)


def _score_pinning_butterfly(
    cfg: PlaybookConfig,
    features: NormalizedFeatures,
    candidate: OptionsStrategyCandidate,
) -> tuple[float, tuple[str, ...]]:
    if candidate.selection.structure != OptionsStructure.CALL_BUTTERFLY:
        return 0.0, ()
    if not _structure_matches_allowed(candidate.selection.structure, cfg.allowed_structures):
        return 0.0, ()
    if features.dealer_regime != "pinning":
        return 0.0, ()
    if cfg.max_dte is not None and candidate.legs and any(
        leg.dte > cfg.max_dte for leg in candidate.legs
    ):
        return 0.0, ()
    score = features.gamma_pressure_score + features.chain_liquidity_score * 0.4
    return score, ("pinning_butterfly",)


def _structure_allowed(
    structure: OptionsStructure,
    allowed: tuple[_MvpStructure, ...],
) -> bool:
    return _structure_matches_allowed(structure, allowed)


def _score_trend_continuation(
    cfg: PlaybookConfig,
    features: NormalizedFeatures,
    candidate: OptionsStrategyCandidate,
) -> tuple[float, tuple[str, ...]]:
    codes: list[str] = []
    score = 0.0
    if cfg.min_trend_quality is not None:
        if features.trend_quality_score < cfg.min_trend_quality:
            return 0.0, ()
        score += features.trend_quality_score
        codes.append("trend_quality_met")
    if cfg.min_predictive_bias is not None:
        if abs(features.predictive_direction_bias) < cfg.min_predictive_bias:
            return 0.0, ()
        score += abs(features.predictive_direction_bias)
        codes.append("predictive_bias_met")
    if cfg.min_options_bias is not None:
        if abs(features.options_direction_bias) < cfg.min_options_bias:
            return 0.0, ()
        score += abs(features.options_direction_bias)
        codes.append("options_bias_met")
    if not _structure_allowed(candidate.selection.structure, cfg.allowed_structures):
        return 0.0, ()
    return score, tuple(codes)


def _score_gamma_wall(
    cfg: PlaybookConfig,
    features: NormalizedFeatures,
    candidate: OptionsStrategyCandidate,
    inp: OptionsStrategyInput,
) -> tuple[float, tuple[str, ...]]:
    if cfg.require_gamma_level:
        ctx = inp.options_context
        if ctx is None or (ctx.call_wall is None and ctx.put_wall is None):
            return 0.0, ()
    if cfg.max_dte is not None and candidate.legs:
        if any(leg.dte > cfg.max_dte for leg in candidate.legs):
            return 0.0, ()
    if not _structure_allowed(candidate.selection.structure, cfg.allowed_structures):
        return 0.0, ()
    score = features.gamma_pressure_score + features.flow_conviction_score * 0.5
    return score, ("gamma_wall_context",)


def _score_compression_breakout(
    cfg: PlaybookConfig,
    features: NormalizedFeatures,
    candidate: OptionsStrategyCandidate,
) -> tuple[float, tuple[str, ...]]:
    required = cfg.require_breakout_state
    if required and features.breakout_state != required:
        return 0.0, ()
    if not _structure_allowed(candidate.selection.structure, cfg.allowed_structures):
        return 0.0, ()
    score = features.structure_alignment_score + features.trend_quality_score * 0.5
    codes = ("compression_breakout",) if required else ()
    return score, codes


class PlaybookMatcher:
    """Selecciona el playbook habilitado con mayor score."""

    @classmethod
    def match(
        cls,
        inp: OptionsStrategyInput,
        features: NormalizedFeatures,
        candidate: OptionsStrategyCandidate,
        *,
        config: OptionsStrategyConfigBundle | None = None,
    ) -> PlaybookMatch:
        active = config or get_options_strategy_config()
        best_name: str | None = None
        best_score = 0.0
        best_codes: tuple[str, ...] = ()

        for name, cfg in active.playbooks.enabled_playbooks().items():
            if name == "gamma_wall_rejection":
                score, codes = _score_gamma_wall(cfg, features, candidate, inp)
            elif name == "compression_breakout":
                score, codes = _score_compression_breakout(cfg, features, candidate)
            elif name in {"trend_continuation", "route1_directional", "route2_directional"}:
                score, codes = _score_trend_continuation(cfg, features, candidate)
            elif name == "bull_call_momentum":
                score, codes = _score_bull_call_momentum(cfg, features, candidate)
            elif name == "pinning_butterfly":
                score, codes = _score_pinning_butterfly(cfg, features, candidate)
            else:
                continue
            if score > best_score:
                best_score = score
                best_name = name
                best_codes = codes

        if best_name is None or best_score <= 0:
            return PlaybookMatch(reason_codes=("no_playbook_match",))
        return PlaybookMatch(
            playbook_family=best_name,
            score=best_score,
            reason_codes=best_codes + (f"playbook_{best_name}",),
        )


__all__ = ["PlaybookMatch", "PlaybookMatcher", "direction_from_bias"]
