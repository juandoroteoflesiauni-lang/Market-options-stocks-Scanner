from __future__ import annotations
"""Intraday Funding Lab outcomes and metric-lake persistence.

This module is intentionally small and independent from the broader
``funding_lab_service`` orchestration. It provides the intraday horizon
mutation used by Funding Lab research: legacy 1d/3d/5d outcomes become
1h/4h/EOD outcomes inside the same RTH session.
"""


import math
import sqlite3
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

DEFAULT_METRIC_LAKE_DB = Path("backend/data/predictions.db")
DEFAULT_NY_TZ = "America/New_York"
EOD_EXECUTION_TIME = time(15, 45)

_OUTCOME_KEYS: tuple[str, ...] = (
    "outcome_return_1h",
    "outcome_return_4h",
    "outcome_return_eod",
    "execution_time_eod",
    "high_intraday",
    "low_intraday",
    "bars_held_1h_exit",
    "bars_held_4h_exit",
    "bars_held_eod_exit",
    "sharpe_intraday_1h",
    "sharpe_intraday_4h",
    "sharpe_intraday_eod",
    "profit_factor_eod",
    "max_drawdown_eod",
    "error",
)


def calculate_intraday_outcomes(
    symbol: str,
    prediction_timestamp: datetime,
    prediction_direction: str,
    entry_price: float,
    bars_same_day: pd.DataFrame,
    ny_tz_str: str = DEFAULT_NY_TZ,
) -> dict[str, object]:
    """Calculate pure intraday Funding Lab outcomes.

    INPUTS:
        symbol: Instrument identifier used only for logging/audit context.
        prediction_timestamp: Signal timestamp. Naive datetimes are treated as
            America/New_York; aware datetimes are converted to that timezone.
        prediction_direction: ``UP``, ``DOWN``/``SHORT`` or ``NEUTRAL``.
            ``DOWN``/``SHORT`` invert returns.
        entry_price: Positive entry/reference price at prediction time.
        bars_same_day: Pandas DataFrame with a ``timestamp`` column or
            DatetimeIndex plus at least ``high``, ``low`` and ``close`` columns.
        ny_tz_str: Timezone name, default ``America/New_York``.

    OUTPUTS:
        dict with all required ``outcome_return_*``, bars-held, intraday risk
        metrics and ``error`` fields. On validation/calculation failure, the
        same shape is returned with metrics set to ``None``/``0`` and ``error``
        containing a stable human-readable message.

    Mock data example for tests:
        ``pd.DataFrame([{"timestamp": dt, "open": 100, "high": 102,
        "low": 99, "close": 101, "volume": 1000}, ...])``
    """

    try:
        ny_tz = ZoneInfo(ny_tz_str)
        pred_time = _as_ny_datetime(prediction_timestamp, ny_tz)
        execution_time_eod = _eod_execution_datetime(pred_time, ny_tz)

        if not str(symbol).strip():
            return _error_payload("symbol is required", execution_time_eod=execution_time_eod)
        if not math.isfinite(float(entry_price)) or float(entry_price) <= 0.0:
            return _error_payload(
                "entry_price must be positive",
                execution_time_eod=execution_time_eod,
            )
        if pred_time > execution_time_eod:
            return _error_payload(
                "prediction_timestamp is after EOD execution window",
                execution_time_eod=execution_time_eod,
            )

        direction_mult = _direction_multiplier(prediction_direction)
        bars = _prepare_intraday_bars(bars_same_day, ny_tz)
        if bars.empty:
            return _error_payload(
                "bars_same_day has no valid OHLC rows", execution_time_eod=execution_time_eod
            )

        valid = bars[
            (bars["timestamp"] >= pred_time) & (bars["timestamp"] <= execution_time_eod)
        ].copy()
        valid = valid.sort_values("timestamp").reset_index(drop=True)
        if valid.empty:
            return _error_payload(
                "no bars between prediction_timestamp and EOD execution",
                execution_time_eod=execution_time_eod,
            )

        one_hour = valid[valid["timestamp"] <= pred_time + timedelta(hours=1)]
        four_hours = valid[valid["timestamp"] <= pred_time + timedelta(hours=4)]

        out = _empty_payload(execution_time_eod=execution_time_eod)
        out.update(
            {
                "outcome_return_1h": _window_best_directional_return(
                    one_hour,
                    float(entry_price),
                    direction_mult,
                ),
                "outcome_return_4h": _window_best_directional_return(
                    four_hours,
                    float(entry_price),
                    direction_mult,
                ),
                "outcome_return_eod": _window_close_directional_return(
                    valid,
                    float(entry_price),
                    direction_mult,
                ),
                "high_intraday": _float_or_none(valid["high"].max()),
                "low_intraday": _float_or_none(valid["low"].min()),
                "bars_held_1h_exit": int(len(one_hour)),
                "bars_held_4h_exit": int(len(four_hours)),
                "bars_held_eod_exit": int(len(valid)),
                "sharpe_intraday_1h": _intraday_sharpe(one_hour, direction_mult),
                "sharpe_intraday_4h": _intraday_sharpe(four_hours, direction_mult),
                "sharpe_intraday_eod": _intraday_sharpe(valid, direction_mult),
                "profit_factor_eod": _intraday_profit_factor(valid, direction_mult),
                "max_drawdown_eod": _intraday_max_drawdown(valid),
                "error": None,
            }
        )
        logger.info(
            "funding_lab.intraday_outcomes symbol=%s direction=%s pred_time=%s eod=%s",
            str(symbol).upper().strip(),
            str(prediction_direction).upper().strip(),
            pred_time.isoformat(),
            execution_time_eod.isoformat(),
        )
        return out
    except Exception as exc:
        logger.exception(
            "funding_lab.intraday_outcomes_failed symbol=%s error=%s",
            symbol,
            str(exc)[:240],
        )
        return _error_payload(str(exc), execution_time_eod=None)


