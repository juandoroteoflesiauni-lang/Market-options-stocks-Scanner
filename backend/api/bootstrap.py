import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)


async def bootstrap_application(
    app: FastAPI, settings: Any
) -> tuple[asyncio.Task, asyncio.Task, Any]:
    from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB, PREDICTIONS_DB
    from backend.infrastructure.sqlite_health import ensure_healthy_or_quarantine
    from backend.services.options_gex_snapshot_store import OptionsGexSnapshotStore

    ensure_healthy_or_quarantine(OPTIONS_GEX_SNAPSHOTS_DB)
    ensure_healthy_or_quarantine(PREDICTIONS_DB)
    OptionsGexSnapshotStore()._init_db()

    from backend.bus.event_bus import EventBus
    from backend.engine.quantitative_engine import QuantitativeEngine
    from backend.hub.market_data_hub import MarketDataHub
    from backend.hub.streams.alpaca_streamer import AlpacaStreamer

    event_bus = EventBus()
    hub = MarketDataHub(settings=settings, event_bus=event_bus)
    engine = QuantitativeEngine(event_bus=event_bus)
    emitter = AlpacaStreamer(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
        universe=settings.default_universe,
        hub=hub,
    )

    app.state.settings = settings
    app.state.event_bus = event_bus
    app.state.hub = hub
    app.state.engine = engine
    app.state.emitter = emitter

    from backend.api.routes.alpaca_bot_router import configure_service as configure_alpaca_service
    from backend.api.routes.bingx_bot_router import _hc_cache_fresh as bingx_healthcheck_cache_fresh
    from backend.api.routes.bingx_bot_router import (
        configure_audit_store as configure_bingx_audit_store,
    )
    from backend.api.routes.bingx_bot_router import configure_scheduler as configure_bingx_scheduler
    from backend.api.routes.bingx_bot_router import configure_service as configure_bingx_service
    from backend.api.routes.options_router import options_snapshot_service
    from backend.layer_1_data.datos.alpaca_client import AlpacaClient
    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient
    from backend.quant_engine.engines.technical.avwap_hybrid.avwap_engine import AVWAPEngine
    from backend.services.alpaca_bot_service import AlpacaBotService
    from backend.services.bingx_audit_store import BingXAuditStore
    from backend.services.bingx_bot_service import BingXBotService
    from backend.services.bingx_live_ticker_hub import configure_live_ticker_hub
    from backend.services.bingx_universe import BingXUniverseService
    from backend.services.market_breadth_tracker import MarketBreadthTracker
    from backend.tasks.bingx_bot_scheduler import BingXBotScheduler
    from backend.tasks.scanner_scheduler import ScannerScheduler, ScannerSchedulerConfig

    async def _bingx_venue_technical_fetcher(
        sym: str, candles: list[dict[str, Any]], timeframe: str
    ) -> dict[str, Any]:
        from backend.services.technical_terminal_payload import (
            build_technical_terminal_payload_from_candles,
        )

        return await build_technical_terminal_payload_from_candles(sym, candles, timeframe)

    _trading_env = settings.bingx_bot_trading_env.strip().lower()
    _env_dry_run = os.getenv("BINGX_DRY_RUN", "").strip().lower()
    _force_venue_live = _env_dry_run in {"0", "false", "no", "live"}
    _live = (
        _trading_env in {"prod-vst", "prod-live"}
        or settings.bingx_bot_enable_live
        or _force_venue_live
    )
    _bx_key = settings.bingx_api_key.get_secret_value() if settings.bingx_api_key else None
    _bx_secret = settings.bingx_secret.get_secret_value() if settings.bingx_secret else None

    if _trading_env == "prod-vst" or (_force_venue_live and _trading_env != "prod-live"):
        _bingx_client = BingXClient(
            api_key=_bx_key,
            secret_key=_bx_secret,
            base_url=BINGX_REST_VST_BASE,
            dry_run=False,
            allow_env_dry_run_override=False,
        )
    elif _trading_env == "prod-live" and _live:
        _bingx_client = BingXClient(
            api_key=_bx_key, secret_key=_bx_secret, dry_run=False, allow_env_dry_run_override=False
        )
    else:
        _bingx_client = BingXClient(
            api_key=_bx_key,
            secret_key=_bx_secret,
            dry_run=not _live,
            allow_env_dry_run_override=False,
        )

    _fmp_client = FMPClient()
    _massive_client = MassiveClient()
    _universe_service = BingXUniverseService(
        client=_bingx_client, fmp_client=_fmp_client, massive_client=_massive_client
    )
    _avwap_engine = AVWAPEngine(fmp_api_key=settings.fmp_api_key.get_secret_value())

    async def _dark_pool_snapshot_fn(underlying: str) -> Any:
        """Resolve a dark-pool snapshot via the Hub (Motor ⑭); None on failure."""
        result = await hub.fetch_dark_pool_prints(underlying)
        return result.unwrap() if result.is_success else None

    _bingx_service = BingXBotService(
        avwap_engine=_avwap_engine,
        client=_bingx_client,
        options_snapshot_fn=options_snapshot_service,
        venue_technical_fn=_bingx_venue_technical_fetcher,
        fmp_client=_fmp_client,
        massive_client=_massive_client,
        universe_service=_universe_service,
        dark_pool_fn=_dark_pool_snapshot_fn,
    )
    _audit_path = Path(settings.bingx_bot_audit_db_path)
    os.makedirs(_audit_path.parent, exist_ok=True)
    _audit_store = BingXAuditStore(_audit_path)
    configure_bingx_service(_bingx_service)
    configure_bingx_audit_store(_audit_store)
    configure_bingx_scheduler(
        BingXBotScheduler(
            service=_bingx_service, audit_store=_audit_store, hc_ok_fn=bingx_healthcheck_cache_fresh
        )
    )

    _mode = settings.alpaca_trading_mode.strip().lower()
    _alpaca_base = (
        settings.alpaca_live_base_url if _mode == "live" else settings.alpaca_trading_base_url
    )
    _alpaca_client = AlpacaClient(
        api_key=settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else None,
        secret_key=(
            settings.alpaca_api_secret.get_secret_value() if settings.alpaca_api_secret else None
        ),
        base_url=_alpaca_base,
        dry_run=_mode == "dry_run",
    )
    _alpaca_service = AlpacaBotService(
        client=_alpaca_client,
        universe=settings.default_universe,
        trading_mode=_mode,
    )
    configure_alpaca_service(_alpaca_service)

    from backend.services.equity_l2_feed_service import (
        EquityL2FeedService,
        configure_equity_l2_feed,
        equity_l2_feed_enabled,
    )

    _equity_l2_feed = EquityL2FeedService(client=_bingx_client)
    configure_equity_l2_feed(_equity_l2_feed)
    if equity_l2_feed_enabled():
        app.state.equity_l2_feed = _equity_l2_feed
        app.state.equity_l2_feed_task = _equity_l2_feed.start_background()
        logger.info("Equity L2 watchlist feed started (BingX REST bootstrap + stream workers)")

    from backend.services.options_gex_institutional_capture_service import (
        configure_options_gex_capture_service,
        get_options_gex_capture_service,
        options_gex_capture_enabled,
    )

    if options_gex_capture_enabled():
        _gex_capture = get_options_gex_capture_service()
        configure_options_gex_capture_service(_gex_capture)
        app.state.options_gex_capture = _gex_capture
        app.state.options_gex_capture_task = _gex_capture.start_background()
        logger.info("Options GEX institutional capture started (R1 watchlist, ~5min cadence)")

    from backend.api.routes.audit_complex_router import (
        configure_audit_complex_store as configure_audit_complex,
    )
    from backend.audit.audit_complex_store import AuditComplexStore

    _audit_complex_path = Path(settings.audit_db_path)
    os.makedirs(_audit_complex_path.parent, exist_ok=True)
    _audit_complex_store = AuditComplexStore(_audit_complex_path)
    configure_audit_complex(_audit_complex_store)

    _live_ticker_hub = configure_live_ticker_hub(client=_bingx_client)
    await _live_ticker_hub.ensure_started()
    app.state.bingx_live_ticker_hub = _live_ticker_hub

    _market_breadth = MarketBreadthTracker()
    app.state.market_breadth = _market_breadth

    _scanner_scheduler = ScannerScheduler(
        hub=hub,
        api_keys=[settings.fmp_api_key.get_secret_value()],
        event_bus=event_bus,
        universe=settings.default_universe,
        breadth_tracker=_market_breadth,
        config=ScannerSchedulerConfig(
            scan_interval_s=settings.phase_a_scan_interval_s,
            respect_market_hours=True,
            publish_to_bus=True,
        ),
    )
    app.state.scanner_scheduler = _scanner_scheduler

    logger.info("Starting QuantitativeEngine background task...")
    engine_task = asyncio.create_task(engine.start_processing())
    logger.info("Starting AlpacaStreamer background task...")
    emitter_task = asyncio.create_task(emitter.start())
    logger.info("Starting Phase A Scanner scheduler...")
    await _scanner_scheduler.start()

    from backend.services.alpaca_universe_fetcher import ensure_alpaca_universe_loaded

    logger.info("Fetching Alpaca Broad Universe (autonomous)...")

    app.state.alpaca_universe_task = asyncio.create_task(
        ensure_alpaca_universe_loaded(
            settings.alpaca_api_key.get_secret_value(),
            settings.alpaca_api_secret.get_secret_value(),
        )
    )

    return engine_task, emitter_task, hub


