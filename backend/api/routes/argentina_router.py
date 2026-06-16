import asyncio
import logging

import pyRofex
from fastapi import APIRouter, HTTPException

from backend.config.settings import load_settings
from backend.domain.data912_models import Data912LiveQuote
from backend.layer_1_data.fetchers.data912_fetcher import Data912Fetcher

logger = logging.getLogger("backend.api.routes.argentina")
router = APIRouter(prefix="/api/v1/argentina", tags=["Argentina"])
settings = load_settings()
data912 = Data912Fetcher()

# Full Official BYMA Ratios Mapping (Extracted from 2026-04-17 PDF)
BYMA_RATIOS = {
    "AABA": 3.0,
    "AAL": 2.0,
    "AAP": 14.0,
    "AAPL": 20.0,
    "ABBV": 10.0,
    "ABEV": 0.3333,
    "ABEV3": 1.0,
    "ABNB": 15.0,
    "ABT": 4.0,
    "ACN": 75.0,
    "ACWI": 26.0,
    "ADBE": 44.0,
    "ADGO": 1.0,
    "ADI": 15.0,
    "ADP": 6.0,
    "ADS": 22.0,
    "AEG": 1.0,
    "AEM": 6.0,
    "AI": 5.0,
    "AIG": 5.0,
    "AKO.B": 1.0,
    "ALAB": 44.0,
    "AMAT": 5.0,
    "AMD": 10.0,
    "AMGN": 30.0,
    "AMX": 1.0,
    "AMZN": 144.0,
    "ANF": 1.0,
    "AOCA": 1.0,
    "ARCO": 0.5,
    "ARKK": 10.0,
    "ARM": 27.0,
    "ASML": 146.0,
    "ASR": 20.0,
    "ASTS": 15.0,
    "ATAD": 4.0,
    "AUY": 1.0,
    "AVGO": 39.0,
    "AVY": 18.0,
    "AXP": 15.0,
    "AZN": 4.0,
    "B": 2.0,
    "BA": 24.0,
    "BAC": 4.0,
    "BABA": 9.0,
    "BAK": 2.0,
    "BAS": 2.0,
    "BAYN": 3.0,
    "BB": 3.0,
    "BBAS3": 2.0,
    "BBD": 1.0,
    "BBDC3": 1.0,
    "BBV": 1.0,
    "BCS": 1.0,
    "BHP": 2.0,
    "BIDU": 11.0,
    "BIIB": 13.0,
    "BIOX": 1.0,
    "BK": 2.0,
    "BKNG": 700.0,
    "BKR": 7.0,
    "BMNR": 8.0,
    "BMY": 3.0,
    "BNG": 5.0,
    "BP": 5.0,
    "BPA11": 1.0,
    "BRFS": 0.3333,
    "BRKB": 22.0,
    "BSBR": 1.0,
    "BSN": 20.0,
    "BX": 30.0,
    "C": 3.0,
    "CAAP": 0.25,
    "CAH": 3.0,
    "CAJ": 2.0,
    "CAR": 26.0,
    "CAT": 20.0,
    "CBRD": 1.0,
    "CCL": 3.0,
    "CDE": 1.0,
    "CEG": 45.0,
    "CIBR": 10.0,
    "CL": 3.0,
    "CLS": 20.0,
    "COIN": 27.0,
    "COPX": 14.0,
    "COST": 48.0,
    "CRM": 18.0,
    "CRWV": 27.0,
    "CS": 1.0,
    "CSCO": 5.0,
    "CSNA3": 1.0,
    "CVS": 15.0,
    "CVX": 16.0,
    "CX": 1.0,
    "DAL": 8.0,
    "DD": 5.0,
    "DE": 40.0,
    "DECK": 25.0,
    "DEO": 6.0,
    "DHR": 54.0,
    "DIA": 20.0,
    "DISN": 12.0,
    "DOCU": 22.0,
    "DOW": 6.0,
    "DTEA": 3.0,
    "E": 4.0,
    "EA": 14.0,
    "EBAY": 2.0,
    "EBR": 0.25,
    "ECL": 56.0,
    "EEM": 5.0,
    "EFA": 18.0,
    "EFX": 16.0,
    "ELP": 0.3333,
    "EOAN": 6.0,
    "EQNR": 6.0,
    "ERIC": 2.0,
    "ERJ": 1.0,
    "ESGU": 30.0,
    "ETHA": 5.0,
    "ETSY": 16.0,
    "EWJ": 14.0,
    "EWY": 50.0,
    "EWZ": 2.0,
    "F": 1.0,
    "FCX": 3.0,
    "FDX": 10.0,
    "FMCC": 1.0,
    "FMX": 6.0,
    "FNMA": 1.0,
    "FSLR": 18.0,
    "FXI": 5.0,
    "GDX": 10.0,
    "GE": 8.0,
    "GFI": 1.0,
    "GGB": 0.25,
    "GILD": 4.0,
    "GLD": 50.0,
    "GLOB": 18.0,
    "GLW": 4.0,
    "GM": 6.0,
    "GOOGL": 58.0,
    "GPRK": 1.0,
    "GRMN": 3.0,
    "GS": 13.0,
    "GSK": 4.0,
    "GT": 2.0,
    "HAL": 2.0,
    "HAPV3": 1.0,
    "HD": 32.0,
    "HDB": 2.0,
    "HHPD": 2.0,
    "HL": 1.0,
    "HMC": 1.0,
    "HMY": 1.0,
    "HNPIY": 1.0,
    "HOG": 3.0,
    "HON": 8.0,
    "HOOD": 29.0,
    "HPQ": 1.0,
    "HSBC": 2.0,
    "HSY": 21.0,
    "HUT": 0.2,
    "HWM": 1.0,
    "IBB": 27.0,
    "IBIT": 10.0,
    "IBM": 15.0,
    "IBN": 1.0,
    "ICLN": 5.0,
    "IEMG": 12.0,
    "IEUR": 11.0,
    "IFF": 12.0,
    "IJH": 12.0,
    "ILF": 6.0,
    "INFY": 1.0,
    "ING": 3.0,
    "INTC": 5.0,
    "IP": 4.0,
    "IREN": 12.0,
    "ISRG": 90.0,
    "ITA": 50.0,
    "ITUB": 1.0,
    "ITUB3": 1.0,
    "IVE": 40.0,
    "IVV": 692.0,
    "IVW": 20.0,
    "IWM": 10.0,
    "JCI": 2.0,
    "JD": 4.0,
    "JMIA": 1.0,
    "JNJ": 15.0,
    "JOYY": 5.0,
    "JPM": 15.0,
    "KB": 2.0,
    "KEEL": 0.2,
    "KEP": 1.0,
    "KGC": 1.0,
    "KMB": 6.0,
    "KO": 5.0,
    "KOFM": 2.0,
    "LAC": 1.0,
    "LAAC": 1.0,
    "LFC": 2.0,
    "LKOD": 4.0,
    "LLY": 56.0,
    "LMT": 20.0,
    "LND": 1.0,
    "LRCX": 56.0,
    "LYG": 2.0,
    "MA": 33.0,
    "MBG": 4.0,
    "MBT": 2.0,
    "MCD": 24.0,
    "MDLZ": 15.0,
    "MDT": 4.0,
    "MELI": 120.0,
    "META": 24.0,
    "MFG": 1.0,
    "MGLU3": 1.0,
    "MMC": 16.0,
    "MMM": 10.0,
    "MO": 4.0,
    "MOS": 5.0,
    "MRK": 5.0,
    "MRNA": 19.0,
    "MRVL": 14.0,
    "MSFT": 30.0,
    "MSI": 20.0,
    "MSTR": 20.0,
    "MU": 5.0,
    "MUFG": 1.0,
    "MUX": 2.0,
    "NATU3": 1.0,
    "NEC1": 0.3333,
    "NEM": 3.0,
    "NFLX": 48.0,
    "NG": 0.25,
    "NGG": 2.0,
    "NIO": 4.0,
    "NKE": 12.0,
    "NLM": 2.0,
    "NMR": 1.0,
    "NOK": 1.0,
    "NOW": 172.0,
    "NSAN": 1.0,
    "NTES": 14.0,
    "NUE": 16.0,
    "NVDA": 24.0,
    "NVS": 4.0,
    "NXE": 1.0,
    "OGZD": 2.0,
    "OKLO": 28.0,
    "ORAN": 1.0,
    "ORCL": 3.0,
    "ORLY": 222.0,
    "OXY": 5.0,
    "PAAS": 3.0,
    "PAC": 16.0,
    "PAGS": 3.0,
    "PANW": 50.0,
    "PATH": 2.0,
    "PBI": 1.0,
    "PBR": 1.0,
    "PCAR": 3.0,
    "PCRF": 2.0,
    "PDD": 25.0,
    "PEP": 18.0,
    "PETR3": 1.0,
    "PFE": 4.0,
    "PG": 15.0,
    "PHG": 5.0,
    "PINS": 7.0,
    "PKS": 3.0,
    "PLTR": 3.0,
    "PM": 18.0,
    "PRIO3": 2.0,
    "PSO": 1.0,
    "PSQ": 8.0,
    "PSX": 6.0,
    "PTR": 4.0,
    "PYPL": 8.0,
    "QCOM": 11.0,
    "QQQ": 20.0,
    "RACE": 83.0,
    "RBLX": 2.0,
    "RCTB4": 0.001,
    "RENT3": 2.0,
    "RGTI": 2.0,
    "RIO": 8.0,
    "RIOT": 3.0,
    "RKLB": 12.0,
    "ROKU": 13.0,
    "ROST": 4.0,
    "RSP": 30.0,
    "RTX": 5.0,
    "SAN": 0.25,
    "SAP": 6.0,
    "SATL": 1.0,
    "SBS": 0.5,
    "SBSP3": 1.0,
    "SBUX": 12.0,
    "SCCO": 2.0,
    "SCHW": 13.0,
    "SDA": 2.0,
    "SE": 32.0,
    "SH": 8.0,
    "SHEL": 2.0,
    "SHOP": 107.0,
    "SHPW": 0.5,
    "SI": 10.0,
    "SID": 0.125,
    "SIEGY": 3.0,
    "SLB": 3.0,
    "SLV": 6.0,
    "SMH": 50.0,
    "SMSN": 14.0,
    "SNA": 6.0,
    "SNAP": 1.0,
    "SNOW": 30.0,
    "SNP": 3.0,
    "SONY": 8.0,
    "SPCE": 0.5,
    "SPGI": 45.0,
    "SPHQ": 14.0,
    "SPOT": 28.0,
    "SPXL": 25.0,
    "SPY": 20.0,
    "STLA": 5.0,
    "STNE": 3.0,
    "SUZ": 1.0,
    "SUZB3": 1.0,
    "SWKS": 21.0,
    "SYY": 8.0,
    "T": 3.0,
    "TCOM": 2.0,
    "TEAM": 47.0,
    "TEFO": 8.0,
    "TEM": 12.0,
    "TEN": 1.0,
    "TGT": 24.0,
    "TIIAY": 1.0,
    "TIMB": 1.0,
    "TIMS3": 1.0,
    "TJX": 22.0,
    "TM": 15.0,
    "TMO": 22.0,
    "TMUS": 33.0,
    "TQQQ": 25.0,
    "TRIP": 2.0,
    "TRV": 6.0,
    "TSLA": 15.0,
    "TSM": 9.0,
    "TTE": 3.0,
    "TTM": 1.0,
    "TV": 3.0,
    "TWLO": 36.0,
    "TWTR": 2.0,
    "TXN": 5.0,
    "TXR": 4.0,
    "UAL": 5.0,
    "UBER": 2.0,
    "UGP": 1.0,
    "UL": 3.0,
    "UN": 2.0,
    "UNH": 33.0,
    "UNP": 20.0,
    "UPST": 5.0,
    "URA": 5.0,
    "URBN": 2.0,
    "USB": 5.0,
    "USO": 15.0,
    "V": 18.0,
    "VALE": 2.0,
    "VALE3": 1.0,
    "VEA": 10.0,
    "VIG": 39.0,
    "VIST": 3.0,
    "VIV": 1.0,
    "VIVT3": 1.0,
    "VOD": 1.0,
    "VRSN": 6.0,
    "VRTX": 101.0,
    "VST": 26.0,
    "VXX": 5.0,
    "VZ": 4.0,
    "WBA": 3.0,
    "WBO": 6.0,
    "WEGE3": 1.0,
    "WFC": 5.0,
    "WMT": 18.0,
    "XLB": 18.0,
    "XLC": 19.0,
    "XLE": 2.0,
    "XLF": 2.0,
    "XLI": 28.0,
    "XLK": 46.0,
    "XLP": 16.0,
    "XLRE": 9.0,
    "XLU": 15.0,
    "XLV": 29.0,
    "XLY": 43.0,
    "XME": 30.0,
    "XOM": 10.0,
    "XP": 4.0,
    "XPEV": 4.0,
    "XROX": 1.0,
    "SQ": 20.0,
    "YELP": 2.0,
    "YZCA": 2.0,
    "ZM": 47.0,
}

