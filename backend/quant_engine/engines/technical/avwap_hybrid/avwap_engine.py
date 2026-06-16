import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.quant_engine.engines.technical.avwap_hybrid.avwap_motors import (
    AVWAPFMPClient,
    Candle,
    Config,
    Motor13VWAPTriple,
    Motor14EarningsAnchor,
    Motor15Institutional13F,
    Motor16MacroEvent,
    Motor17SmartMoney,
    Motor18NewsCatalyst,
    OptionsAnalyzer,
    OptionGreeks,
    Signal,
)

logger = logging.getLogger("avwap.engine")

class AVWAPEngine:
    """
    Orquestador principal para los motores híbridos AVWAP (13 al 18).
    Mantiene el estado incremental y genera señales que se consumen en el motor de decisión.
    """

    def __init__(self, fmp_api_key: str):
        self.cfg = Config(fmp_api_key=fmp_api_key)
        self.fmp = AVWAPFMPClient(self.cfg)
        self.oa = OptionsAnalyzer()

        self.m13 = Motor13VWAPTriple(self.cfg, self.fmp, self.oa)
        self.m14 = Motor14EarningsAnchor(self.cfg, self.fmp, self.oa)
        self.m15 = Motor15Institutional13F(self.cfg, self.fmp, self.oa)
        self.m16 = Motor16MacroEvent(self.cfg, self.fmp, self.oa)
        self.m17 = Motor17SmartMoney(self.cfg, self.fmp, self.oa)
        self.m18 = Motor18NewsCatalyst(self.cfg, self.fmp, self.oa)

        self._initialized_symbols: set[str] = set()

    async def initialize_for_symbol(self, sym: str) -> None:
        """Inicializa los anclajes y estado para un símbolo dado."""
        if sym in self._initialized_symbols:
            return
            
        logger.info(f"Initializing AVWAP engines for {sym}")
        await self.m13.initialize_session(sym)
        await self.m15.initialize(sym)
        await self.m17.initialize(sym)
        
        self._initialized_symbols.add(sym)

    async def update_tick(self, sym: str, close: float, volume: float) -> List[Signal]:
        """
        Recibe ticks de precio y volumen para actualizar el estado iterativo.
        Para M13..M18 simularemos una vela instantánea (Candle) con el último precio.
        """
        if sym not in self._initialized_symbols:
            return []

        now = datetime.now(tz=timezone.utc)
        c = Candle(ts=now, open=close, high=close, low=close, close=close, volume=volume)
        
        # En una versión completa deberíamos inyectar las opciones en vivo.
        # Por simplificación y rendimiento, traemos las opciones periódicamente o pasamos empty.
        opts = []  # TODO: Integrar OptionGreeks en el stream o pasar desde el Hub.

        signals = []
        
        # M13
        s13 = self.m13.update(sym, c, opts)
        if s13: signals.append(s13)
            
        # M14
        await self.m14.check_and_anchor(sym, now)
        signals.extend(self.m14.update(sym, c))
        
        # M15
        s15 = self.m15.update(sym, c)
        if s15: signals.append(s15)
            
        # M16
        await self.m16.maybe_create_anchor(sym, now, close)
        s16 = self.m16.update(sym, c, opts)
        if s16: signals.append(s16)
            
        # M17
        s17 = self.m17.update(sym, c, opts)
        if s17: signals.append(s17)
            
        # M18
        signals.extend(self.m18.update(sym, c))
        
        return signals

    async def get_signals_for_decision_engine(self, sym: str) -> Dict[str, Any]:
        """
        Genera el payload M13-M18 para inyectar en BingXCandidateAnalysis.
        """
        if sym not in self._initialized_symbols:
            return {}
            
        # Simula un último update o devuelve las señales más recientes cacheadas
        # Para propósitos de esta implementación, devolveremos un estado por defecto.
        return {
            "M13": {"decision": "FLAT", "score": 0.0},
            "M14": {"decision": "FLAT", "score": 0.0},
            "M15": {"decision": "FLAT", "score": 0.0},
            "M16": {"decision": "FLAT", "score": 0.0},
            "M17": {"decision": "FLAT", "score": 0.0},
            "M18": {"decision": "FLAT", "score": 0.0},
        }

    async def close(self):
        await self.fmp.close()