def save_intraday_outcomes_to_lake(
    symbol: str,
    prediction_id: str | int,
    outcomes_dict: dict[str, object],
    *,
    db_path: Path | str = DEFAULT_METRIC_LAKE_DB,
) -> bool:
    """Persist intraday outcome metrics into the metric lake.

    INPUTS:
        symbol: Canonical or provider symbol for audit lookup.
        prediction_id: Stable prediction identifier; acts as the table primary key.
        outcomes_dict: Result from ``calculate_intraday_outcomes``.
        db_path: SQLite metric-lake database path. Defaults to
            ``backend/data/predictions.db``.

    OUTPUTS:
        ``True`` when the row was inserted/updated, ``False`` when validation or
        persistence fails. The function logs failures and does not raise.
    """

    try:
        sym = str(symbol).upper().strip()
        pred_id = str(prediction_id).strip()
        if not sym or not pred_id:
            logger.warning("funding_lab.metric_lake_rejected missing symbol or prediction_id")
            return False

        db = Path(db_path)
        db.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=UTC).isoformat()
        execution_time_eod = _datetime_to_iso(outcomes_dict.get("execution_time_eod"))

        with sqlite3.connect(db) as con:
            _init_intraday_outcomes_table(con)
            con.execute(
                """
                INSERT INTO intraday_outcomes (
                    prediction_id, symbol,
                    outcome_return_1h, outcome_return_4h, outcome_return_eod,
                    execution_time_eod, high_intraday, low_intraday,
                    bars_held_1h_exit, bars_held_4h_exit, bars_held_eod_exit,
                    sharpe_intraday_1h, sharpe_intraday_4h, sharpe_intraday_eod,
                    profit_factor_eod, max_drawdown_eod, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    outcome_return_1h = excluded.outcome_return_1h,
                    outcome_return_4h = excluded.outcome_return_4h,
                    outcome_return_eod = excluded.outcome_return_eod,
                    execution_time_eod = excluded.execution_time_eod,
                    high_intraday = excluded.high_intraday,
                    low_intraday = excluded.low_intraday,
                    bars_held_1h_exit = excluded.bars_held_1h_exit,
                    bars_held_4h_exit = excluded.bars_held_4h_exit,
                    bars_held_eod_exit = excluded.bars_held_eod_exit,
                    sharpe_intraday_1h = excluded.sharpe_intraday_1h,
                    sharpe_intraday_4h = excluded.sharpe_intraday_4h,
                    sharpe_intraday_eod = excluded.sharpe_intraday_eod,
                    profit_factor_eod = excluded.profit_factor_eod,
                    max_drawdown_eod = excluded.max_drawdown_eod,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    pred_id,
                    sym,
                    _float_or_none(outcomes_dict.get("outcome_return_1h")),
                    _float_or_none(outcomes_dict.get("outcome_return_4h")),
                    _float_or_none(outcomes_dict.get("outcome_return_eod")),
                    execution_time_eod,
                    _float_or_none(outcomes_dict.get("high_intraday")),
                    _float_or_none(outcomes_dict.get("low_intraday")),
                    _int_or_zero(outcomes_dict.get("bars_held_1h_exit")),
                    _int_or_zero(outcomes_dict.get("bars_held_4h_exit")),
                    _int_or_zero(outcomes_dict.get("bars_held_eod_exit")),
                    _float_or_none(outcomes_dict.get("sharpe_intraday_1h")),
                    _float_or_none(outcomes_dict.get("sharpe_intraday_4h")),
                    _float_or_none(outcomes_dict.get("sharpe_intraday_eod")),
                    _float_or_none(outcomes_dict.get("profit_factor_eod")),
                    _float_or_none(outcomes_dict.get("max_drawdown_eod")),
                    _str_or_none(outcomes_dict.get("error")),
                    now,
                    now,
                ),
            )
        logger.info("funding_lab.metric_lake_saved symbol=%s prediction_id=%s", sym, pred_id)
        return True
    except Exception as exc:
        logger.exception(
            "funding_lab.metric_lake_save_failed symbol=%s prediction_id=%s error=%s",
            symbol,
            prediction_id,
            str(exc)[:240],
        )
        return False


def _init_intraday_outcomes_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS intraday_outcomes (
            prediction_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            outcome_return_1h REAL,
            outcome_return_4h REAL,
            outcome_return_eod REAL,
            execution_time_eod TEXT,
            high_intraday REAL,
            low_intraday REAL,
            bars_held_1h_exit INTEGER NOT NULL DEFAULT 0,
            bars_held_4h_exit INTEGER NOT NULL DEFAULT 0,
            bars_held_eod_exit INTEGER NOT NULL DEFAULT 0,
            sharpe_intraday_1h REAL,
            sharpe_intraday_4h REAL,
            sharpe_intraday_eod REAL,
            profit_factor_eod REAL,
            max_drawdown_eod REAL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_intraday_outcomes_symbol
            ON intraday_outcomes(symbol, prediction_id)
        """
    )


