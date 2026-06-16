from decimal import Decimal
from pathlib import Path

from backend.domain.builder_models import BuilderPayoutCycleRecord, mffu_builder_50k_profile
from backend.services.builder_state_machine import (
    advance_eval_to_sim,
    default_builder_account_state,
    evaluate_builder_phase,
)
from backend.services.builder_state_store import BuilderStateStore


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "predictions.db"
    store = BuilderStateStore(predictions_db=db_path)

    store.ensure_schema()
    store.ensure_schema()

    assert db_path.exists()


def test_state_persistence_round_trip(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile, account_id="acct-1").model_copy(
        update={
            "current_equity": Decimal("53000"),
            "trading_days_count": 1,
        }
    )
    transitioned = evaluate_builder_phase(state, profile).state

    store.save_state(transitioned)
    loaded = store.load_state(account_id="acct-1")

    assert loaded.phase == "EVAL_PASSED_PENDING"
    assert loaded.current_equity == Decimal("53000")
    assert loaded.trading_days_count == 1


def test_simulated_eval_flow_persists_through_sim(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    profile = mffu_builder_50k_profile()
    state = default_builder_account_state(profile).model_copy(
        update={
            "current_equity": Decimal("53000"),
            "trading_days_count": 1,
        }
    )

    pending = evaluate_builder_phase(state, profile).state
    store.save_state(pending)
    sim = advance_eval_to_sim(store.load_state()).state
    store.save_state(sim)
    loaded = store.load_state()

    assert loaded.phase == "SIM_ACTIVE"


def test_payout_cycle_persistence_round_trip(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")
    cycle = BuilderPayoutCycleRecord(
        cycle_id="cycle-1",
        account_id="default",
        cycle_number=1,
        buffer_target=Decimal("2100"),
        buffer_progress=Decimal("900"),
        qualified_days_count=1,
        withdrawable_amount=Decimal("0"),
    )

    store.save_payout_cycle(cycle)
    loaded_cycles = store.list_payout_cycles()

    assert len(loaded_cycles) == 1
    assert loaded_cycles[0].cycle_id == "cycle-1"
    assert loaded_cycles[0].buffer_progress == Decimal("900")


def test_create_payout_cycle_assigns_incremental_number(tmp_path: Path) -> None:
    store = BuilderStateStore(predictions_db=tmp_path / "predictions.db")

    first = store.create_payout_cycle()
    second = store.create_payout_cycle()

    assert first.cycle_number == 1
    assert second.cycle_number == 2
    assert second.buffer_target == Decimal("2100")