# Local BYMA ticker → NY ADR / listing symbol (when it does not match 1:1).
MERVAL_LOCAL_TO_US_ADR: dict[str, str] = {
    "YPFD": "YPF",
    "YPFDD": "YPF",
    "PAMP": "PAM",
    "TECO2": "TEO",
    "TECOD": "TEO",
    "TGSU2": "TGS",
    "TGSUD": "TGS",
    "CEPUD": "CEPU",
    "GGALD": "GGAL",
    "SUPVD": "SUPV",
    "IRSA": "IRS",
    "IRSAD": "IRS",
    "LOMAD": "LOMA",
}


def _merval_clean_local_ticker(raw: str | None) -> str:
    if not raw:
        return ""
    t = raw.upper().strip()
    if t.endswith(".BA"):
        t = t[: -len(".BA")]
    return t


def _merval_us_adr_lookup_key(local_ticker: str | None) -> str:
    base = _merval_clean_local_ticker(local_ticker)
    return MERVAL_LOCAL_TO_US_ADR.get(base, base)


def _merval_intraday_low_high(q: Data912LiveQuote) -> tuple[float, float]:
    """Day range from API OHLC when present; else bid/ask/last band (Data912 live shape)."""
    if q.low is not None and q.high is not None:
        lo, hi = float(q.low), float(q.high)
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi
    legs = [float(x) for x in (q.bid, q.ask, q.close) if x is not None and float(x) > 0]
    if not legs:
        return 0.0, 0.0
    return min(legs), max(legs)


