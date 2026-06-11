"""Insider and institutional flow calculators."""

from __future__ import annotations

from .flow_models import (
    InsiderFlowProfile,
    InstitutionalFlowProfile,
    InstitutionalHolder,
    RawTransaction,
)

REALTIME_API_ENV_KEYS: tuple[str, ...] = (
    "FMP_KEY_13F",
    "FMP_KEY_FILINGS",
    "SEC_API_KEY",
    "MASSIVE_KEY_WS_TRADES",
)


_THRESH_ACCUMULATION = 0.20
_THRESH_DISTRIBUTION = -0.20


class InsiderFlowCalculator:
    """Pure SEC Form-4 flow aggregator."""

    @staticmethod
    def _classify(score: float) -> str:
        if score > _THRESH_ACCUMULATION:
            return "ACUMULACIÓN INSIDER"
        if score < _THRESH_DISTRIBUTION:
            return "DISTRIBUCIÓN INSIDER"
        return "NEUTRAL"

    @staticmethod
    def calculate(transactions: list[RawTransaction]) -> InsiderFlowProfile:
        if not transactions:
            return InsiderFlowProfile(ok=True, score=0.0, bias="NEUTRAL")

        buy_shares = 0.0
        sell_shares = 0.0
        buy_value = 0.0
        sell_value = 0.0
        buy_count = 0
        sell_count = 0
        insiders_buy: set[str] = set()
        insiders_sell: set[str] = set()
        dates: list[str] = []

        for transaction in transactions:
            value = transaction.shares * transaction.transaction_price
            if transaction.transaction_date:
                dates.append(transaction.transaction_date)

            if transaction.transaction_type == "Purchase":
                buy_shares += transaction.shares
                buy_value += value
                buy_count += 1
                if transaction.insider_name:
                    insiders_buy.add(transaction.insider_name)
            elif transaction.transaction_type == "Sale":
                sell_shares += transaction.shares
                sell_value += value
                sell_count += 1
                if transaction.insider_name:
                    insiders_sell.add(transaction.insider_name)

        total_shares = buy_shares + sell_shares
        net_shares = buy_shares - sell_shares
        net_value = buy_value - sell_value
        score = round(net_shares / total_shares, 4) if total_shares > 0 else 0.0

        return InsiderFlowProfile(
            ok=True,
            score=score,
            bias=InsiderFlowCalculator._classify(score),
            buy_shares=int(buy_shares),
            sell_shares=int(sell_shares),
            net_shares=int(net_shares),
            buy_transactions=buy_count,
            sell_transactions=sell_count,
            total_transactions=buy_count + sell_count,
            buy_value_usd=round(buy_value, 2),
            sell_value_usd=round(sell_value, 2),
            net_value_usd=round(net_value, 2),
            insiders_buying=tuple(sorted(insiders_buy)),
            insiders_selling=tuple(sorted(insiders_sell)),
            latest_transaction_date=max(dates) if dates else None,
        )

    @staticmethod
    def from_dicts(raw: list[dict]) -> InsiderFlowProfile:
        transactions: list[RawTransaction] = []
        for record in raw:
            if not isinstance(record, dict):
                continue
            try:
                transactions.append(
                    RawTransaction(
                        insider_name=str(record.get("insider_name", "") or ""),
                        transaction_type=str(record.get("transaction_type", "")),
                        shares=float(record.get("shares") or 0),
                        transaction_price=float(record.get("transaction_price") or 0),
                        transaction_date=record.get("transaction_date"),
                    )
                )
            except Exception:
                continue
        return InsiderFlowCalculator.calculate(transactions)


class InstitutionalFlowCalculator:
    """Pure 13F ownership profile aggregator."""

    _MAX_TOP_HOLDERS = 10

    @staticmethod
    def calculate(
        top_holders: list[InstitutionalHolder],
        inst_pct_float: float | None = None,
        inst_pct_shares: float | None = None,
        insider_pct: float | None = None,
        total_institutions: int | None = None,
    ) -> InstitutionalFlowProfile:
        holders = top_holders[: InstitutionalFlowCalculator._MAX_TOP_HOLDERS]
        inst_pct = inst_pct_float if inst_pct_float is not None else inst_pct_shares

        if inst_pct is None and holders:
            estimated = sum(holder.pct_held for holder in holders if holder.pct_held is not None)
            if estimated > 0:
                inst_pct = estimated

        has_data = inst_pct is not None or insider_pct is not None or bool(holders)
        if not has_data:
            return InstitutionalFlowProfile(
                ok=False,
                error=(
                    "No 13F data available — asset may be non-US, an ETF, or"
                    " without institutional coverage."
                ),
            )

        return InstitutionalFlowProfile(
            ok=True,
            inst_ownership_pct=inst_pct,
            insider_pct=insider_pct,
            top_holders=tuple(holders),
            total_institutions=total_institutions,
        )

    @staticmethod
    def from_dicts(
        holder_dicts: list[dict],
        inst_pct_float: float | None = None,
        inst_pct_shares: float | None = None,
        insider_pct: float | None = None,
        total_institutions: int | None = None,
    ) -> InstitutionalFlowProfile:
        def _norm_pct(value: float | None) -> float | None:
            if value is None:
                return None
            return value / 100.0 if value > 1.0 else value

        def _safe_float(value: object) -> float | None:
            if value is None:
                return None
            try:
                parsed = float(value)
                return None if parsed != parsed else parsed
            except (TypeError, ValueError):
                return None

        name_keys = ["holder", "institution", "name", "fund name"]
        share_keys = ["shares", "shares held", "sharesheld", "position"]
        pct_keys = ["% out", "pctout", "pctheld", "% held", "pct held"]
        value_keys = ["value", "market value", "value (usd)"]
        date_keys = ["date reported", "datereported", "report date"]

        def _first(row: dict[str, object], keys: list[str]) -> object:
            for key in keys:
                if key in row and row[key] is not None:
                    return row[key]
            return None

        holders: list[InstitutionalHolder] = []
        for raw in holder_dicts:
            if not isinstance(raw, dict):
                continue
            row = {str(key).lower().strip(): value for key, value in raw.items()}
            name = _first(row, name_keys)
            if not name:
                continue
            try:
                holders.append(
                    InstitutionalHolder(
                        holder=str(name).strip(),
                        shares=int(float(_first(row, share_keys) or 0)) or None,
                        pct_held=_norm_pct(_safe_float(_first(row, pct_keys))),
                        value_usd=_safe_float(_first(row, value_keys)),
                        date_reported=(
                            str(_first(row, date_keys))[:10]
                            if _first(row, date_keys) is not None
                            else None
                        ),
                    )
                )
            except Exception:
                continue

        return InstitutionalFlowCalculator.calculate(
            top_holders=holders,
            inst_pct_float=inst_pct_float,
            inst_pct_shares=inst_pct_shares,
            insider_pct=insider_pct,
            total_institutions=total_institutions,
        )


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: smart_money.py
# Eliminado: encabezado con referencia a sistema anterior
# Preservado: agregación de flujos insider/institucionales y umbrales de sesgo
# Pendientes: ninguno
# ─────────────────────────────────────────────────
