from datetime import datetime, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from backend.models.canonical_signal import CanonicalLegSpec, CanonicalSignalPayload


def test_canonical_signal_valid_options_spread() -> None:
    # ARRANGE
    timestamp = datetime.now(timezone.utc)
    leg1 = CanonicalLegSpec(contract_symbol="SPY260619C00400000", side="buy", ratio=1)
    leg2 = CanonicalLegSpec(contract_symbol="spy260619c00410000", side="sell", ratio=1)

    # ACT
    payload = CanonicalSignalPayload(
        symbol="spy ",
        asset_type="option",
        direction="bullish",
        confidence=0.85,
        entry_price=Decimal("415.50"),
        stop_loss_price=Decimal("400.00"),
        max_loss_usd=Decimal("250.00"),
        structure="call_debit_spread",
        legs=(leg1, leg2),
        source_engine="omni_engine",
        timestamp=timestamp,
        reason_codes=("smc_bullish_alignment", "iv_cheap_buy_outright"),
    )

    # ASSERT
    assert payload.symbol == "SPY"
    assert payload.asset_type == "option"
    assert payload.direction == "bullish"
    assert payload.confidence == 0.85
    assert payload.entry_price == Decimal("415.50")
    assert payload.stop_loss_price == Decimal("400.00")
    assert payload.max_loss_usd == Decimal("250.00")
    assert payload.structure == "call_debit_spread"
    assert len(payload.legs) == 2
    assert payload.legs[0].contract_symbol == "SPY260619C00400000"
    assert payload.legs[1].contract_symbol == "SPY260619C00410000"
    assert payload.legs[1].side == "sell"
    assert payload.timestamp == timestamp
    assert "smc_bullish_alignment" in payload.reason_codes


def test_canonical_signal_rejects_naive_timestamp() -> None:
    # ARRANGE & ACT & ASSERT
    leg = CanonicalLegSpec(contract_symbol="SPY260619C00400000", side="buy", ratio=1)
    naive_dt = datetime.now()  # Sin zona horaria
    
    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=0.85,
            entry_price=Decimal("415.50"),
            structure="long_call",
            legs=(leg,),
            timestamp=naive_dt,
        )


def test_canonical_signal_rejects_out_of_bounds_confidence() -> None:
    # ARRANGE & ACT & ASSERT
    timestamp = datetime.now(timezone.utc)
    
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=-0.1,
            entry_price=Decimal("415.50"),
            structure="long_call",
            timestamp=timestamp,
        )

    with pytest.raises(ValidationError, match="less than or equal to 1"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=1.1,
            entry_price=Decimal("415.50"),
            structure="long_call",
            timestamp=timestamp,
        )


def test_canonical_signal_rejects_negative_or_zero_prices() -> None:
    # ARRANGE & ACT & ASSERT
    timestamp = datetime.now(timezone.utc)

    with pytest.raises(ValidationError, match="greater than 0"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=0.5,
            entry_price=Decimal("0.0"),
            structure="long_call",
            timestamp=timestamp,
        )

    with pytest.raises(ValidationError, match="greater than 0"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=0.5,
            entry_price=Decimal("100.0"),
            stop_loss_price=Decimal("-5.0"),
            structure="long_call",
            timestamp=timestamp,
        )

    with pytest.raises(ValidationError, match="greater than 0"):
        CanonicalSignalPayload(
            symbol="SPY",
            asset_type="option",
            direction="bullish",
            confidence=0.5,
            entry_price=Decimal("100.0"),
            max_loss_usd=Decimal("0.0"),
            structure="long_call",
            timestamp=timestamp,
        )
