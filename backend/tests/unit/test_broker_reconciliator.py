from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.domain.builder_models import BuilderAccountState
from backend.services.builder_state_store import BuilderStateStore
from backend.services.broker_reconciliator import BrokerStateReconciliator
from backend.layer_1_data.datos.alpaca_client import AlpacaClient


@pytest.fixture
def temp_db(tmp_path) -> str:
    return str(tmp_path / "test_predictions.db")


@pytest.fixture
def store(temp_db) -> BuilderStateStore:
    # Asegurar que la caché esté vacía para aislamiento
    BuilderStateStore._state_cache.clear()
    store = BuilderStateStore(predictions_db=temp_db)
    store.ensure_schema()
    return store


def test_builder_state_store_uses_in_memory_cache(store) -> None:
    # ARRANGE
    account_id = "cache-test-account"
    state = BuilderAccountState(
        account_id=account_id,
        initial_capital=Decimal("50000"),
        current_equity=Decimal("50000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
    )

    # ACT
    store.save_state(state)
    
    # 1. Verificar escritura en caché
    assert account_id in BuilderStateStore._state_cache
    assert BuilderStateStore._state_cache[account_id].current_equity == Decimal("50000")

    # 2. Mutar caché directamente para verificar hit de lectura
    BuilderStateStore._state_cache[account_id] = state.model_copy(
        update={"current_equity": Decimal("99999")}
    )
    loaded = store.load_state(account_id)
    assert loaded.current_equity == Decimal("99999")  # Cargado desde caché


@pytest.mark.asyncio
async def test_reconciliator_successful_sync(store) -> None:
    # ARRANGE
    account_id = "sync-account"
    initial_state = BuilderAccountState(
        account_id=account_id,
        initial_capital=Decimal("50000"),
        current_equity=Decimal("50000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
    )
    store.save_state(initial_state)

    # Mock del cliente Alpaca
    client = MagicMock(spec=AlpacaClient)
    client.fetch_account_balance = AsyncMock(return_value={
        "equity": "52000.0",
        "last_equity": "51000.0",
    })
    client.fetch_positions = AsyncMock(return_value=[
        {"symbol": "AAPL", "unrealized_pl": "400.0"},
        {"symbol": "MSFT", "unrealized_pl": "100.0"},
    ])

    reconciliator = BrokerStateReconciliator(store)

    # ACT
    updated_state = await reconciliator.reconcile(account_id, client=client)

    # ASSERT
    # Total daily PnL = equity - last_equity = 52000 - 51000 = 1000 USD
    # Unrealized PnL = 400 + 100 = 500 USD
    # Realized daily PnL = total - unrealized = 1000 - 500 = 500 USD
    assert updated_state.current_equity == Decimal("52000.0")
    assert updated_state.unrealized_pnl == Decimal("500.0")
    assert updated_state.realized_daily_pnl == Decimal("500.0")
    assert updated_state.high_watermark_balance == Decimal("52000.0")  # HWM actualizado


@pytest.mark.asyncio
async def test_reconciliator_fallback_on_failure(store) -> None:
    # ARRANGE
    account_id = "fallback-account"
    initial_state = BuilderAccountState(
        account_id=account_id,
        initial_capital=Decimal("50000"),
        current_equity=Decimal("50000"),
        start_of_day_balance=Decimal("50000"),
        high_watermark_balance=Decimal("50000"),
    )
    store.save_state(initial_state)

    # Mock del cliente Alpaca para simular fallo
    client = MagicMock(spec=AlpacaClient)
    client.fetch_account_balance = AsyncMock(side_effect=Exception("Connection timed out"))

    reconciliator = BrokerStateReconciliator(store)

    # ACT
    result_state = await reconciliator.reconcile(account_id, client=client)

    # ASSERT - debe retornar el estado cacheado sin cambios ante fallo
    assert result_state.current_equity == Decimal("50000")
    assert result_state.high_watermark_balance == Decimal("50000")
