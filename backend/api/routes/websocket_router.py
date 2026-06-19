import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.routes.builder_router import get_builder_dashboard_service
from backend.config.logger_setup import get_logger
from backend.domain.portfolio_risk_models import AccountState
from backend.infrastructure.repositories.trade_history_repository import TradeHistoryRepository
from backend.services.bingx_live_ticker_hub import get_live_ticker_hub
from backend.services.builder_state_store import BuilderStateStore
from backend.services.global_context_engine import GlobalContextEngine
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["websocket"])


@router.websocket("/ws/live-ticker")
async def live_ticker_stream(websocket: WebSocket) -> None:
    """Stream BingX account + mark/ticker updates tick-by-tick to the dashboard.

    Message contract (JSON text frames):
    - ``type: "snapshot"`` — initial state right after connect.
    - ``type: "tick"`` — incremental update (account + positions mirror).

    Each payload includes:
    - ``account``: ``total_equity``, ``available_margin``, ``used_margin``
    - ``positions``: rows with ``current_spot``, ``pnl_real_apalancado``, ``current_zone``
    """
    await websocket.accept()
    hub = get_live_ticker_hub()
    await hub.register(websocket)
    logger.info("websocket.live_ticker.connected clients=%s", hub.client_count)
    try:
        while True:
            # Keep the connection alive; venue data is server-pushed.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("websocket.live_ticker.closed error=%s", exc)
    finally:
        await hub.unregister(websocket)


@router.websocket("/ws/funding")
async def funding_stream(websocket: WebSocket) -> None:
    """Stream live funding cockpit telemetry tick-by-tick (every 3 seconds).

    Message contract (JSON text frames):
    - globalContext: GlobalContextSnapshot
    - riskMetrics: RiskMetricsSnapshot
    - builderMetrics: BuilderMetricsResponse
    """
    await websocket.accept()
    logger.info("websocket.funding.connected")

    store = BuilderStateStore()
    dashboard_svc = get_builder_dashboard_service()
    context_engine = GlobalContextEngine()
    perf_engine = PerformanceAnalyticsEngine()
    trade_repo = TradeHistoryRepository()

    try:
        while True:
            # 1. Retrieve live state and metrics
            try:
                live_state = store.load_state("default")
                builder_metrics = dashboard_svc.get_metrics("default")

                # Retrieve macro context
                global_context = context_engine.evaluate({"vix": 15.0, "spy": None, "qqq": None})

                # Calculate live risk metrics using local account balance
                account_state = AccountState(
                    initial_capital=float(live_state.initial_capital),
                    current_equity=float(live_state.current_equity),
                    start_of_day_balance=float(live_state.start_of_day_balance),
                )
                trades = trade_repo.get_recent(window=100)
                risk_metrics = perf_engine.compute_snapshot(trades, account_state, window=100)

                # 2. Stream data
                payload = {
                    "globalContext": json.loads(global_context.model_dump_json()),
                    "riskMetrics": json.loads(risk_metrics.model_dump_json()),
                    "builderMetrics": json.loads(builder_metrics.model_dump_json()),
                }
                await websocket.send_text(json.dumps(payload))
            except Exception as eval_exc:
                logger.error("websocket.funding.evaluation_failed error=%s", eval_exc)

            # 3. Sleep for 3 seconds
            await asyncio.sleep(3.0)

    except WebSocketDisconnect:
        logger.info("websocket.funding.disconnected")
    except Exception as exc:
        logger.error("websocket.funding.error error=%s", exc)


@router.websocket("/ws/alpaca/risk")
async def alpaca_risk_stream(websocket: WebSocket) -> None:
    """Stream Alpaca live risk metrics every 3 seconds."""
    from backend.api.routes.alpaca_risk_router import get_alpaca_service
    from backend.config.funding_thresholds import FundingThresholds
    from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate

    await websocket.accept()
    logger.info("websocket.alpaca_risk.connected")
    svc = get_alpaca_service()
    perf_engine = PerformanceAnalyticsEngine()
    trade_repo = TradeHistoryRepository()
    thresholds = FundingThresholds()
    gate = PreTradeRiskGate.instance()

    try:
        while True:
            try:
                balance = await svc._client.fetch_account_balance()
                equity = float(balance.get("equity") or balance.get("portfolio_value") or 0)
                buying_power = float(balance.get("buying_power") or equity)
                account_state = AccountState(
                    initial_capital=float(thresholds.ftmo_initial_capital),
                    current_equity=equity or float(thresholds.ftmo_initial_capital),
                    start_of_day_balance=buying_power or equity,
                )
                trades = [
                    t
                    for t in trade_repo.get_recent(window=100)
                    if t.mode in {"paper", "live", "alpaca"}
                ]
                risk_metrics = perf_engine.compute_snapshot(trades, account_state, window=100)
                gate.update_bur(risk_metrics.bur)
                payload = {
                    "riskMetrics": json.loads(risk_metrics.model_dump_json()),
                    "bufferZone": gate.buffer_zone,
                    "bur": gate.bur,
                    "openPositions": len(svc._risk_desk.open_positions),
                }
                await websocket.send_text(json.dumps(payload))
            except Exception as eval_exc:
                logger.error("websocket.alpaca_risk.evaluation_failed error=%s", eval_exc)
            await asyncio.sleep(3.0)
    except WebSocketDisconnect:
        logger.info("websocket.alpaca_risk.disconnected")
    except Exception as exc:
        logger.error("websocket.alpaca_risk.error error=%s", exc)
