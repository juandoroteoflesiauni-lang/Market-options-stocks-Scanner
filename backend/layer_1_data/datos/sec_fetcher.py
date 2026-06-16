from __future__ import annotations
from typing import Any
"""Fetcher de SEC API (sec-api.io) para filings e insider activity."""


import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("backend.layer_1_data.datos.sec_fetcher")

_SEC_API_BASE = "https://api.sec-api.io"

# ── requests lazy import ──────────────────────────────────────────────────────
try:
    import requests as _requests

    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests = None  # type: ignore[assignment]
    _REQUESTS_AVAILABLE = False
    logger.warning("requests not installed — SECFetcher disabled.")

# ── Insider transaction type classification ───────────────────────────────────
_BUY_CODES = {"P", "A"}  # P = Open market purchase, A = Award/grant
_SELL_CODES = {"S", "D"}  # S = Open market sale,    D = Disposition


class SECFetcher:
    """
    SEC API (sec-api.io) client for filing and insider transaction data.

    Instantiate once and reuse. All methods are stateless and fault-tolerant.

    Parameters
    ----------
    api_key : sec-api.io API key (passed as `apikey` query param).
    """

    def __init__(self, api_key: str) -> None:
        self._key = api_key.strip()
        self._session: object | None = None

        if _REQUESTS_AVAILABLE and self._key:
            try:
                sess = _requests.Session()

                sess.headers.update({"Content-Type": "application/json"})
                self._session = sess
                logger.debug("SECFetcher: session initialised (key=%.8s…)", self._key)
            except Exception as exc:
                logger.warning("SECFetcher: session init failed: %s", exc)
        elif not _REQUESTS_AVAILABLE:
            pass
        else:
            logger.warning("SECFetcher: empty API key — fetcher inactive.")

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def get_insider_summary(
        self,
        ticker: str,
        lookback_days: int = 90,
    ) -> dict[str, Any]:
        """
        Aggregate insider activity signal from Form 4 filings for the last N days.

        Returns a dict with keys:
          sec_insider_net           : int   — net buy/sell count (0 if undetermined)
          sec_insider_buys          : int   — buy transactions (if available)
          sec_insider_sells         : int   — sell transactions (if available)
          sec_insider_value_net     : float — net USD value (0.0 if unavailable)
          sec_insider_signal        : str   — "BULLISH" | "BEARISH" | "NEUTRAL" | "ACTIVE"
          sec_recent_filings_count  : int   — total Form 4 filings in window

        Note: On the free API tier, transaction-level data (buy/sell codes, shares,
        prices) may not be available. In that case, filing count is used as a proxy
        for insider activity level. ACTIVE = elevated filing count (> 10 in window).

        Returns empty dict (safe default) on failure.
        """
        if not self._is_ready():
            return {}

        try:
            transactions = self._fetch_form4(ticker, lookback_days=lookback_days)
            return self._aggregate_insider(ticker, transactions)
        except Exception as exc:
            logger.error("SECFetcher.get_insider_summary(%s): %s", ticker, exc)
            return {}

    def get_insider_transactions(
        self,
        ticker: str,
        lookback_days: int = 90,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return raw Form 4 insider transactions (up to `limit` records).

        Each record contains: ticker, name, role, transaction_type,
        shares, price_per_share, total_value, filed_at.
        Returns empty list on failure.
        """
        if not self._is_ready():
            return []

        try:
            return self._fetch_form4(ticker, lookback_days=lookback_days, limit=limit)
        except Exception as exc:
            logger.error("SECFetcher.get_insider_transactions(%s): %s", ticker, exc)
            return []

    def get_recent_filings(
        self,
        ticker: str,
        form_type: str = "10-K",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Return recent SEC filings for a ticker (by form type).

        Useful for locating recent 10-K/10-Q/8-K filings.
        Each record contains: form_type, filed_at, period_of_report, url.
        Returns empty list on failure.
        """
        if not self._is_ready():
            return []

        try:
            return self._search_filings(ticker, form_type, limit)
        except Exception as exc:
            logger.error("SECFetcher.get_recent_filings(%s, %s): %s", ticker, form_type, exc)
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _is_ready(self) -> bool:
        return _REQUESTS_AVAILABLE and self._session is not None and bool(self._key)

    def _post(self, endpoint: str, payload: dict) -> dict | None:
        """POST to sec-api.io query endpoint; return parsed JSON or None."""
        url = f"{_SEC_API_BASE}{endpoint}"
        params = {"token": self._key}
        try:
            resp = self._session.post(url, json=payload, params=params, timeout=15)

            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "SEC API POST %s → HTTP %d: %s",
                endpoint,
                resp.status_code,
                resp.text[:200],
            )
            return None
        except Exception as exc:
            logger.debug("SEC API POST %s error: %s", endpoint, exc)
            return None

    def _get(self, url: str, params: dict | None = None) -> dict | None:
        """GET request to sec-api.io; return parsed JSON or None."""
        if params is None:
            params = {}
        params["token"] = self._key
        try:
            resp = self._session.get(url, params=params, timeout=15)

            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "SEC API GET %s → HTTP %d: %s",
                url,
                resp.status_code,
                resp.text[:200],
            )
            return None
        except Exception as exc:
            logger.debug("SEC API GET %s error: %s", url, exc)
            return None

    def _fetch_form4(
        self,
        ticker: str,
        lookback_days: int = 90,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Query sec-api.io full-text search for Form 4 filings.

        Uses date filter inside query_string (more reliable than dateRange param
        on the free tier). Returns filing-level metadata — transaction-level
        data (buy/sell codes, shares) may not be present on the free plan.
        """
        end_dt = datetime.now(UTC)
        start_dt = end_dt - timedelta(days=lookback_days)

        date_filter = f'filedAt:[{start_dt.strftime("%Y-%m-%d")} TO {end_dt.strftime("%Y-%m-%d")}]'

        payload = {
            "query": {
                "query_string": {
                    "query": (f'formType:"4" AND ticker:{ticker.upper()} AND {date_filter}'),
                }
            },
            "from": "0",
            "size": str(min(limit, 50)),
            "sort": [{"filedAt": {"order": "desc"}}],
        }

        data = self._post("", payload)
        if data is None:
            return []

        filings = data.get("filings") or data.get("hits", {}).get("hits", [])
        if not filings:
            return []

        records: list[dict[str, Any]] = []
        for f in filings:
            # Handle both top-level and nested _source format
            src = f.get("_source", f)
            records.append(
                {
                    "ticker": ticker.upper(),
                    "form_type": src.get("formType", "4"),
                    "filed_at": src.get("filedAt", ""),
                    "period_of_report": src.get("periodOfReport", ""),
                    "entity_name": src.get("entityName", ""),
                    "url": src.get("linkToFilingDetails", ""),
                    # Transaction-level data (may be absent in search results)
                    "transaction_type": src.get("transactionCode", ""),
                    "shares": src.get("shares"),
                    "price_per_share": src.get("pricePerShare"),
                    "total_value": src.get("totalValue"),
                    "role": src.get("reportingOwnerRelationship", ""),
                }
            )

        logger.debug(
            "SECFetcher: %d Form 4 records found for %s (%d-day window)",
            len(records),
            ticker,
            lookback_days,
        )
        return records

    def _search_filings(
        self,
        ticker: str,
        form_type: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search for recent filings by form type."""
        payload = {
            "query": {
                "query_string": {
                    "query": f'formType:"{form_type}" AND ticker:"{ticker.upper()}"',
                }
            },
            "from": "0",
            "size": str(min(limit, 10)),
            "sort": [{"filedAt": {"order": "desc"}}],
        }

        data = self._post("", payload)
        if data is None:
            return []

        filings = data.get("filings") or data.get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for f in filings:
            src = f.get("_source", f)
            results.append(
                {
                    "form_type": src.get("formType", form_type),
                    "filed_at": src.get("filedAt", ""),
                    "period_of_report": src.get("periodOfReport", ""),
                    "entity_name": src.get("entityName", ""),
                    "url": src.get("linkToFilingDetails", ""),
                }
            )
        return results

    def _aggregate_insider(
        self,
        ticker: str,
        transactions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Convert raw Form 4 filings list into a quantitative insider signal.

        When transaction-level data (buy/sell codes) is available, computes
        a directional signal. When only filing metadata is available (free
        plan), uses filing count as an activity-level proxy.
        """
        filing_count = len(transactions)

        if not transactions:
            return {
                "sec_insider_net": 0,
                "sec_insider_buys": 0,
                "sec_insider_sells": 0,
                "sec_insider_value_net": 0.0,
                "sec_insider_signal": "NEUTRAL",
                "sec_recent_filings_count": 0,
            }

        buys = sells = 0
        value_net = 0.0
        has_transaction_data = False

        for t in transactions:
            code = str(t.get("transaction_type", "")).upper()
            val = t.get("total_value")
            if code:
                has_transaction_data = True
            if val is not None:
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    val = 0.0
            else:
                val = 0.0

            if code in _BUY_CODES:
                buys += 1
                value_net += val
            elif code in _SELL_CODES:
                sells += 1
                value_net -= val

        if has_transaction_data:
            # We have actual buy/sell codes
            net = buys - sells
            if net >= 2 or value_net > 500_000:
                signal = "BULLISH"
            elif net <= -2 or value_net < -500_000:
                signal = "BEARISH"
            else:
                signal = "NEUTRAL"
        else:
            # Free plan: no transaction codes — use activity level only
            # ACTIVE = elevated insider activity (>10 Form 4 filings in window)
            net = 0
            signal = "ACTIVE" if filing_count > 10 else "NEUTRAL"

        result: dict[str, Any] = {
            "sec_insider_net": net,
            "sec_insider_buys": buys,
            "sec_insider_sells": sells,
            "sec_insider_value_net": round(value_net, 2),
            "sec_insider_signal": signal,
            "sec_recent_filings_count": filing_count,
        }

        logger.debug(
            "SECFetcher: %s insider — filings=%d net=%+d signal=%s (tx_data=%s)",
            ticker,
            filing_count,
            net,
            signal,
            has_transaction_data,
        )
        return result


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: sec_fetcher.py
# Eliminado: encabezado y referencias nominales al sistema previo
# Preservado: firmas públicas, agregación insider Form 4, clasificación BUY/SELL y contrato de salidas
# Pendientes: ninguno
# ─────────────────────────────────────────────────