def _empty_payload(*, execution_time_eod: datetime | None) -> dict[str, object]:
    payload: dict[str, object] = {key: None for key in _OUTCOME_KEYS}
    payload["execution_time_eod"] = execution_time_eod
    payload["bars_held_1h_exit"] = 0
    payload["bars_held_4h_exit"] = 0
    payload["bars_held_eod_exit"] = 0
    return payload


def _error_payload(message: str, *, execution_time_eod: datetime | None) -> dict[str, object]:
    payload = _empty_payload(execution_time_eod=execution_time_eod)
    payload["error"] = message
    return payload


def _as_ny_datetime(value: datetime, ny_tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ny_tz)
    return value.astimezone(ny_tz)


def _eod_execution_datetime(prediction_timestamp: datetime, ny_tz: ZoneInfo) -> datetime:
    return datetime.combine(prediction_timestamp.date(), EOD_EXECUTION_TIME, tzinfo=ny_tz)


def _direction_multiplier(prediction_direction: str) -> float:
    direction = str(prediction_direction).upper().strip()
    if direction in {"UP", "LONG", "BULLISH"}:
        return 1.0
    if direction in {"DOWN", "SHORT", "BEARISH"}:
        return -1.0
    if direction == "NEUTRAL":
        return 0.0
    raise ValueError("prediction_direction must be UP, DOWN, SHORT, LONG, or NEUTRAL")