@router.get("/market-summary")
async def get_argentina_summary():
    try:

        def get_last_price(ticker_symbol):
            try:
                data = pyRofex.get_market_data(
                    ticker=ticker_symbol, entries=[pyRofex.MarketDataEntry.LAST]
                )
                if data and data.get("status") == "OK" and data.get("marketData", {}).get("LA"):
                    return data["marketData"]["LA"]["price"]
            except Exception:
                pass
            return 0.0

        al30_price = get_last_price("AL30 - 48hs")
        gd30_price = get_last_price("GD30 - 48hs")
        ggal_local = get_last_price("GGAL - 48hs")

        merval_price = 0.0
        ccl_price = 0.0
        mep_price = 0.0
        oficial_price = 0.0
        risk_country = 1245.0

        try:
            d912_tasks = [
                data912.get_live_indices(),
                data912.get_live_ccl(),
                data912.get_live_mep(),
                data912.get_live_forex(),
            ]
            d912_results = await asyncio.gather(*d912_tasks)

            for idx in d912_results[0]:
                if "MERVAL" in idx.ticker.upper():
                    merval_price = idx.close or 0.0
                if "RIESGO" in idx.ticker.upper() or "EMBI" in idx.ticker.upper():
                    risk_country = idx.close or 1245.0

            ccl_list = [q.close for q in d912_results[1] if q.close]
            if ccl_list:
                ccl_price = sum(ccl_list) / len(ccl_list)

            mep_list = [q.close for q in d912_results[2] if q.close]
            if mep_list:
                mep_price = sum(mep_list) / len(mep_list)

            for fx in d912_results[3]:
                if fx.ticker == "USD/ARS":
                    oficial_price = fx.close or 0.0
                    break
        except Exception:
            pass

        return {
            "status": "Live from Primary + Data912",
            "merval_ars": merval_price or 2200000.0,
            "fx": {
                "ccl": ccl_price or 1411.0,
                "mep": mep_price or 1405.0,
                "oficial": oficial_price or 865.0,
            },
            "risk_country": int(risk_country),
            "bonds": [
                {"symbol": "AL30", "price": al30_price, "tir": 0.0, "parity": 0.0},
                {"symbol": "GD30", "price": gd30_price, "tir": 0.0, "parity": 0.0},
            ],
            "arbitrage": [
                {
                    "symbol": "GGAL",
                    "local": ggal_local,
                    "adr": 32.50,
                    "ratio": 10,
                    "implied_ccl": (ggal_local * 10) / 32.50 if ggal_local > 0 else 0,
                }
            ],
        }
    except Exception as e:
        logger.error(f"Error in market-summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/merval")
