"""Round-trip tests for Phase C contract filter serialization."""

from __future__ import annotations

from backend.config.phase_thresholds import (
    get_active_weights,
    reset_to_defaults,
    set_active_weights,
)


def test_flat_dict_roundtrip_preserves_sd_and_optimal_dte() -> None:
    reset_to_defaults()
    base = get_active_weights()
    updated = base.model_copy(
        update={
            "phase_c": base.phase_c.model_copy(
                update={
                    "contract_filters": base.phase_c.contract_filters.model_copy(
                        update={
                            "optimal_dte": 42,
                            "use_sd_strikes": True,
                            "strike_sd_range": 2.5,
                            "max_strikes_each_side": 8,
                            "delta_target_put": -0.40,
                            "use_american_greeks": True,
                            "dividend_yield": 0.015,
                        }
                    )
                }
            )
        }
    )
    set_active_weights(updated)
    flat = get_active_weights().to_flat_dict()
    assert flat["phase_c.optimal_dte"] == 42.0
    assert flat["phase_c.use_sd_strikes"] == 1.0
    assert flat["phase_c.strike_sd_range"] == 2.5
    assert flat["phase_c.delta_target_put"] == -0.40
    reset_to_defaults()
