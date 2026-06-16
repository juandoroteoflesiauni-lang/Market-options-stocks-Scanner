from __future__ import annotations
from typing import Protocol, Any
"""Opciones vía REST Massive / Polygon (compatible).

Intenta `GET /v3/snapshot/options/{underlying}` con cada clave Massive definida en
`Config` y cada host REST (por defecto api.polygon.io y api.massive.com).

La salida se normaliza al shape que consume `options_router._parse_finnhub_chain`
(Finnhub `stock/option-chain`).
"""


import json
import math
import threading
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

try:
    from config.logger_setup import get_logger
    from config.settings import Config, load_settings
except ModuleNotFoundError:  # pragma: no cover
    from backend.config.logger_setup import get_logger
    from backend.config.settings import Config, load_settings

try:
    from layer_1_data.datos.finnhub_fetcher import FinnhubFetcher
except ModuleNotFoundError:  # pragma: no cover
    pass

logger = get_logger(__name__)


class MassiveOptionsFetcher:
    """
    Backward-compatible async wrapper used by probabilistic_router.

    The canonical implementation in this module is still the sync
    fetch_option_chain_raw/try_massive_option_chain path; this class keeps the
    older router integration working without duplicating fetch logic.
    """

    def __init__(self, settings: Config | None = None):
        self._settings = settings

    async def get_chain(self, symbol: str, expiry: str | None = None) -> dict[str, Any] | None:
        try:
            if self._settings is not None:
                shaped, _source, _meta = try_massive_option_chain(
                    symbol.upper(), expiry, self._settings
                )
                return shaped
            result, _source, _meta = fetch_option_chain_raw(symbol.upper(), expiry)
            return result
        except Exception as exc:
            logger.warning("MassiveOptionsFetcher.get_chain failed for %s: %s", symbol, exc)
            return None


# Round-robin entry point for REST snapshot keys (distributes load across MASSIVE_KEY_OPTIONS_*).
_options_rr_lock = threading.Lock()
_options_rr_seq = 0


# Configuración de performance
def _get_request_config():
    """Devuelve configuración optimizada para las solicitudes HTTP."""
    return {
        # /v3/snapshot/options admite límites bajos (2000 dispara 400).
        # Mantener 250 evita Bad Request y permite paginar con next_url.
        "default_page_limit": 250,
        "timeout_base": 15.0,  # Timeout inicial para intentos
        "timeout_max": 45.0,  # Timeout máximo permitido
        "max_pages": 100,  # Límites para evitar búsquedas infinitas
    }


_DEFAULT_HOSTS: tuple[str, ...] = (
    "https://api.polygon.io",
    "https://api.massive.com",
)

# Cache de autenticación exitosa para cada host
host_auth_cache: dict[str, list[str]] = {
    "api.polygon.io": ["query_apiKey", "header_Bearer"],
    "api.massive.com": ["query_apikey"],
}


def _safe_float(x: object) -> float | None:
    try:
        f = float(x)  # type: ignore[arg-type]
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _normalize_iv(iv_raw: object) -> float | None:
    """IV en decimal (0.32). Polygon/Massive devuelve decimal o porcentaje ocasional (> 10).

    Valores extremos se limitan al rango [0.01, 5.0] para evitar comportamiento anómalo
    en cáculos posteriores como GEX o valoración BSM.
    """
    iv = _safe_float(iv_raw)
    if iv is None or iv <= 0:
        return None
    # Polygon API returns IV as decimal (0.32 = 32%). Values > 10.0 are clearly
    # percentages (e.g. 32.0 from some feeds). Values 2.0–10.0 are valid high-vol
    # names (biotech, meme stocks with 200–1000% IV). Do NOT scale those.

    # Aplicar escalamiento solo a valores que claramente son porcentajes (> 10%)
    # pero proteger contra valores extremadamente altos
    if iv > 10.0 and iv < 1.0e6:  # 1e6 = 1,000,000% límite superior razonable
        iv = iv / 100.0

    # Limitar IV final al rango [1%, 500%] para evitar cálculos numéricamente inestables
    # Esto captura IVs normales (0.1-0.4), altas (0.4-2.0), y extremas legitimamente altas
    return min(max(iv, 0.01), 5.0)


