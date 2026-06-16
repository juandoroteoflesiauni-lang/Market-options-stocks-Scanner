from __future__ import annotations
"""Curated liquid symbol universes for the Market Scanner."""



def _dedupe(symbols: list[str]) -> list[str]:
    """Return uppercase symbols once, preserving curation order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw).upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


GENERAL = _dedupe(
    [
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "TSLA",
        "AMD",
        "JPM",
        "XOM",
        "LLY",
        "GLD",
        "USO",
        "IREN",
        "CRWV",
        "ONDS",
    ]
)

WALL_STREET = _dedupe(
    [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "META",
        "GOOGL",
        "GOOG",
        "TSLA",
        "AMD",
        "AVGO",
        "MU",
        "INTC",
        "PLTR",
        "ORCL",
        "MSTR",
        "TSM",
        "TXN",
        "CSCO",
        "QCOM",
        "AMAT",
        "LRCX",
        "KLAC",
        "ADI",
        "JPM",
        "BAC",
        "WFC",
        "C",
        "GS",
        "MS",
        "XOM",
        "CVX",
        "OXY",
        "SLB",
        "LLY",
        "UNH",
        "JNJ",
        "PFE",
        "MRK",
        "ABBV",
        "COST",
        "WMT",
        "HD",
        "NKE",
        "DIS",
        "NFLX",
        "ADBE",
        "CRM",
        "BA",
        "CAT",
        "GE",
        "V",
        "MA",
        "CCL",
    ]
)

MAGNIFICAS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]

FTMO_CORE = _dedupe(
    [
        "GOOGL",
        "AAPL",
        "TSLA",
        "XAUUSD",
        "XAGUSD",
        "US100.CASH",
        "BTC/USDT",
    ]
)

OPTIONS_LIQUID = _dedupe(
    [
        "SPY",
        "QQQ",
        "IWM",
        "TQQQ",
        "SQQQ",
        "GLD",
        "SLV",
        "USO",
        "SMH",
        "SOXL",
        "IBIT",
        "TLT",
        "EEM",
        "XLF",
        "HYG",
        "KRE",
        "ARKK",
        "GDX",
        "EWZ",
        "TSLA",
        "NVDA",
        "AMD",
        "MU",
        "AMZN",
        "META",
        "GOOGL",
        "GOOG",
        "INTC",
        "AAPL",
        "MSFT",
        "ORCL",
        "MSTR",
        "NFLX",
        "AVGO",
        "TSM",
        "PLTR",
        "TXN",
        "CVNA",
        "JPM",
        "BAC",
        "PFE",
        "WMT",
    ]
)

AI_INFRA_MOMENTUM = _dedupe(
    [
        "IREN",
        "CRWV",
        "ONDS",
        "NBIS",
        "ASTS",
        "RKLB",
        "SOUN",
        "IONQ",
        "RGTI",
        "QBTS",
        "PLTR",
        "SMCI",
        "VRT",
        "DELL",
        "HPE",
        "ANET",
        "CIEN",
        "LITE",
        "AAOI",
        "WDC",
        "STX",
        "SNDK",
        "MU",
        "AMD",
        "NVDA",
        "AVGO",
        "ARM",
        "TSM",
        "ASML",
        "AMAT",
        "LRCX",
        "KLAC",
        "TXN",
        "QCOM",
        "INTC",
        "MRVL",
        "ON",
        "MCHP",
        "CLS",
        "BE",
        "OKLO",
        "SMR",
    ]
)

HEALTHCARE = _dedupe(
    [
        "LLY",
        "NVO",
        "UNH",
        "JNJ",
        "ABBV",
        "MRK",
        "PFE",
        "BMY",
        "GILD",
        "AMGN",
        "REGN",
        "VRTX",
        "BIIB",
        "MRNA",
        "CVS",
        "HUM",
        "CI",
        "ELV",
        "HCA",
        "ISRG",
        "ABT",
        "TMO",
        "DHR",
        "SYK",
        "BSX",
        "MDT",
        "EW",
        "DXCM",
        "MCK",
        "CAH",
        "COR",
        "GEHC",
        "RMD",
        "ZBH",
    ]
)

ENERGY = _dedupe(
    [
        "XOM",
        "CVX",
        "COP",
        "EOG",
        "OXY",
        "SLB",
        "HAL",
        "BKR",
        "DVN",
        "FANG",
        "MPC",
        "VLO",
        "PSX",
        "LNG",
        "WMB",
        "KMI",
        "ET",
        "ENB",
        "TRP",
        "EQT",
        "XLE",
        "XOP",
        "OIH",
        "USO",
        "UNG",
        "CCJ",
        "UUUU",
        "DNN",
        "NXE",
        "URA",
        "URNM",
        "OKLO",
        "SMR",
        "FSLR",
        "ENPH",
        "RUN",
        "PLUG",
        "BE",
    ]
)

METALS_COMMODITIES = _dedupe(
    [
        "GLD",
        "IAU",
        "SLV",
        "GDX",
        "GDXJ",
        "XME",
        "COPX",
        "PICK",
        "SIL",
        "DBC",
        "DBA",
        "CPER",
        "PPLT",
        "BHP",
        "RIO",
        "VALE",
        "FCX",
        "SCCO",
        "TECK",
        "B",
        "NEM",
        "AEM",
        "KGC",
        "AU",
        "GFI",
        "PAAS",
        "HL",
        "AG",
        "CDE",
        "MP",
        "ALB",
        "SQM",
        "LAC",
        "PLL",
        "CCJ",
        "UUUU",
        "DNN",
        "NXE",
    ]
)

ADR_ARG = ["GGAL", "YPF", "PAM", "BMA", "SUPV", "TEO", "CEPU", "LOMA", "TGS"]

ARGENTINA_PLUS = _dedupe(
    [
        "MELI",
        "YPF",
        "GGAL",
        "PAM",
        "BMA",
        "TGS",
        "BBAR",
        "SUPV",
        "CEPU",
        "TEO",
        "LOMA",
        "VIST",
        "CRESY",
        "IRS",
        "EDN",
        "CAAP",
        "ARCO",
        "BIOX",
        "LAR",
    ]
)

ARG_LOCAL = _dedupe(
    [
        "GGAL",
        "YPFD",
        "PAMP",
        "ALUA",
        "TXAR",
        "COME",
        "MIRG",
        "CEPU",
        "TGSU2",
        "BYMA",
        "VALO",
        "TECO2",
        "BBAR",
        "EDN",
        "TRAN",
        "TGNO4",
        "CRES",
        "IRSA",
    ]
)

INDICES_ETFS = _dedupe(
    [
        "SPY",
        "QQQ",
        "DIA",
        "IWM",
        "VIXY",
        "VXX",
        "UVXY",
        "TQQQ",
        "SQQQ",
        "SOXL",
        "SMH",
        "XLF",
        "XLK",
        "XLE",
        "XLV",
        "XLY",
        "XLP",
        "XLI",
        "XLB",
        "XLU",
        "GLD",
        "SLV",
        "USO",
        "TLT",
        "HYG",
        "EEM",
        "EFA",
        "EWZ",
        "FXI",
        "GDX",
        "IYR",
        "KRE",
        "IBIT",
    ]
)

CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD"]

ALL_LIQUID = _dedupe(
    GENERAL
    + WALL_STREET
    + OPTIONS_LIQUID
    + AI_INFRA_MOMENTUM
    + HEALTHCARE
    + ENERGY
    + METALS_COMMODITIES
    + ARGENTINA_PLUS
    + INDICES_ETFS
)

from backend.services.alpaca_universe_fetcher import ALPACA_EXTENDED_CACHE

DEFAULT_UNIVERSES: dict[str, tuple[str, list[str]]] = {
    "general": ("General", GENERAL),
    "wall_street": ("Wall Street", WALL_STREET),
    "alpaca_extended": ("Alpaca Broad Market", ALPACA_EXTENDED_CACHE),
    "magnificas": ("7 Magnificas", MAGNIFICAS),
    "ftmo_core": ("FTMO Core", FTMO_CORE),
    "options_liquid": ("Options Liquid", OPTIONS_LIQUID),
    "ai_infra_momentum": ("AI Infra Momentum", AI_INFRA_MOMENTUM),
    "healthcare": ("Healthcare", HEALTHCARE),
    "energy": ("Energy", ENERGY),
    "metals_commodities": ("Metals & Commodities", METALS_COMMODITIES),
    "adr_arg": ("ADR Arg", ADR_ARG),
    "argentina_plus": ("Argentina Plus", ARGENTINA_PLUS),
    "arg": ("ARG", ARG_LOCAL),
    "indices": ("Indices / ETFs", INDICES_ETFS),
    "crypto": ("Crypto", CRYPTO),
    "all_liquid": ("All Liquid", ALL_LIQUID),
}
