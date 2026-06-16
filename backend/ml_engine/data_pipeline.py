"""Data Pipeline para Machine Learning. # [TH][IM]

Extrae y cruza los snapshots de decisión (Audit Process Snapshots) con
los resultados finales de PnL (Audit Trade Results) desde DuckDB.
"""

import json
from typing import Any

import pandas as pd

from backend.audit.structured_logger import _get_store


def build_training_dataset() -> pd.DataFrame:
    """Extrae snapshots y los cruza con PnL final usando merge_asof_backward."""
    store = _get_store()

    with store._connect() as con:
        # Fetch snapshots
        snapshots_raw = con.execute(
            """
            SELECT timestamp, module, symbol, indicators
            FROM audit_process_snapshots
            """
        ).fetchall()

        # Fetch trade results (PnL final)
        trades_raw = con.execute(
            """
            SELECT timestamp, module, symbol, pnl_pct, pnl_usd, exit_reason
            FROM audit_trade_results
            """
        ).fetchall()

    if not snapshots_raw or not trades_raw:
        return pd.DataFrame()

    df_snapshots = pd.DataFrame(
        snapshots_raw, columns=["snap_time", "module", "symbol", "indicators"]
    )
    df_trades = pd.DataFrame(
        trades_raw, columns=["trade_time", "module", "symbol", "pnl_pct", "pnl_usd", "exit_reason"]
    )

    df_snapshots["snap_time"] = pd.to_datetime(df_snapshots["snap_time"])
    df_trades["trade_time"] = pd.to_datetime(df_trades["trade_time"])

    df_snapshots = df_snapshots.sort_values("snap_time")
    df_trades = df_trades.sort_values("trade_time")

    # Merge asof to get the nearest snapshot BEFORE the trade execution
    merged = pd.merge_asof(
        df_trades,
        df_snapshots,
        left_on="trade_time",
        right_on="snap_time",
        by=["symbol", "module"],
        direction="backward",
    )

    # Filtrar operaciones que no pudieron emparejarse con un snapshot técnico anterior
    merged = merged.dropna(subset=["indicators"])

    features_list = []
    for _, row in merged.iterrows():
        try:
            indicators = json.loads(row["indicators"]) if row["indicators"] else {}
        except Exception:
            indicators = {}

        features: dict[str, Any] = {}
        features["symbol"] = row["symbol"]
        features["module"] = row["module"]
        features["pnl_pct"] = row["pnl_pct"]
        features["pnl_usd"] = row["pnl_usd"]
        features["exit_reason"] = row["exit_reason"]
        features["trade_time"] = row["trade_time"]
        features["snap_time"] = row["snap_time"]

        # Win rate target classification: 1 for profit, 0 for loss/scratch
        features["target_win"] = 1 if row["pnl_pct"] > 0.1 else 0

        _flatten_dict(indicators, features, prefix="ind_")

        features_list.append(features)

    return pd.DataFrame(features_list)


def _flatten_dict(d: dict[str, Any], out: dict[str, Any], prefix: str = "") -> None:
    for k, v in d.items():
        if isinstance(v, dict):
            _flatten_dict(v, out, f"{prefix}{k}_")
        elif isinstance(v, int | float):
            out[f"{prefix}{k}"] = float(v)
        elif isinstance(v, bool):
            out[f"{prefix}{k}"] = 1.0 if v else 0.0