def _prepare_intraday_bars(bars_same_day: pd.DataFrame, ny_tz: ZoneInfo) -> pd.DataFrame:
    if not isinstance(bars_same_day, pd.DataFrame):
        raise TypeError("bars_same_day must be a pandas DataFrame")
    if bars_same_day.empty:
        return pd.DataFrame(columns=["timestamp", "high", "low", "close"])

    bars = bars_same_day.copy()
    if "timestamp" not in bars.columns:
        if isinstance(bars.index, pd.DatetimeIndex):
            bars = bars.reset_index().rename(columns={bars.index.name or "index": "timestamp"})
        else:
            raise ValueError("bars_same_day requires a timestamp column or DatetimeIndex")

    missing = {"high", "low", "close"} - set(bars.columns)
    if missing:
        raise ValueError(f"bars_same_day missing OHLC columns: {','.join(sorted(missing))}")

    timestamps = pd.to_datetime(bars["timestamp"], errors="coerce")
    if getattr(timestamps.dt, "tz", None) is None:
        bars["timestamp"] = timestamps.dt.tz_localize(ny_tz)
    else:
        bars["timestamp"] = timestamps.dt.tz_convert(ny_tz)

    for column in ("high", "low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")

    bars = bars.dropna(subset=["timestamp", "high", "low", "close"])
    finite = (
        bars["high"].map(math.isfinite)
        & bars["low"].map(math.isfinite)
        & bars["close"].map(math.isfinite)
    )
    return bars.loc[finite, ["timestamp", "high", "low", "close"]].copy()


def _window_best_directional_return(
    bars: pd.DataFrame,
    entry_price: float,
    direction_mult: float,
) -> float | None:
    if bars.empty:
        return None
    if direction_mult == 0.0:
        return 0.0
    exit_price = float(bars["low"].min()) if direction_mult < 0 else float(bars["high"].max())
    return _clean_float(direction_mult * (exit_price - entry_price) / entry_price)


def _window_close_directional_return(
    bars: pd.DataFrame,
    entry_price: float,
    direction_mult: float,
) -> float | None:
    if bars.empty:
        return None
    if direction_mult == 0.0:
        return 0.0
    exit_price = float(bars["close"].iloc[-1])
    return _clean_float(direction_mult * (exit_price - entry_price) / entry_price)


def _intraday_close_returns(bars: pd.DataFrame, direction_mult: float) -> pd.Series:
    if len(bars) < 2:
        return pd.Series(dtype="float64")
    returns = bars["close"].astype(float).pct_change().dropna()
    if direction_mult < 0:
        returns = -returns
    elif direction_mult == 0.0:
        returns = returns * 0.0
    return returns


def _intraday_sharpe(bars: pd.DataFrame, direction_mult: float) -> float | None:
    returns = _intraday_close_returns(bars, direction_mult)
    if len(returns) < 2:
        return None
    std = float(returns.std())
    if not math.isfinite(std) or std <= 0.0:
        return 0.0
    return _clean_float(float(returns.mean()) / std)


def _intraday_profit_factor(bars: pd.DataFrame, direction_mult: float) -> float | None:
    returns = _intraday_close_returns(bars, direction_mult)
    if returns.empty:
        return None
    wins = float(returns[returns > 0.0].sum())
    losses = abs(float(returns[returns < 0.0].sum()))
    if losses <= 0.0:
        return None
    return _clean_float(wins / losses)


def _intraday_max_drawdown(bars: pd.DataFrame) -> float | None:
    if bars.empty:
        return None
    closes = bars["close"].astype(float)
    peaks = closes.cummax()
    drawdowns = (closes - peaks) / peaks
    return _clean_float(float(drawdowns.min()))


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean_float(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _int_or_zero(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value)) if math.isfinite(value) else 0
    if not isinstance(value, str):
        return 0
    try:
        number = int(value)
    except ValueError:
        return 0
    return max(0, number)


def _datetime_to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
