"""
═══════════════════════════════════════════════════════════════════════
 Sistema VWAP/AVWAP Híbrido — Motores 13 al 18
 FMP Enterprise · Greeks de Opciones
 Symbols: AAPL · MSFT · PLTR · TSLA · GOOGL
═══════════════════════════════════════════════════════════════════════
 MOTORES
   13  VWAP Triple Híbrido      spot + GEX-opciones + vol-FMP-1m
   14  AVWAP Earnings Anchor    ancla automática en earnings release
   15  AVWAP Institucional 13F  ancla en precio promedio top-N fondos
   16  AVWAP Macro Event        CPI/Fed/NFP + ventana silencio 30 min
   17  AVWAP Smart Money        compras insider/congress filtradas
   18  AVWAP News Catalyst      press-releases de alta relevancia
═══════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import aiohttp
import numpy as np
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Protocol, Any, Dict, List, Optional, Tuple

logger = logging.getLogger("avwap.motors")

SYMBOLS: List[str] = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]
FMP_BASE = "https://financialmodelingprep.com/api"
FMP_WS   = "wss://websockets.financialmodelingprep.com"

# ─────────────────────────────────────────────────────────────
# 1. CONFIGURACIÓN GLOBAL
# ─────────────────────────────────────────────────────────────

@dataclass
class Config:
    """Todos los parámetros calibrables. Ver CALIBRACION.md."""
    fmp_api_key    : str   = ""
    request_timeout: int   = 15
    cache_ttl_sec  : int   = 300

    # Motor 13
    m13_spot_weight : float = 0.50
    m13_gex_weight  : float = 0.30
    m13_vol1m_weight: float = 0.20
    m13_band_std    : float = 1.50
    m13_conv_tol    : float = 0.003

    # Motor 14
    m14_band_k            : float = 0.80
    m14_persist_sessions  : int   = 15
    m14_iv_crush_threshold: float = 0.25
    m14_surprise_min      : float = 0.005

    # Motor 15
    m15_top_n        : int   = 5
    m15_oi_wall_mult : float = 1.50
    m15_min_float_pct: float = 0.005

    # Motor 16
    m16_silence_min      : int   = 30
    m16_iv_band_mult     : float = 0.50
    m16_min_importance   : int   = 3
    m16_reanchor_wait_min: int   = 15

    # Motor 17
    m17_insider_min_usd : float = 500_000
    m17_congress_min_usd: float = 250_000
    m17_delta_threshold : float = 0.55
    m17_lookback_days   : int   = 30

    # Motor 18
    m18_relevance_min: float = 0.65
    m18_iv_spike_min : float = 0.15
    m18_wait_min     : int   = 5
    m18_max_active   : int   = 3

    # Critical Moment Engine
    cme_freeze_severity: int   = 3
    cme_close_severity : int   = 4
    cme_block_severity : int   = 5
    cme_earnings_reduce: float = 0.50
    cme_etf_align_mult : float = 1.25


# ─────────────────────────────────────────────────────────────
# 2. MODELOS DE DATOS
# ─────────────────────────────────────────────────────────────

@dataclass
class Candle:
    ts: datetime; open: float; high: float
    low: float;   close: float; volume: float

    @property
    def tp(self) -> float:
        return (self.high + self.low + self.close) / 3.0


@dataclass
class OptionGreeks:
    strike: float; expiry: str; delta: float
    gamma: float;  iv: float;   open_interest: int; call_put: str


@dataclass
class AVWAPState:
    """Acumulador incremental O(1) por vela."""
    symbol      : str
    anchor_ts   : datetime
    anchor_price: float
    motor_id    : int
    motor_name  : str
    meta        : Dict[str, Any] = field(default_factory=dict)

    _cum_pv : float = 0.0
    _cum_vol: float = 0.0
    _cum_pv2: float = 0.0
    _n      : int   = 0

    active        : bool = True
    frozen        : bool = False
    sessions_alive: int  = 0

    def update(self, c: Candle) -> None:
        if self.frozen or not self.active:
            return
        v = c.volume
        tp = c.tp
        self._cum_pv  += tp * v
        self._cum_vol += v
        self._cum_pv2 += (tp ** 2) * v
        self._n       += 1

    @property
    def value(self) -> Optional[float]:
        return self._cum_pv / self._cum_vol if self._cum_vol > 0 else None

    @property
    def std(self) -> Optional[float]:
        if self._cum_vol == 0 or self._n < 2:
            return None
        m = self.value
        return float(max(0.0, self._cum_pv2 / self._cum_vol - m ** 2) ** 0.5)

    def band(self, n: float = 1.0) -> Tuple[Optional[float], Optional[float]]:
        v, s = self.value, self.std
        if v is None or s is None:
            return None, None
        return v + n * s, v - n * s


@dataclass
class Signal:
    ts: datetime; symbol: str; motor_id: int
    direction: str; strength: float
    avwap_value: Optional[float]
    band_upper : Optional[float]
    band_lower : Optional[float]
    rationale  : str
    meta       : Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 3. CLIENTE FMP ENTERPRISE
# ─────────────────────────────────────────────────────────────

class AVWAPFMPClient:
    """HTTP async con caché TTL para endpoints lentos (13F, dividendos, etc.)."""

    def __init__(self, cfg: Config):
        self.cfg   = cfg
        self._ses  : Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}

    async def _s(self) -> aiohttp.ClientSession:
        if self._ses is None or self._ses.closed:
            self._ses = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.cfg.request_timeout))
        return self._ses

    async def _get(self, url: str, params: Dict = {}, cache=False) -> Any:
        import time
        key = url + str(sorted(params.items()))
        if cache and key in self._cache:
            exp, d = self._cache[key]
            if time.time() < exp:
                return d
        p = {**params, "apikey": self.cfg.fmp_api_key}
        s = await self._s()
        async with s.get(url, params=p) as r:
            r.raise_for_status()
            d = await r.json()
        if cache:
            self._cache[key] = (time.time() + self.cfg.cache_ttl_sec, d)
        return d

    # ── precio ────────────────────────────────────────────────
    async def get_intraday(self, sym: str, iv: str = "1min",
                           limit: int = 390) -> List[Candle]:
        raw = await self._get(f"{FMP_BASE}/v3/historical-chart/{iv}/{sym}",
                              {"limit": limit})
        out = []
        for r in reversed(raw if isinstance(raw, list) else []):
            out.append(Candle(
                ts    = datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc),
                open  = float(r["open"]),  high  = float(r["high"]),
                low   = float(r["low"]),   close = float(r["close"]),
                volume= float(r["volume"]),
            ))
        return out

    # ── opciones ──────────────────────────────────────────────
    async def get_options(self, sym: str) -> List[OptionGreeks]:
        raw = await self._get(f"{FMP_BASE}/v3/options/{sym}")
        out = []
        for r in (raw if isinstance(raw, list) else []):
            out.append(OptionGreeks(
                strike=float(r.get("strike",0)),
                expiry=r.get("expirationType",""),
                delta =float(r.get("delta",0)),
                gamma =float(r.get("gamma",0)),
                iv    =float(r.get("impliedVolatility",0)),
                open_interest=int(r.get("openInterest",0)),
                call_put=r.get("callOrPut","call").lower(),
            ))
        return out

    # ── earnings ──────────────────────────────────────────────
    async def get_earnings_calendar(self, frm: str, to: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/earning_calendar",
                               {"from": frm, "to": to}, cache=True)

    async def get_earnings_surprise(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/earningsSurprises/{sym}", cache=True)

    # ── 13F ───────────────────────────────────────────────────
    async def get_institutional_holders(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/institutional-holder/{sym}", cache=True)

    # ── macro ─────────────────────────────────────────────────
    async def get_economic_calendar(self, frm: str, to: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/economic_calendar",
                               {"from": frm, "to": to}, cache=True)

    # ── insider / congreso ────────────────────────────────────
    async def get_insider_trades(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v4/insider-trading",
                               {"symbol": sym, "limit": 50}, cache=True)

    async def get_senate_trades(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v4/senate-trading",
                               {"symbol": sym}, cache=True)

    async def get_house_trades(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v4/house-disclosure",
                               {"symbol": sym}, cache=True)

    # ── noticias ──────────────────────────────────────────────
    async def get_news(self, sym: str, limit: int = 15) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/stock_news",
                               {"tickers": sym, "limit": limit})

    async def get_press_releases(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/press-releases/{sym}", {"limit":10})

    # ── dividendos / splits ───────────────────────────────────
    async def get_dividends(self, sym: str) -> List[Dict]:
        r = await self._get(
            f"{FMP_BASE}/v3/historical-price-full/stock_dividend/{sym}", cache=True)
        return r.get("historical", []) if isinstance(r, dict) else []

    async def get_splits(self, sym: str) -> List[Dict]:
        r = await self._get(
            f"{FMP_BASE}/v3/historical-price-full/stock_split/{sym}", cache=True)
        return r.get("historical", []) if isinstance(r, dict) else []

    # ── ETF ───────────────────────────────────────────────────
    async def get_etf_holders(self, sym: str) -> List[Dict]:
        return await self._get(f"{FMP_BASE}/v3/etf-holder/{sym}", cache=True)

    async def close(self):
        if self._ses and not self._ses.closed:
            await self._ses.close()


# ─────────────────────────────────────────────────────────────
# 4. ANALIZADOR DE OPCIONES
# ─────────────────────────────────────────────────────────────

class OptionsAnalyzer:

    @staticmethod
    def shadow_delta_weight(opts: List[OptionGreeks],
                            price: float, band=0.02) -> float:
        """
        |delta| medio ponderado por OI en opciones ATM (±band%).
        Usado como multiplicador del volumen en la capa GEX-VWAP.
        """
        atm = [o for o in opts if abs(o.strike - price) / price <= band]
        if not atm:
            return 0.5
        tot = sum(o.open_interest for o in atm)
        if tot == 0:
            return 0.5
        return float(np.clip(
            sum(abs(o.delta) * o.open_interest for o in atm) / tot, 0.1, 0.9))

    @staticmethod
    def atm_iv(opts: List[OptionGreeks], price: float, band=0.02) -> float:
        atm = [o for o in opts if abs(o.strike - price) / price <= band]
        return float(np.mean([o.iv for o in atm])) if atm else 0.0

    @staticmethod
    def oi_by_strike(opts: List[OptionGreeks]) -> Dict[float, int]:
        d: Dict[float, int] = {}
        for o in opts:
            d[o.strike] = d.get(o.strike, 0) + o.open_interest
        return d

    @staticmethod
    def detect_oi_walls(oi_map: Dict[float, int], mult=1.5) -> List[float]:
        if not oi_map:
            return []
        mean = float(np.mean(list(oi_map.values())))
        return sorted(s for s, v in oi_map.items() if v >= mult * mean)

    @staticmethod
    def net_delta_bias(opts: List[OptionGreeks]) -> float:
        calls = sum(o.delta * o.open_interest for o in opts if o.call_put == "call")
        puts  = sum(abs(o.delta) * o.open_interest for o in opts if o.call_put == "put")
        tot   = calls + puts
        return (calls - puts) / tot if tot > 0 else 0.0


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.split("T")[0]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def _parse_amount(s: str) -> float:
    if not s:
        return 0.0
    c = s.replace("$","").replace(",","").strip()
    if "-" in c:
        p = c.split("-")
        try:
            return (float(p[0]) + float(p[1])) / 2
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(c)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────
# 5. MOTOR 13 — VWAP TRIPLE HÍBRIDO
# ─────────────────────────────────────────────────────────────

@dataclass
class _TripleState:
    symbol: str
    spot_vwap: Optional[float] = None
    gex_vwap : Optional[float] = None
    vol1_vwap: Optional[float] = None
    composite: Optional[float] = None
    upper    : Optional[float] = None
    lower    : Optional[float] = None
    _sp_pv: float=0; _sp_vol: float=0; _sp_pv2: float=0
    _gx_pv: float=0; _gx_vol: float=0
    _1m_pv: float=0; _1m_vol: float=0


class Motor13VWAPTriple:
    """
    Tres capas de VWAP en simultáneo:
      Capa 1 — SPOT  : precio_típico × volumen real             (peso 50 %)
      Capa 2 — GEX   : volumen × shadow_delta de opciones ATM   (peso 30 %)
      Capa 3 — VOL1m : VWAP usando volumen exacto FMP 1-min     (peso 20 %)

    Señal cuando precio sale de la banda ±N·σ del compuesto ponderado.
    Spread entre capas < m13_conv_tol → equilibrio, no operar.

    Calibración rápida:
      Scalping 1-5 min  → m13_band_std 1.0–1.5, m13_conv_tol 0.002
      Swing intradía    → m13_band_std 2.0–2.5, m13_conv_tol 0.005
    """

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._st: Dict[str, _TripleState] = {s: _TripleState(s) for s in SYMBOLS}

    def reset_session(self, sym: str) -> None:
        self._st[sym] = _TripleState(sym)
        logger.info(f"[M13] {sym}: sesión reiniciada")

    def update(self, sym: str, c: Candle,
               opts: List[OptionGreeks]) -> Optional[Signal]:
        st = self._st[sym]
        tp = c.tp; v = c.volume

        # Capa 1 — spot
        st._sp_pv += tp*v; st._sp_vol += v; st._sp_pv2 += tp**2*v
        if st._sp_vol: st.spot_vwap = st._sp_pv / st._sp_vol

        # Capa 2 — GEX (shadow-delta como peso del volumen)
        sd = self.oa.shadow_delta_weight(opts, c.close)
        gv = v * sd
        st._gx_pv += tp*gv; st._gx_vol += gv
        if st._gx_vol: st.gex_vwap = st._gx_pv / st._gx_vol

        # Capa 3 — vol FMP 1-min
        st._1m_pv += tp*v; st._1m_vol += v
        if st._1m_vol: st.vol1_vwap = st._1m_pv / st._1m_vol

        # Compuesto + banda σ
        if all(x is not None for x in [st.spot_vwap, st.gex_vwap, st.vol1_vwap]):
            st.composite = (self.cfg.m13_spot_weight  * st.spot_vwap
                          + self.cfg.m13_gex_weight   * st.gex_vwap
                          + self.cfg.m13_vol1m_weight * st.vol1_vwap)
            if st._sp_vol:
                var = max(0.0, st._sp_pv2/st._sp_vol - st.spot_vwap**2)
                sd_ = var**0.5
                st.upper = st.composite + self.cfg.m13_band_std * sd_
                st.lower = st.composite - self.cfg.m13_band_std * sd_

        return self._signal(sym, c)

    def _signal(self, sym: str, c: Candle) -> Optional[Signal]:
        st = self._st[sym]
        if st.composite is None or st.upper is None:
            return None
        layers = [x for x in [st.spot_vwap, st.gex_vwap, st.vol1_vwap] if x]
        if len(layers) < 2:
            return None
        spread = (max(layers) - min(layers)) / st.composite
        if spread < self.cfg.m13_conv_tol:
            return None  # capas convergidas → equilibrio

        p = c.close
        if   p < st.lower: direction = "LONG";  strength = min(1.0,(st.lower-p)/st.lower*20)
        elif p > st.upper: direction = "SHORT"; strength = min(1.0,(p-st.upper)/st.upper*20)
        else: return None

        return Signal(ts=c.ts, symbol=sym, motor_id=13,
            direction=direction, strength=strength,
            avwap_value=st.composite, band_upper=st.upper, band_lower=st.lower,
            rationale=(f"M13 spread={spread:.4f} spot={st.spot_vwap:.2f} "
                       f"gex={st.gex_vwap:.2f} 1m={st.vol1_vwap:.2f} "
                       f"comp={st.composite:.2f}"))

    async def initialize_session(self, sym: str) -> None:
        self.reset_session(sym)
        candles = await self.fmp.get_intraday(sym, "1min", 390)
        opts    = await self.fmp.get_options(sym)
        for c in candles:
            self.update(sym, c, opts)
        logger.info(f"[M13] {sym}: inicializado con {len(candles)} velas")


# ─────────────────────────────────────────────────────────────
# 6. MOTOR 14 — AVWAP EARNINGS ANCHOR
# ─────────────────────────────────────────────────────────────

class Motor14EarningsAnchor:
    """
    Ancla automática en timestamp exacto del earnings release.
    Banda calibrada con earnings_surprise%.
    Persiste m14_persist_sessions sesiones de mercado.

    Señal:
      precio < banda_inferior → LONG  (retorno post-earnings gap-down)
      precio > banda_superior → SHORT (sobreextensión post-earnings gap-up)

    Calibración:
      m14_band_k 0.5-0.8  para acciones con reacciones de earnings moderadas
      m14_band_k 1.0-1.5  para TSLA/PLTR (reacciones extremas)
      m14_surprise_min: subir a 0.01 si hay demasiadas anclas de ruido
    """

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._anchors: Dict[str, List[AVWAPState]] = {s: [] for s in SYMBOLS}

    async def check_and_anchor(self, sym: str,
                               now: datetime) -> Optional[AVWAPState]:
        frm = (now - timedelta(hours=12)).strftime("%Y-%m-%d")
        to  = (now + timedelta(hours=12)).strftime("%Y-%m-%d")
        try:
            cal  = await self.fmp.get_earnings_calendar(frm, to)
            surp = await self.fmp.get_earnings_surprise(sym)
        except Exception as e:
            logger.warning(f"[M14] {sym}: FMP error {e}"); return None

        ev = next((e for e in cal if e.get("symbol") == sym), None)
        if not ev: return None

        try:
            ev_ts = datetime.fromisoformat(
                ev.get("date","").replace("Z","+00:00")).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        if abs((now - ev_ts).total_seconds()) > 1800: return None

        # Evitar duplicados
        if any(abs((a.anchor_ts-ev_ts).total_seconds()) < 60
               for a in self._anchors[sym]):
            return None

        # Calcular surprise %
        sp = 0.0
        if surp:
            actual   = float(surp[0].get("actualEarningResult", 0) or 0)
            estimate = float(surp[0].get("estimatedEarning", 0.001) or 0.001)
            sp = (actual - estimate) / abs(estimate) if estimate else 0.0

        if abs(sp) < self.cfg.m14_surprise_min:
            logger.info(f"[M14] {sym}: surprise {sp:.3f} < umbral, ignorado")
            return None

        cs = await self.fmp.get_intraday(sym, "1min", 5)
        ap = cs[-1].close if cs else 0.0

        state = AVWAPState(
            symbol=sym, anchor_ts=ev_ts, anchor_price=ap,
            motor_id=14, motor_name="AVWAP Earnings",
            meta={"surprise_pct": sp,
                  "persist_sessions": self.cfg.m14_persist_sessions})
        self._anchors[sym].append(state)
        logger.info(f"[M14] {sym}: ancla @ {ap:.2f} surprise={sp:+.2%}")
        return state

    def update(self, sym: str, c: Candle) -> List[Signal]:
        sigs = []
        for a in self._anchors[sym]:
            if not a.active: continue
            a.update(c)
            s = self._signal(sym, a, c)
            if s: sigs.append(s)
        return sigs

    def _signal(self, sym: str, a: AVWAPState, c: Candle) -> Optional[Signal]:
        v = a.value
        if v is None: return None
        sp   = abs(a.meta.get("surprise_pct", 0))
        bp   = self.cfg.m14_band_k * max(sp, 0.01)
        up   = a.anchor_price * (1 + bp)
        lo   = a.anchor_price * (1 - bp)
        p    = c.close
        if   p < lo: d="LONG";  st=min(1.0,(lo-p)/(lo*bp)*.5)
        elif p > up: d="SHORT"; st=min(1.0,(p-up)/(up*bp)*.5)
        elif abs(p-v)/v < 0.005: d="NEUTRAL"; st=0.4
        else: return None
        return Signal(ts=c.ts, symbol=sym, motor_id=14,
            direction=d, strength=st, avwap_value=v,
            band_upper=up, band_lower=lo,
            rationale=(f"M14 ancla={a.anchor_price:.2f} "
                       f"surprise={a.meta['surprise_pct']:+.2%} avwap={v:.2f}"),
            meta={"anchor_ts": a.anchor_ts.isoformat(),
                  "surprise_pct": a.meta["surprise_pct"]})

    def new_session(self, sym: str) -> None:
        for a in self._anchors[sym]:
            if not a.active: continue
            a.sessions_alive += 1
            lim = a.meta.get("persist_sessions", self.cfg.m14_persist_sessions)
            if a.sessions_alive >= lim:
                a.active = False
                logger.info(f"[M14] {sym}: ancla expirada tras {a.sessions_alive} sesiones")
        self._anchors[sym] = [a for a in self._anchors[sym] if a.active]


# ─────────────────────────────────────────────────────────────
# 7. MOTOR 15 — AVWAP INSTITUCIONAL 13F
# ─────────────────────────────────────────────────────────────

class Motor15Institutional13F:
    """
    Ancla en precio promedio de entrada de los top-N fondos institucionales.
    Peso por fondo = shares_held / rank.
    Validación cruzada con OI walls de opciones.

    Señal activa cuando:
      precio ≈ AVWAP (±1%) AND hay OI wall en ±2% del precio.
    Strength 0.75 con confirmación de OI wall, 0.45 sin ella.

    Calibración:
      m15_top_n: 3 (PLTR muy concentrado) → 10 (AAPL/MSFT diversificados)
      m15_oi_wall_mult: 1.2 si liquidez de opciones es baja
      Actualizar semanalmente (datos 13F son trimestrales)
    """

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._anchors : Dict[str, Optional[AVWAPState]] = {s:None for s in SYMBOLS}
        self._oi_walls: Dict[str, List[float]]          = {s:[]   for s in SYMBOLS}

    async def initialize(self, sym: str) -> Optional[AVWAPState]:
        try:
            holders = await self.fmp.get_institutional_holders(sym)
        except Exception as e:
            logger.warning(f"[M15] {sym}: FMP error {e}"); return None
        if not holders: return None

        top = sorted(holders,
                     key=lambda h: float(h.get("shares",0) or 0),
                     reverse=True)[:self.cfg.m15_top_n]

        tw = 0.0; wp = 0.0
        last_date = datetime.now(tz=timezone.utc) - timedelta(days=90)

        for rank, h in enumerate(top, 1):
            shares = float(h.get("shares",0) or 0)
            if shares < 1: continue
            price  = float(h.get("avgCost", 0) or 0)
            w      = shares / rank
            wp    += price * w; tw += w
            ds     = h.get("dateReported","")
            if ds:
                try:
                    fd = datetime.fromisoformat(ds.split("T")[0]).replace(tzinfo=timezone.utc)
                    if fd > last_date: last_date = fd
                except ValueError: pass

        if tw == 0: return None
        ap = (wp / tw) if wp > 0 else 0.0
        if ap == 0.0:
            cs = await self.fmp.get_intraday(sym, "1min", 5)
            ap = cs[-1].close if cs else 0.0

        state = AVWAPState(
            symbol=sym, anchor_ts=last_date, anchor_price=ap,
            motor_id=15, motor_name="AVWAP 13F",
            meta={"top_holders": [h.get("holder","") for h in top]})
        self._anchors[sym] = state

        try:
            opts = await self.fmp.get_options(sym)
            self._oi_walls[sym] = self.oa.detect_oi_walls(
                self.oa.oi_by_strike(opts), self.cfg.m15_oi_wall_mult)
        except Exception: pass

        logger.info(f"[M15] {sym}: ancla 13F @ {ap:.2f} "
                    f"filing={last_date.date()} walls={self._oi_walls[sym][:4]}")
        return state

    def update(self, sym: str, c: Candle) -> Optional[Signal]:
        a = self._anchors[sym]
        if a is None or not a.active: return None
        a.update(c)
        v = a.value
        if v is None: return None
        p = c.close
        if abs(p - v) / v > 0.01: return None  # fuera del ±1%

        walls     = self._oi_walls[sym]
        wall_near = any(abs(p - w) / p < 0.02 for w in walls)
        strength  = 0.75 if wall_near else 0.45
        up, lo    = a.band(1.0)

        return Signal(ts=c.ts, symbol=sym, motor_id=15,
            direction="LONG" if p >= v else "NEUTRAL",
            strength=strength, avwap_value=v, band_upper=up, band_lower=lo,
            rationale=(f"M15 ancla={a.anchor_price:.2f} avwap={v:.2f} "
                       f"precio={p:.2f} OI-wall={wall_near}"),
            meta={"oi_walls": walls[:5], "wall_confirms": wall_near})


# ─────────────────────────────────────────────────────────────
# 8. MOTOR 16 — AVWAP MACRO EVENT
# ─────────────────────────────────────────────────────────────

class Motor16MacroEvent:
    """
    Protocolo 3 fases para CPI, Fed Decision, NFP, PCE:

      SILENCIO  (−m16_silence_min antes del evento)
        → Signal FREEZE; el CME cierra posiciones.

      RE-ANCLA  (0–m16_reanchor_wait_min post-evento)
        → Crear nuevo AVWAPState en precio de apertura post-evento.

      CONSOLIDACIÓN  (hasta 1 h post-evento)
        → AVWAP activo; banda = IV_actual × m16_iv_band_mult.

    Calibración:
      m16_silence_min: 30 (CPI/Fed) / 15 (NFP) / 5 (datos menores)
      m16_iv_band_mult: 0.3 (mercado tranquilo) / 0.7 (alta volatilidad)
      m16_min_importance: 3 (default) / 4 (solo eventos mayores)
    """

    from enum import Enum
    class Phase(Enum):
        NORMAL="normal"; SILENCIO="silencio"; REANCLA="reancla"; CONSOLID="consolid"

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._anchors: Dict[str, List[AVWAPState]] = {s:[] for s in SYMBOLS}
        self._events : List[Dict] = []

    async def load_events_for_today(self) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            evs = await self.fmp.get_economic_calendar(today, today)
            self._events = [e for e in evs
                            if int(e.get("importance",0) or 0) >= self.cfg.m16_min_importance]
            logger.info(f"[M16] {len(self._events)} eventos macro cargados")
        except Exception as e:
            logger.warning(f"[M16] error cargando calendario: {e}")

    def _phase(self, now: datetime) -> "Motor16MacroEvent.Phase":
        from enum import Enum
        for ev in self._events:
            try:
                ev_ts = datetime.fromisoformat(
                    ev.get("date","").replace("Z","+00:00")).replace(tzinfo=timezone.utc)
            except ValueError: continue
            ss = ev_ts - timedelta(minutes=self.cfg.m16_silence_min)
            re = ev_ts + timedelta(minutes=self.cfg.m16_reanchor_wait_min)
            co = ev_ts + timedelta(hours=1)
            if ss <= now < ev_ts: return Motor16MacroEvent.Phase.SILENCIO
            if ev_ts <= now < re: return Motor16MacroEvent.Phase.REANCLA
            if re <= now < co:    return Motor16MacroEvent.Phase.CONSOLID
        return Motor16MacroEvent.Phase.NORMAL

    async def maybe_create_anchor(self, sym: str, now: datetime,
                                  price: float) -> Optional[AVWAPState]:
        if self._phase(now) != Motor16MacroEvent.Phase.REANCLA: return None
        if any((now - a.anchor_ts).total_seconds() < 900 for a in self._anchors[sym]):
            return None
        ev = max((e for e in self._events
                  if _parse_dt(e.get("date","")) is not None
                  and _parse_dt(e.get("date","")) <= now),
                 key=lambda e: e.get("date",""), default=None)
        if not ev: return None
        state = AVWAPState(
            symbol=sym, anchor_ts=now, anchor_price=price,
            motor_id=16, motor_name="AVWAP Macro",
            meta={"event_name": ev.get("event",""),
                  "importance" : ev.get("importance",0),
                  "actual"     : ev.get("actual",""),
                  "estimate"   : ev.get("estimate","")})
        self._anchors[sym].append(state)
        logger.info(f"[M16] {sym}: ancla [{ev.get('event','')}] @ {price:.2f}")
        return state

    def update(self, sym: str, c: Candle,
               opts: List[OptionGreeks]) -> Optional[Signal]:
        now   = c.ts
        phase = self._phase(now)

        if phase == Motor16MacroEvent.Phase.SILENCIO:
            return Signal(ts=now, symbol=sym, motor_id=16,
                direction="FREEZE", strength=1.0,
                avwap_value=None, band_upper=None, band_lower=None,
                rationale="M16: SILENCIO pre-evento — congelar todo")

        iv = self.oa.atm_iv(opts, c.close)
        for a in self._anchors[sym]:
            if not a.active: continue
            a.update(c)
            v = a.value
            if v is None: continue
            bh = max(iv * self.cfg.m16_iv_band_mult * c.close / 16.0,
                     c.close * 0.005)
            up = v + bh; lo = v - bh; p = c.close
            if   p < lo: d="LONG";  st=min(1.0,(lo-p)/bh*.8)
            elif p > up: d="SHORT"; st=min(1.0,(p-up)/bh*.8)
            else: continue
            return Signal(ts=now, symbol=sym, motor_id=16,
                direction=d, strength=st, avwap_value=v,
                band_upper=up, band_lower=lo,
                rationale=(f"M16 [{a.meta.get('event_name','')}] "
                           f"IV={iv:.2f} bh={bh:.2f} precio={p:.2f}"),
                meta={"event": a.meta.get("event_name",""), "iv": iv})
        return None

    def is_in_silence(self, now: datetime) -> bool:
        return self._phase(now) == Motor16MacroEvent.Phase.SILENCIO


# ─────────────────────────────────────────────────────────────
# 9. MOTOR 17 — AVWAP SMART MONEY
# ─────────────────────────────────────────────────────────────

class Motor17SmartMoney:
    """
    Ancla en fecha exacta de compras significativas de insiders / congresistas.
    Confirmación obligatoria: delta neto de opciones ≥ m17_delta_threshold.

    Fuentes FMP:
      /v4/insider-trading  (directivos, ejecutivos)
      /v4/senate-trading   (Senado EE.UU.)
      /v4/house-disclosure (Cámara de Representantes)

    Señal LONG cuando precio cotiza por DEBAJO del AVWAP Smart Money
    y el delta confirma sesgo alcista → precio irá a recuperar nivel smart money.

    Calibración:
      m17_insider_min_usd: 100k (PLTR con menos volumen insider) / 1M (AAPL)
      m17_delta_threshold: 0.4 (menos restrictivo) / 0.7 (muy restrictivo)
      m17_lookback_days: 15 (mercados rápidos) / 60 (tickers menos activos)
    """

    BUY_KWS = {"buy","purchase","p-purchase","p - purchase"}

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._anchors: Dict[str, List[AVWAPState]] = {s:[] for s in SYMBOLS}

    async def scan_and_anchor(self, sym: str,
                              opts: List[OptionGreeks]) -> List[AVWAPState]:
        cutoff     = datetime.now(tz=timezone.utc) - timedelta(days=self.cfg.m17_lookback_days)
        delta_bias = self.oa.net_delta_bias(opts)
        new_anch   = []

        sources = [
            ("insider", self.fmp.get_insider_trades,  self.cfg.m17_insider_min_usd,
             "securitiesTransacted", "price", "transactionDate", "reportingName"),
            ("senate",  self.fmp.get_senate_trades,   self.cfg.m17_congress_min_usd,
             None, None, "transactionDate", "representative"),
            ("house",   self.fmp.get_house_trades,    self.cfg.m17_congress_min_usd,
             None, None, "transactionDate", "representative"),
        ]

        for (src, getter, min_usd, sf, pf, df, nf) in sources:
            try:
                trades = await getter(sym)
            except Exception as e:
                logger.warning(f"[M17] {sym} {src}: {e}"); continue
            for t in trades:
                tx = t.get("transactionType","").lower().replace(" ","")
                if not any(kw in tx for kw in self.BUY_KWS): continue
                amount = (float(t.get(sf,0) or 0) * float(t.get(pf,0) or 0)
                          if sf and pf else _parse_amount(t.get("amount","")))
                if amount < min_usd: continue
                ts = _parse_dt(t.get(df,""))
                if ts is None or ts < cutoff: continue
                name  = t.get(nf,"")
                price = float(t.get(pf or "price",0) or 0) if pf else 0.0
                a     = self._create_if_new(sym, ts, price, src, name, amount)
                if a:
                    a.meta["delta_bias"]     = delta_bias
                    a.meta["delta_confirms"] = delta_bias >= self.cfg.m17_delta_threshold
                    new_anch.append(a)

        if new_anch:
            logger.info(f"[M17] {sym}: {len(new_anch)} anclas smart money | "
                        f"delta_bias={delta_bias:.3f} "
                        f"confirma={delta_bias>=self.cfg.m17_delta_threshold}")
        return new_anch

    def _create_if_new(self, sym, ts, price, src, name, amt) -> Optional[AVWAPState]:
        if any(abs((a.anchor_ts - ts).total_seconds()) < 86400
               and a.meta.get("source") == src and a.meta.get("name") == name
               for a in self._anchors[sym]):
            return None
        s = AVWAPState(symbol=sym, anchor_ts=ts, anchor_price=price,
                       motor_id=17, motor_name="AVWAP Smart Money",
                       meta={"source":src,"name":name,"amount_usd":amt})
        self._anchors[sym].append(s)
        return s

    def update(self, sym: str, c: Candle) -> List[Signal]:
        sigs = []
        for a in self._anchors[sym]:
            if not a.active: continue
            if a.anchor_price == 0.0: a.anchor_price = c.close
            a.update(c)
            v = a.value
            if v is None: continue
            if not a.meta.get("delta_confirms", False): continue
            p = c.close
            if p >= v: continue  # precio ya encima → sin señal LONG
            st_ = min(1.0, (v - p) / v * 20)
            up, lo = a.band(1.5)
            sigs.append(Signal(ts=c.ts, symbol=sym, motor_id=17,
                direction="LONG", strength=st_, avwap_value=v,
                band_upper=up, band_lower=lo,
                rationale=(f"M17 [{a.meta.get('source','')} "
                           f"{a.meta.get('name','')}] "
                           f"ancla={a.anchor_price:.2f} avwap={v:.2f} "
                           f"delta={a.meta.get('delta_bias',0):.3f}"),
                meta=a.meta.copy()))
        return sigs


# ─────────────────────────────────────────────────────────────
# 10. MOTOR 18 — AVWAP NEWS CATALYST
# ─────────────────────────────────────────────────────────────

class Motor18NewsCatalyst:
    """
    Ancla en press-releases y noticias de alta relevancia.

    Scoring (0–1.0):
      +0.30  press release oficial de la compañía
      +0.20  keyword de alta relevancia corporativa en el título
      +0.15  spike de IV ≥ m18_iv_spike_min (confirmación opciones)
      +0.15  símbolo mencioando en el título
      +0.10  fuente premium (reuters, bloomberg, wsj, ft, cnbc, barrons)
      +0.10  noticia publicada hace < 2 horas

    Solo ancla si score ≥ m18_relevance_min.
    Señal cuando precio se aleja > 2% del AVWAP tras la noticia.

    Calibración:
      m18_relevance_min: 0.50 (más señales) / 0.75 (solo señales limpias)
      m18_wait_min: 3 (scalping) / 10 (swing)
      m18_max_active: 2 (PLTR) / 4 (AAPL, MSFT)
    """

    HRK = {"acquisition","merger","fda","approval","partnership","guidance",
           "revenue","forecast","ceo","dividend","buyback","spinoff","recall",
           "lawsuit","settlement","contract","deal","beat","miss",
           "upgrade","downgrade","investigation","bankruptcy"}
    PREM = {"reuters","bloomberg","wsj","ft","cnbc","barrons","marketwatch"}

    def __init__(self, cfg: Config, fmp: AVWAPFMPClient, oa: OptionsAnalyzer):
        self.cfg = cfg; self.fmp = fmp; self.oa = oa
        self._anchors : Dict[str, List[AVWAPState]] = {s:[] for s in SYMBOLS}
        self._prev_iv : Dict[str, float] = {s:0.0 for s in SYMBOLS}

    async def scan_news(self, sym: str, price: float,
                        opts: List[OptionGreeks]) -> List[AVWAPState]:
        now      = datetime.now(tz=timezone.utc)
        iv_now   = self.oa.atm_iv(opts, price)
        iv_spike = ((iv_now - self._prev_iv[sym]) / max(self._prev_iv[sym],0.01)
                    if self._prev_iv[sym] > 0 else 0.0)
        self._prev_iv[sym] = iv_now
        new_anch = []
        items    = []

        try:
            for n in await self.fmp.get_news(sym, 15):
                items.append({"text":n.get("title",""),
                              "ts":n.get("publishedDate",""),
                              "source":n.get("site","").lower(),"is_pr":False})
        except Exception: pass
        try:
            for n in await self.fmp.get_press_releases(sym):
                items.append({"text":n.get("title",""),
                              "ts":n.get("date",""),
                              "source":"press-release","is_pr":True})
        except Exception: pass

        for item in items:
            ts = _parse_dt(item["ts"])
            if ts is None: continue
            age = (now - ts).total_seconds() / 3600
            if age > 24: continue
            sc = self._score(item, age, iv_spike, sym)
            if sc < self.cfg.m18_relevance_min: continue
            if any(abs((a.anchor_ts - ts).total_seconds()) < 600
                   for a in self._anchors[sym]): continue

            # Respetar límite de anclas activas
            active = [a for a in self._anchors[sym] if a.active]
            if len(active) >= self.cfg.m18_max_active:
                min(active, key=lambda a: a.anchor_ts).active = False

            a = AVWAPState(
                symbol=sym, anchor_ts=ts, anchor_price=price,
                motor_id=18, motor_name="AVWAP News",
                meta={"headline":item["text"][:120],"source":item["source"],
                      "relevance_score":round(sc,3),"iv_spike":round(iv_spike,4),
                      "is_press_release":item["is_pr"],"wait_min":self.cfg.m18_wait_min})
            self._anchors[sym].append(a)
            new_anch.append(a)
            logger.info(f"[M18] {sym}: ancla score={sc:.2f} '{item['text'][:60]}'")
        return new_anch

    def _score(self, item, age, iv_spike, sym) -> float:
        sc   = 0.0
        text = (item["text"] or "").lower()
        if item["is_pr"]:                                    sc += 0.30
        if any(k in text for k in self.HRK):                sc += 0.20
        if iv_spike >= self.cfg.m18_iv_spike_min:            sc += 0.15
        if sym.lower() in text:                              sc += 0.15
        if any(p in item["source"] for p in self.PREM):     sc += 0.10
        if age < 2:                                          sc += 0.10
        return min(1.0, sc)

    def update(self, sym: str, c: Candle) -> List[Signal]:
        sigs = []
        wait = timedelta(minutes=self.cfg.m18_wait_min)
        for a in self._anchors[sym]:
            if not a.active: continue
            if (c.ts - a.anchor_ts) < wait: continue
            a.update(c)
            v = a.value
            if v is None: continue
            p = c.close; dist = abs(p - v) / v
            if dist < 0.02: continue
            d  = "LONG" if p < v else "SHORT"
            st = min(1.0, dist / 0.02 * 0.6)
            up, lo = a.band(1.0)
            sigs.append(Signal(ts=c.ts, symbol=sym, motor_id=18,
                direction=d, strength=st, avwap_value=v,
                band_upper=up, band_lower=lo,
                rationale=(f"M18 [{a.meta.get('headline','')[:40]}] "
                           f"score={a.meta.get('relevance_score',0):.2f} "
                           f"avwap={v:.2f} precio={p:.2f}"),
                meta={"headline":a.meta.get("headline",""),
                      "relevance_score":a.meta.get("relevance_score",0)}))
        return sigs
