"""Map BingX options metrics / chain snapshots → hybrid motor inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from backend.quant_engine.engines.hybrid.divergences_hybrid import TickInput
from backend.quant_engine.engines.hybrid.elliott_wave_hybrid import GEXBar
from backend.quant_engine.engines.hybrid.exhaustion_hybrid import CharmSnapshot
from backend.quant_engine.engines.hybrid.shadow_macd_hybrid import OptionsChain, OptionStrike
from backend.quant_engine.engines.hybrid.vsa_hybrid import VannaSnapshot
from backend.quant_engine.engines.hybrid.wavetrend_hybrid import GEXSnapshot


def _ts_now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(UTC))


def _gamma_regime(net_gex: float, zero_gamma: float | None, spot: float | None) -> str:
    if zero_gamma is not None and spot is not None and spot > 0:
        dist = abs(spot - zero_gamma) / spot
        if dist <= 0.05:
            return "GAMMA_FLIP"
    if net_gex > 0:
        return "GAMMA_POS"
    if net_gex < 0:
        return "GAMMA_NEG"
    return "NEUTRAL"


def chain_rows_from_snapshot(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    chain = raw.get("chain")
    return list(chain) if isinstance(chain, list) else []


def build_option_strikes(chain_rows: list[dict[str, Any]], spot: float) -> list[OptionStrike]:
    strikes: list[OptionStrike] = []
    for row in chain_rows:
        strike = row.get("strike")
        if strike is None:
            continue
        strikes.append(
            OptionStrike(
                strike=float(strike),
                call_delta=float(row.get("call_delta") or row.get("delta_call") or 0.5),
                put_delta=float(row.get("put_delta") or row.get("delta_put") or -0.5),
                call_oi=int(row.get("call_oi") or row.get("open_interest_call") or 0),
                put_oi=int(row.get("put_oi") or row.get("open_interest_put") or 0),
                call_gamma=float(row.get("call_gamma") or row.get("gamma_call") or 0.0),
                put_gamma=float(row.get("put_gamma") or row.get("gamma_put") or 0.0),
            )
        )
    if not strikes and spot > 0:
        strikes.append(
            OptionStrike(
                strike=spot,
                call_delta=0.5,
                put_delta=-0.5,
                call_oi=1,
                put_oi=1,
                call_gamma=0.01,
                put_gamma=0.01,
            )
        )
    return strikes


def build_gex_snapshot(
    *,
    ticker: str,
    metrics: dict[str, Any],
    timestamp: pd.Timestamp | None = None,
) -> GEXSnapshot:
    ts = timestamp or _ts_now()
    spot = float(metrics.get("spot") or 0.0)
    return GEXSnapshot(
        timestamp=ts,
        ticker=ticker,
        net_gex=float(metrics.get("net_gex_total") or 0.0),
        gex_calls=float(metrics.get("call_gex_total") or 0.0),
        gex_puts=float(metrics.get("put_gex_total") or 0.0),
        gamma_flip=float(metrics.get("zero_gamma") or spot),
        iv_atm=float(metrics.get("atm_iv") or 0.0),
        spot=spot,
    )


def build_options_chain(
    *,
    ticker: str,
    metrics: dict[str, Any],
    chain_rows: list[dict[str, Any]],
    timestamp: pd.Timestamp | None = None,
) -> OptionsChain:
    ts = timestamp or _ts_now()
    spot = float(metrics.get("spot") or 0.0)
    return OptionsChain(
        timestamp=ts,
        ticker=ticker,
        spot=spot,
        strikes=build_option_strikes(chain_rows, spot),
    )


def build_vanna_snapshot(
    *,
    ticker: str,
    metrics: dict[str, Any],
    timestamp: pd.Timestamp | None = None,
) -> VannaSnapshot:
    ts = timestamp or _ts_now()
    spot = float(metrics.get("spot") or 0.0)
    vanna_net = float(metrics.get("total_vanna") or 0.0)
    iv = float(metrics.get("atm_iv") or 0.0)
    return VannaSnapshot(
        timestamp=ts,
        ticker=ticker,
        vanna_net=vanna_net,
        vanna_calls=vanna_net * 0.55,
        vanna_puts=vanna_net * 0.45,
        vanna_atm=vanna_net * 0.35,
        iv_atm=iv,
        iv_change_1m=float(metrics.get("iv_change_1m") or 0.0),
        net_gex=float(metrics.get("net_gex_total") or 0.0),
        spot=spot,
    )


def build_charm_snapshot(
    *,
    ticker: str,
    metrics: dict[str, Any],
    timestamp: pd.Timestamp | None = None,
) -> CharmSnapshot:
    ts = timestamp or _ts_now()
    charm_raw = metrics.get("charm_flow")
    charm_net = float(charm_raw) if isinstance(charm_raw, int | float) else 0.0
    return CharmSnapshot(
        timestamp=ts,
        ticker=ticker,
        charm_net=charm_net,
        charm_calls=charm_net * 0.6,
        charm_puts=charm_net * 0.4,
        charm_atm=charm_net * 0.3,
        theta_net=-abs(charm_net) * 0.1,
        iv_atm=float(metrics.get("atm_iv") or 0.0),
        net_gex=float(metrics.get("net_gex_total") or 0.0),
        days_to_expiry=7.0,
    )


def build_gex_bar(
    *,
    metrics: dict[str, Any],
    timestamp: pd.Timestamp,
) -> GEXBar:
    spot = float(metrics.get("spot") or 0.0)
    return GEXBar(
        timestamp=timestamp,
        net_gex=float(metrics.get("net_gex_total") or 0.0),
        gamma_flip=float(metrics.get("zero_gamma") or spot),
        gex_calls=float(metrics.get("call_gex_total") or 0.0),
        gex_puts=float(metrics.get("put_gex_total") or 0.0),
        iv_atm=float(metrics.get("atm_iv") or 0.0),
        spot=spot,
    )


def build_tick_input(
    *,
    ticker: str,
    candle: dict[str, Any],
    timestamp: pd.Timestamp,
    flow: dict[str, float],
    metrics: dict[str, Any],
) -> TickInput:
    spot = float(metrics.get("spot") or candle.get("close") or 0.0)
    net_gex = float(metrics.get("net_gex_total") or 0.0)
    return TickInput(
        timestamp=timestamp,
        ticker=ticker,
        close=float(candle.get("close") or candle.get("c") or 0.0),
        high=float(candle.get("high") or candle.get("h") or 0.0),
        low=float(candle.get("low") or candle.get("l") or 0.0),
        delta_rsi=float(flow.get("delta_rsi") or 50.0),
        rsi_flow=float(flow.get("rsi_flow") or 50.0),
        hist_flow=float(flow.get("hist_flow") or 0.0),
        ndde=float(flow.get("ndde") or metrics.get("ndde") or 0.0),
        ndde_smooth=float(flow.get("ndde_smooth") or metrics.get("ndde") or 0.0),
        macd_ndde=float(flow.get("macd_ndde") or 0.0),
        net_gex=net_gex,
        net_premium=float(metrics.get("net_premium") or 0.0),
        iv_atm=float(metrics.get("atm_iv") or 0.0),
        regime=_gamma_regime(net_gex, metrics.get("zero_gamma"), spot),
        sweep_count=int(metrics.get("sweep_count") or 0),
    )