def _first_timestamp(*values: object) -> float | None:
    for value in values:
        ts = _safe_float(value)
        if ts is not None and ts > 0:
            return ts
    return None


def _dedupe_key_pairs(pairs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for label, raw in pairs:
        if not raw or not str(raw).strip():
            continue
        key = str(raw).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((label, key))
    return out


def _massive_key_bindings(settings: Any) -> list[tuple[str, str]]:
    """(etiqueta .env, clave) sin duplicar valores idénticos, orden estable."""
    pairs: list[tuple[str, str | None]] = [
        ("MASSIVE_KEY_OPTIONS_PRIMARY", getattr(settings, "massive_key_options_primary", None)),
        ("MASSIVE_KEY_OPTIONS_SECONDARY", getattr(settings, "massive_key_options_secondary", None)),
        ("MASSIVE_KEY_OPTIONS", getattr(settings, "massive_key_options", None)),
        ("MASSIVE_KEY_FINANCIALS", getattr(settings, "massive_key_financials", None)),
        ("MASSIVE_KEY_DISTRESS", getattr(settings, "massive_key_distress", None)),
        ("MASSIVE_KEY_MACRO", getattr(settings, "massive_key_macro", None)),
        ("MASSIVE_KEY_WS_QUOTES", getattr(settings, "massive_key_ws_quotes", None)),
        ("MASSIVE_KEY_WS_TRADES", getattr(settings, "massive_key_ws_trades", None)),
    ]
    return _dedupe_key_pairs(pairs)


def _massive_key_bindings_options_only(settings: Any) -> list[tuple[str, str]]:
    """Solo PRIMARY y SECONDARY para snapshot de opciones."""
    pairs: list[tuple[str, str | None]] = [
        ("MASSIVE_KEY_OPTIONS_PRIMARY", getattr(settings, "massive_key_options_primary", None)),
        ("MASSIVE_KEY_OPTIONS_SECONDARY", getattr(settings, "massive_key_options_secondary", None)),
    ]
    return _dedupe_key_pairs(pairs)


def _keys_for_options_snapshot(settings: Config) -> list[tuple[str, str]]:
    """Claves a usar en ``/v3/snapshot/options``: siempre solo PRIMARY y SECONDARY."""
    return _massive_key_bindings_options_only(settings)


def _ensure_polygon_api_key_on_url(url: str, api_key: str) -> str:
    """Polygon devuelve ``next_url`` sin ``apiKey``; sin él la paginación responde 401."""
    parsed = urlparse(url)
    q_items = parse_qsl(parsed.query, keep_blank_values=True)
    if any(k.lower() == "apikey" for k, _ in q_items):
        return url
    merged: list[tuple[str, str]] = list(q_items) + [("apiKey", api_key)]
    new_query = urlencode(merged)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def ordered_massive_keys_round_robin(keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rota el orden de prueba de claves en cada llamada (host×clave en caller)."""
    global _options_rr_seq
    if not keys:
        return []
    with _options_rr_lock:
        start = _options_rr_seq % len(keys)
        _options_rr_seq += 1
    return keys[start:] + keys[:start]


def _api_denial_message(text: str) -> str:
    """Texto legible del JSON de error (sin volcar la clave)."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            parts = [
                str(data.get(k) or "")
                for k in ("status", "message", "error", "request_id")
                if data.get(k)
            ]
            if parts:
                return " | ".join(parts)[:400]
    except (json.JSONDecodeError, TypeError):
        pass
    return (text or "")[:400]


def _ordered_expiry_keys(exp_keys: list[str]) -> list[str]:
    """Primera expiry alineada con Finnhub: la vigente más cercana (>= hoy UTC)."""
    today = datetime.now(tz=UTC).date()
    parsed: list[tuple[date, str]] = []
    for k in exp_keys:
        ks = str(k)[:10]
        try:
            parsed.append((datetime.strptime(ks, "%Y-%m-%d").date(), k))
        except ValueError:
            parsed.append((date.max, k))
    parsed.sort(key=lambda x: x[0])
    future = [k for d, k in parsed if d >= today]
    return future if future else [k for _, k in parsed]


def _rest_hosts(settings: Any) -> tuple[str, ...]:
    raw = getattr(settings, "massive_rest_base_urls", None)
    if not raw or not str(raw).strip():
        return _DEFAULT_HOSTS
    parts = [p.strip().rstrip("/") for p in str(raw).split(",") if p.strip()]
    return tuple(parts) if parts else _DEFAULT_HOSTS


def _fetch_snapshot_pages(
    host: str,
    api_key: str,
    symbol: str,
    expiry: str | None,
    key_label: str,
) -> tuple[list[dict[str, Any]] | None, int, int]:
    """Una clave + un host. Prueba formas de auth con manejo optimizado de errores y timeouts.

    Returns:
        (results, pages_read, status_hint)
        status_hint: 0 = éxito; 429 = rate limit; otro = fallo duro
    """
    # Obtener configuración optimizada
    config = _get_request_config()

    sym = symbol.upper().strip()
    base_url = f"{host.rstrip('/')}/v3/snapshot/options/{sym}"

    # Usar límite seguro local (evita depender de atributos opcionales del Config)
    page_limit = int(config["default_page_limit"])
    base_params: dict[str, Any] = {"limit": page_limit}
    if expiry and str(expiry).strip():
        base_params["expiration_date"] = str(expiry).strip()[:10]
    else:
        # [PD-3][TH] API Optimization: Predictivity restricted to Intraday/Scalping window (7 days)
        # Limits options chain size by ~90% for massive cost savings.
        from datetime import timedelta

        horizon_date = datetime.now(tz=UTC).date() + timedelta(days=7)
        base_params["expiration_date.lte"] = horizon_date.isoformat()

    collected: list[dict[str, Any]] = []
    next_url: str | None = base_url
    is_first = True
    pages = 0

    # Obtener estilos de autenticación preferidos del cache
    host_key = host.replace("https://", "").split(":")[0]
    preferred_auth_styles = host_auth_cache.get(host_key, [])

    try:
        with httpx.Client(timeout=config["timeout_max"]) as client:
            while next_url and pages < config["max_pages"]:
                pages += 1

                if is_first:
                    # Construir intentos de autenticación
                    auth_attempts: list[tuple[str, dict[str, Any], dict[str, str]]] = []

                    # Usar estilos preferidos primero (del cache)
                    for style in preferred_auth_styles:
                        if style == "query_apiKey":
                            auth_attempts.append(
                                ("query_apiKey", {**base_params, "apiKey": api_key}, {})
                            )
                        elif style == "query_apikey":
                            auth_attempts.append(
                                ("query_apikey", {**base_params, "apikey": api_key}, {})
                            )
                        elif style == "header_Bearer":
                            auth_attempts.append(
                                (
                                    "header_Bearer",
                                    dict(base_params),
                                    {"Authorization": f"Bearer {api_key}"},
                                )
                            )

                    # Si no hay preferencias, probar todos los esquemas
                    if not preferred_auth_styles:
                        auth_attempts.extend(
                            [
                                ("query_apiKey", {**base_params, "apiKey": api_key}, {}),
                                ("query_apikey", {**base_params, "apikey": api_key}, {}),
                                (
                                    "header_Bearer",
                                    dict(base_params),
                                    {"Authorization": f"Bearer {api_key}"},
                                ),
                            ]
                        )

                    resp: httpx.Response | None = None
                    last_exception = None

                    for attempt_idx, (_style, qp, hdr) in enumerate(auth_attempts):
                        try:
                            # Timeout escalonado: rápido inicialmente, más tiempo para reintentos
                            timeout = min(
                                config["timeout_base"] + (attempt_idx * 7.0), config["timeout_max"]
                            )

                            # Usar GET con retries implícitos mediante el ciclo de intentos
                            r = client.get(base_url, params=qp, headers=hdr, timeout=timeout)
                            resp = r

                            # Registro del intento
                            logger.debug(
                                f"massive_options: auth attempt {_style} for {host}: HTTP {r.status_code}"
                            )

                            # Éxito: terminar
                            if r.status_code == 200:
                                # Registrar esquema exitoso para futuros usos
                                if host_key not in host_auth_cache:
                                    host_auth_cache[host_key] = []
                                if _style not in host_auth_cache[host_key]:
                                    host_auth_cache[host_key].insert(0, _style)  # Poner al inicio
                                break

                            # Rate limit: terminar con error específico
                            if r.status_code == 429:
                                last_exception = None
                                break

                            # Para errores no relacionados con auth, terminar aquí
                            if r.status_code not in (401, 403):
                                break

                        except (
                            httpx.TimeoutException,
                            httpx.NetworkError,
                            httpx.RemoteProtocolError,
                        ) as err:
                            logger.debug(
                                f"massive_options: attempt {_style} failed with error: {err}"
                            )
                            last_exception = err
                            if attempt_idx < len(auth_attempts) - 1:
                                continue  # Probar siguiente intento
                            else:
                                raise  # Llegó al último intento

                    is_first = False

                    # Error especifico de rate limit
                    if resp is not None and resp.status_code == 429:
                        host_short = host.replace("https://", "")
                        logger.info(
                            "massive_options: 429 rate limit %s %s - rotando a otra clave/host",
                            key_label,
                            host_short,
                        )
                        return None, 0, 429

                    if resp is None or resp.status_code != 200:
                        break
                else:
                    paginated_url = _ensure_polygon_api_key_on_url(next_url, api_key)
                    resp = client.get(paginated_url)
                    if resp.status_code in (401, 403):
                        resp = client.get(
                            paginated_url,
                            headers={"Authorization": f"Bearer {api_key}"},
                        )
                    if resp.status_code == 429:
                        logger.info(
                            "massive_options: 429 mid-pagination host=%s sym=%s pages=%s - devolviendo parcial",
                            host,
                            sym,
                            pages,
                        )
                        return (collected if collected else None), pages, 429
                    if resp.status_code != 200:
                        logger.debug(
                            "massive_options: pagination host=%s sym=%s status=%s",
                            host,
                            sym,
                            resp.status_code,
                        )
                        break
                body = resp.json()
                if not isinstance(body, dict):
                    return None, pages, int(resp.status_code) if resp is not None else 500
                status = str(body.get("status", "")).upper()
                chunk = body.get("results")
                if isinstance(chunk, list):
                    collected.extend(c for c in chunk if isinstance(c, dict))
                if status not in ("OK", "DELAYED") and not chunk:
                    logger.debug("massive_options: host=%s sym=%s api status=%s", host, sym, status)
                next_url = body.get("next_url")
                if isinstance(next_url, str) and next_url.startswith("http"):
                    continue
                break
    except Exception as exc:
        logger.debug("massive_options: request failed host=%s sym=%s: %s", host, sym, exc)
        return None, 0, 0

    return (collected if collected else None, pages, 0)


def massive_snapshot_to_finnhub_shape(results: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Convierte `results` del snapshot a payload estilo Finnhub."""
    by_exp: dict[str, list[dict[str, Any]]] = {}
    spot: float | None = None

    for item in results:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        exp = details.get("expiration_date")
        if not exp:
            continue
        exp_s = str(exp)[:10]
        ctype = str(details.get("contract_type", "")).upper()
        if ctype not in ("CALL", "PUT"):
            continue
        strike = _safe_float(details.get("strike_price"))
        if strike is None or strike <= 0:
            continue

        day = item.get("day") if isinstance(item.get("day"), dict) else {}
        lq = item.get("last_quote") if isinstance(item.get("last_quote"), dict) else {}
        lt = item.get("last_trade") if isinstance(item.get("last_trade"), dict) else {}
        greeks = item.get("greeks") if isinstance(item.get("greeks"), dict) else {}

        bid = _safe_float(lq.get("bid"))
        if bid is None:
            bid = _safe_float(lq.get("bid_price"))
        ask = _safe_float(lq.get("ask"))
        if ask is None:
            ask = _safe_float(lq.get("ask_price"))
        # Prefer most-recent data: last trade → quote midpoint → day close
        last = _safe_float(lt.get("price"))
        if last is None:
            last = _safe_float(lq.get("midpoint"))
        if last is None:
            last = _safe_float(day.get("close"))

        oi = _safe_float(item.get("open_interest"))
        vol = _safe_float(day.get("volume"))
        iv = _normalize_iv(item.get("implied_volatility"))

        opt_row: dict[str, Any] = {
            "type": "CALL" if ctype == "CALL" else "PUT",
            "strike": strike,
            "contractTicker": str(details.get("ticker") or "") or None,
            "exerciseStyle": str(details.get("exercise_style") or "") or None,
            "sharesPerContract": _safe_float(details.get("shares_per_contract")),
            "primaryExchange": str(details.get("primary_exchange") or "") or None,
            "additionalUnderlyingsCount": (
                len(details.get("additional_underlyings"))
                if isinstance(details.get("additional_underlyings"), list)
                else None
            ),
            "bid": bid,
            "ask": ask,
            "lastPrice": last,
            "openInterest": oi,
            "openInterestChange": _safe_float(item.get("open_interest_change")),
            "volume": vol,
            "impliedVolatility": iv,
            # API-provided Greeks (fallback when BSM can't compute from IV)
            "delta": _safe_float(greeks.get("delta")),
            "gamma": _safe_float(greeks.get("gamma")),
            "theta": _safe_float(greeks.get("theta")),
            "vega": _safe_float(greeks.get("vega")),
            # Extended fields from Massive/Polygon snapshot
            "breakEvenPrice": _safe_float(item.get("break_even_price")),
            "bidSize": _safe_float(lq.get("bid_size")),
            "askSize": _safe_float(lq.get("ask_size")),
            "change": _safe_float(day.get("change")),
            "changePercent": _safe_float(day.get("change_percent")),
            "vwap": _safe_float(day.get("vwap")),
            "open": _safe_float(day.get("open")),
            "high": _safe_float(day.get("high")),
            "low": _safe_float(day.get("low")),
            "close": _safe_float(day.get("close")),
            "previousClose": _safe_float(day.get("previous_close")),
            "quoteTimestamp": _first_timestamp(
                lq.get("sip_timestamp"),
                lq.get("participant_timestamp"),
                lq.get("timestamp"),
                lq.get("time"),
            ),
            "tradeTimestamp": _first_timestamp(
                lt.get("sip_timestamp"),
                lt.get("participant_timestamp"),
                lt.get("timestamp"),
                lt.get("time"),
            ),
        }
        by_exp.setdefault(exp_s, []).append(opt_row)

        ua = item.get("underlying_asset") if isinstance(item.get("underlying_asset"), dict) else {}
        p = _safe_float(ua.get("price"))
        if p is not None and p > 0:
            spot = p

    if not by_exp:
        return {}

    data_blocks: list[dict[str, Any]] = []
    for exp_key in _ordered_expiry_keys(list(by_exp.keys())):
        block: dict[str, Any] = {"expirationDate": exp_key, "options": by_exp[exp_key]}
        if spot is not None and not data_blocks:
            block["underlying"] = {"close": spot}
        data_blocks.append(block)

    out: dict[str, Any] = {"data": data_blocks}
    if spot is not None:
        out["quote"] = {"c": spot}
    return out


def _snapshot_shape_has_underlying_spot(shaped: dict[str, Any]) -> bool:
    """True si el payload ya trae spot del subyacente (Polygon ``underlying_asset`` → quote/underlying)."""
    q = shaped.get("quote")
    if isinstance(q, dict):
        c = _safe_float(q.get("c"))
        if c is not None and c > 0:
            return True
    data = shaped.get("data")
    if isinstance(data, list):
        for blk in data:
            if not isinstance(blk, dict):
                continue
            u = blk.get("underlying")
            if isinstance(u, dict):
                cl = _safe_float(u.get("close"))
                if cl is not None and cl > 0:
                    return True
    return False


def try_massive_option_chain(
    symbol: str, expiry: str | None, settings: Config | None = None
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """
    Prueba cada host REST × cada clave Massive del .env hasta obtener `results`.

    Returns:
        (payload_finnhub_shape, fuente, meta) — meta incluye massive_contracts, massive_pages, maybe_truncated.
    """
    sym = symbol.upper().strip()
    use_chain_cache = expiry is None
    if use_chain_cache:
        from backend.hub.market_data_ttl_cache import (
            get_massive_options_chain,
            put_massive_options_chain,
        )

        cached = get_massive_options_chain(sym)
        if cached is not None:
            shaped, src, meta = cached
            if shaped is None:
                return None, "", {}
            return shaped, src, meta

    def _store(result: tuple[dict[str, Any] | None, str, dict[str, Any]]) -> tuple[
        dict[str, Any] | None, str, dict[str, Any]
    ]:
        if use_chain_cache:
            from backend.hub.market_data_ttl_cache import put_massive_options_chain

            put_massive_options_chain(sym, result)
        return result

    cfg = settings or load_settings()
    hosts = _rest_hosts(cfg)
    keys = _keys_for_options_snapshot(cfg)
    if not keys:
        logger.info("massive_options: no Massive API keys in env — skip REST fallback")
        return _store((None, "", {}))

    fallback: tuple[dict[str, Any], str, dict[str, Any]] | None = None
    for host in hosts:
        for label, api_key in keys:
            results, pages_read, hint = _fetch_snapshot_pages(host, api_key, sym, expiry, label)
            if results:
                shaped = massive_snapshot_to_finnhub_shape(results, sym)
                if shaped.get("data"):
                    src = f"massive:{label}@{host.replace('https://', '')}"
                    n = len(results)
                    meta: dict[str, Any] = {
                        "massive_contracts": n,
                        "massive_pages": pages_read,
                        "maybe_truncated": n >= 9900,
                        "last_http_hint": hint,
                    }
                    if _snapshot_shape_has_underlying_spot(shaped):
                        logger.info(
                            "massive_options: chain for %s via %s (%d contracts)",
                            sym,
                            src,
                            n,
                        )
                        return _store((shaped, src, meta))
                    logger.info(
                        "massive_options: cadena %s (%d) sin spot de subyacente en payload — "
                        "siguiente clave/host",
                        label,
                        n,
                    )
                    if fallback is None:
                        fallback = (shaped, src, meta)
            if hint == 429:
                continue
    if fallback is not None:
        sh, src_fb, meta_fb = fallback
        meta_fb = {
            **meta_fb,
            "underlying_spot_missing": True,
        }
        logger.warning(
            "massive_options: usando cadena sin spot de Polygon para %s (%s) — "
            "el router puede mostrar spot por defecto",
            sym,
            src_fb,
        )
        return _store((sh, src_fb, meta_fb))
    logger.warning("massive_options: all Massive hosts/keys failed for %s", sym)
    return _store((None, "", {}))


def fetch_option_chain_raw(
    symbol: str,
    expiry: str | None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """
    Fuente exclusiva: Massive/Polygon con MASSIVE_KEY_OPTIONS_PRIMARY → SECONDARY.

    Returns:
        (raw_dict, "massive:…" | "", meta) — meta describe filas/cobertura de fetch.
    """
    sym = symbol.upper().strip()
    shaped, src, meta_m = try_massive_option_chain(sym, expiry)
    if shaped and isinstance(shaped.get("data"), list) and len(shaped["data"]) > 0:
        return shaped, src, meta_m
    return None, "", {}


# ── Diagnóstico de acceso de claves ───────────────────────────────────────────


def probe_options_key_access(
    symbol: str = "AAPL",
    settings: Config | None = None,
) -> dict[str, Any]:
    """
    Prueba cada clave (PRIMARY y SECONDARY) contra los endpoints relevantes de opciones
    y devuelve un informe de qué tiene acceso.

    Endpoints probados:
      - /v3/snapshot/options/{symbol}   → snapshot de cadena (options_snapshot)
      - /v3/trades/O:{OCC_ticker}       → trades tick-a-tick (requiere plan superior)

    Uso:
        from backend.layer_1_data.datos.massive_options_fetcher import probe_options_key_access
        logger.info(probe_options_key_access())
    """
    cfg = settings or load_settings()
    sym = symbol.upper().strip()
    today_str = datetime.now(tz=UTC).strftime("%y%m%d")
    occ_probe = f"O:{sym}{today_str}C00300000"

    key_pairs: list[tuple[str, str]] = _dedupe_key_pairs(
        [
            ("MASSIVE_KEY_OPTIONS_PRIMARY", cfg.massive_key_options_primary),
            ("MASSIVE_KEY_OPTIONS_SECONDARY", cfg.massive_key_options_secondary),
        ]
    )

    report: dict[str, Any] = {"symbol": sym, "occ_probe": occ_probe, "keys": []}

    if not key_pairs:
        report["error"] = "No keys configured (MASSIVE_KEY_OPTIONS_PRIMARY / SECONDARY)"
        logger.warning("probe_options_key_access: %s", report["error"])
        return report

    hosts = _rest_hosts(cfg)

    with httpx.Client(timeout=10.0) as client:
        for label, api_key in key_pairs:
            key_report: dict[str, Any] = {
                "label": label,
                "masked": f"{api_key[:4]}...{api_key[-4:]}",
                "hosts": [],
            }
            for host in hosts:
                host_short = host.replace("https://", "")
                host_entry: dict[str, Any] = {"host": host_short, "snapshot": None, "trades": None}

                # ── Snapshot probe ──────────────────────────────────────────
                try:
                    r = client.get(
                        f"{host}/v3/snapshot/options/{sym}",
                        params={"limit": 1, "apiKey": api_key},
                    )
                    if r.status_code == 200:
                        body = r.json()
                        n = len(body.get("results") or [])
                        host_entry["snapshot"] = f"OK (results={n})"
                    else:
                        host_entry["snapshot"] = f"HTTP {r.status_code}"
                except Exception as exc:
                    host_entry["snapshot"] = f"ERROR: {exc}"

                # ── Trades probe ────────────────────────────────────────────
                try:
                    r = client.get(
                        f"{host}/v3/trades/{occ_probe}",
                        params={"limit": 1, "apiKey": api_key},
                    )
                    if r.status_code == 200:
                        body = r.json()
                        n = len(body.get("results") or [])
                        host_entry["trades"] = f"OK (results={n})"
                    else:
                        host_entry["trades"] = f"HTTP {r.status_code}"
                except Exception as exc:
                    host_entry["trades"] = f"ERROR: {exc}"

                key_report["hosts"].append(host_entry)
                logger.info(
                    "probe_options_key_access: label=%s host=%s snapshot=%s trades=%s",
                    label,
                    host_short,
                    host_entry["snapshot"],
                    host_entry["trades"],
                )

            report["keys"].append(key_report)

    return report