async def shutdown_application(
    app: FastAPI, engine_task: asyncio.Task, emitter_task: asyncio.Task, hub: Any
):
    logger.info("Stopping system services...")
    from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB, PREDICTIONS_DB
    from backend.infrastructure.sqlite_health import wal_checkpoint_truncate

    engine_task.cancel()
    emitter_task.cancel()
    try:
        await asyncio.gather(engine_task, emitter_task, return_exceptions=True)
    except Exception as exc:
        logger.error(f"Error during task cancellation: {exc}", exc_info=True)

    await app.state.scanner_scheduler.stop()
    await app.state.bingx_live_ticker_hub.shutdown()
    equity_feed = getattr(app.state, "equity_l2_feed", None)
    if equity_feed is not None:
        await equity_feed.stop()
    equity_task = getattr(app.state, "equity_l2_feed_task", None)
    if equity_task is not None:
        equity_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await equity_task
    gex_capture = getattr(app.state, "options_gex_capture", None)
    if gex_capture is not None:
        await gex_capture.stop()
    await hub.close()

    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient

    await FMPClient.aclose_shared_client()
    await MassiveClient.aclose_shared_client()
    wal_checkpoint_truncate(OPTIONS_GEX_SNAPSHOTS_DB)
    wal_checkpoint_truncate(PREDICTIONS_DB)
    logger.info("System shutdown complete.")
