"""Adaptador de datos de opciones para motores de src/quant_engine.

Convierte OptionChainSnapshot y OptionContract al formato numpy 2D
que esperan los motores matemáticos existentes.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from backend.models.option_contract import OptionChainSnapshot

FloatArray = npt.NDArray[np.float64]


class OptionsDataAdapter:
    """Convierte modelos del dominio a arrays numpy para motores."""

    @staticmethod
    def to_chain_data_gex(chain: OptionChainSnapshot) -> FloatArray:
        """Formato para GammaFlipEngine: [strike, is_call, open_interest]."""
        rows = []
        for c in chain.contracts:
            rows.append(
                [
                    float(c.strike),
                    1.0 if c.is_call else 0.0,
                    float(c.open_interest),
                ]
            )
        return np.array(rows, dtype=np.float64) if rows else np.empty((0, 3), dtype=np.float64)

    @staticmethod
    def to_chain_data_dex(chain: OptionChainSnapshot) -> FloatArray:
        """Formato para DeltaExposureEngine: [strike, is_call, delta, open_interest]."""
        rows = []
        for c in chain.contracts:
            rows.append(
                [
                    float(c.strike),
                    1.0 if c.is_call else 0.0,
                    float(c.delta),
                    float(c.open_interest),
                ]
            )
        return np.array(rows, dtype=np.float64) if rows else np.empty((0, 4), dtype=np.float64)

    @staticmethod
    def to_chain_data_zero_day(chain: OptionChainSnapshot) -> FloatArray:
        """Formato para ZeroDayEngine: [strike, is_call, bid, ask, last, vol, oi, delta, gamma, iv]."""  # noqa: E501
        rows = []
        for c in chain.contracts:
            rows.append(
                [
                    float(c.strike),
                    1.0 if c.is_call else 0.0,
                    float(c.bid),
                    float(c.ask),
                    float(c.last_price),
                    float(c.volume),
                    float(c.open_interest),
                    float(c.delta),
                    float(c.gamma),
                    float(c.implied_volatility),
                ]
            )
        return np.array(rows, dtype=np.float64) if rows else np.empty((0, 10), dtype=np.float64)

    @staticmethod
    def to_chain_data_shadow_delta(chain: OptionChainSnapshot) -> FloatArray:
        """Formato para ShadowDeltaEngine: [strike, is_call, iv, quantity]."""
        rows = []
        for c in chain.contracts:
            rows.append(
                [
                    float(c.strike),
                    1.0 if c.is_call else 0.0,
                    float(c.implied_volatility),
                    float(c.volume),
                ]
            )
        return np.array(rows, dtype=np.float64) if rows else np.empty((0, 4), dtype=np.float64)

    @staticmethod
    def to_chain_data_delta_flow(chain: OptionChainSnapshot) -> FloatArray:
        """Formato para DeltaWeightedFlow_Engine: [is_call, volume, mark_price, delta]."""
        rows = []
        for c in chain.contracts:
            mark = float(c.mid_price) if c.mid_price > 0 else float(c.last_price)
            rows.append(
                [
                    1.0 if c.is_call else 0.0,
                    float(c.volume),
                    mark,
                    float(c.delta),
                ]
            )
        return np.array(rows, dtype=np.float64) if rows else np.empty((0, 4), dtype=np.float64)

    @staticmethod
    def to_flow_rows(chain: OptionChainSnapshot) -> list[dict[str, object]]:
        """Formato para OptionsFlowSignalEngine: lista de dicts."""
        rows: list[dict[str, object]] = []
        for c in chain.contracts:
            rows.append(
                {
                    "symbol": c.contract_symbol,
                    "underlying": c.underlying_ticker,
                    "expiry": c.expiry.isoformat(),
                    "strike": float(c.strike),
                    "right": "call" if c.is_call else "put",
                    "volume": float(c.volume),
                    "open_interest": float(c.open_interest),
                    "mark": float(c.mid_price) if c.mid_price > 0 else float(c.last_price),
                    "spot": float(chain.spot_price),
                    "dte": c.dte,
                }
            )
        return rows

    @staticmethod
    def to_options_engine_arrays(
        chain: OptionChainSnapshot,
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
        """Formato para OptionsEngine.analyze_chain():
        Returns: (strikes, call_oi, put_oi, call_iv, put_iv)
        Agrupados por strike único.
        """
        calls = [c for c in chain.contracts if c.is_call]
        puts = [c for c in chain.contracts if c.is_put]

        call_strikes = {float(c.strike): c for c in calls}
        put_strikes = {float(c.strike): c for c in puts}

        all_strikes = sorted(set(call_strikes.keys()) | set(put_strikes.keys()))

        strikes_arr = np.array(all_strikes, dtype=np.float64)
        call_oi = np.array(
            [call_strikes[s].open_interest if s in call_strikes else 0.0 for s in all_strikes],
            dtype=np.float64,
        )
        put_oi = np.array(
            [put_strikes[s].open_interest if s in put_strikes else 0.0 for s in all_strikes],
            dtype=np.float64,
        )
        call_iv = np.array(
            [
                call_strikes[s].implied_volatility if s in call_strikes else 0.20
                for s in all_strikes
            ],
            dtype=np.float64,
        )
        put_iv = np.array(
            [put_strikes[s].implied_volatility if s in put_strikes else 0.20 for s in all_strikes],
            dtype=np.float64,
        )

        return strikes_arr, call_oi, put_oi, call_iv, put_iv

    @staticmethod
    def compute_tte(chain: OptionChainSnapshot) -> float:
        """Calcula TTE promedio en años desde los contratos."""
        if not chain.contracts:
            return 30.0 / 365.0
        avg_dte = sum(c.dte for c in chain.contracts) / len(chain.contracts)
        return max(avg_dte / 365.0, 1.0 / 365.0)

    @staticmethod
    def compute_atm_iv(chain: OptionChainSnapshot) -> float:
        """Estima ATM IV de la cadena."""
        if not chain.contracts:
            return 0.20
        spot = float(chain.spot_price)
        closest = min(chain.contracts, key=lambda c: abs(float(c.strike) - spot))
        return closest.implied_volatility