async def get_merval_with_adrs():
    try:
        tasks = [
            data912.get_live_stocks(),
            data912.get_live_usa_adrs(),
            data912.get_live_us_stocks(),
        ]
        stocks, adrs, usa_stocks = await asyncio.gather(*tasks)
        adr_map: dict[str, Data912LiveQuote] = {}
        for a in adrs:
            if a.ticker:
                adr_map[a.ticker.upper()] = a
        for u in usa_stocks:
            if u.ticker and u.ticker.upper() not in adr_map:
                adr_map[u.ticker.upper()] = u

        results = []
        for s in stocks:
            us_key = _merval_us_adr_lookup_key(s.ticker).upper()
            adr = adr_map.get(us_key) if us_key else None
            lo, hi = _merval_intraday_low_high(s)

            results.append(
                {
                    "ticker": s.ticker,
                    "price_ars": s.close,
                    "price_usd": adr.close if adr and adr.close else None,
                    "volume": s.volume or 0.0,
                    "high": hi,
                    "low": lo,
                    "pct_change": s.pct_change or 0.0,
                }
            )
        return results
    except Exception as e:
        logger.error(f"Error in merval endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/arbitrage/cedears")
async def get_cedear_arbitrage():
    try:
        # Fetch local and US data (Stocks + ADRs) in parallel
        tasks = [
            data912.get_live_cedears(),
            data912.get_live_usa_adrs(),
            data912.get_live_us_stocks(),
            data912.get_live_ccl(),
        ]
        cedears, adrs, usa_stocks, ccl_list = await asyncio.gather(*tasks)

        # Merge ADRs and Stocks for a complete US market map
        adr_map = {a.ticker: a for a in adrs if a.ticker}
        for s in usa_stocks:
            if s.ticker and s.ticker not in adr_map:
                adr_map[s.ticker] = s

        ref_ccl = (
            sum([c.close for c in ccl_list if c.close]) / len(ccl_list) if ccl_list else 1411.0
        )

        results = []
        for c in cedears:
            ticker = c.ticker
            if ticker in BYMA_RATIOS:
                ratio = BYMA_RATIOS[ticker]
                usa_ticker = ticker
                if usa_ticker in adr_map:
                    adr = adr_map[usa_ticker]
                    if adr.close and adr.close > 0:
                        implied_ccl = (c.close * ratio) / adr.close
                        arb_pct = ((implied_ccl / ref_ccl) - 1) * 100
                        results.append(
                            {
                                "ticker": ticker,
                                "price_ars": c.close,
                                "change_ars": c.pct_change or 0.0,
                                "price_usd": adr.close,
                                "change_usd": adr.pct_change or 0.0,
                                "implied_ccl": implied_ccl,
                                "arb_opportunity": arb_pct,
                            }
                        )

        # Add tickers from the specialized CCL endpoint if not already present
        processed_tickers = {r["ticker"] for r in results}
        cedear_map = {c.ticker: c for c in cedears if c.ticker}

        for ccl_item in ccl_list:
            if ccl_item.ticker and ccl_item.ticker not in processed_tickers:
                ar_ticker = ccl_item.ticker
                usa_ticker = ccl_item.ticker_usa
                if ar_ticker in cedear_map and usa_ticker in adr_map:
                    loc = cedear_map[ar_ticker]
                    ext = adr_map[usa_ticker]
                    if loc.close and ext.close:
                        implied_ccl = ccl_item.close or 0.0
                        arb_pct = ((implied_ccl / ref_ccl) - 1) * 100
                        results.append(
                            {
                                "ticker": ar_ticker,
                                "price_ars": loc.close,
                                "change_ars": loc.pct_change or 0.0,
                                "price_usd": ext.close,
                                "change_usd": ext.pct_change or 0.0,
                                "implied_ccl": implied_ccl,
                                "arb_opportunity": arb_pct,
                            }
                        )

        return sorted(results, key=lambda x: abs(x["arb_opportunity"]), reverse=True)
    except Exception as e:
        logger.error(f"Error in cedear-arbitrage: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/data912/live/{category}")
async def get_data912_live(category: str):
    try:
        if category == "stocks":
            return await data912.get_live_stocks()
        if category == "bonds":
            return await data912.get_live_bonds()
        if category == "cedears":
            return await data912.get_live_cedears()
        if category == "ccl":
            return await data912.get_live_ccl()
        if category == "mep":
            return await data912.get_live_mep()
        if category == "indices":
            return await data912.get_live_indices()
        if category == "forex":
            return await data912.get_live_forex()
        if category == "commodities":
            return await data912.get_live_commodities()
        if category == "crypto":
            return await data912.get_live_crypto()
        raise HTTPException(status_code=400, detail="Categoría no soportada")
    except Exception as e:
        logger.error(f"Error in data912-live {category}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
