"""
data_engine.py
Production PSX Data Engine with Strict Data Integrity & Multi-Source Fallback.
Zero synthetic fabrication in production.
"""

import os
import datetime
import requests
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Top 75 Most Liquid & Active PSX Stocks across All Core Sectors
PSX_WATCHLIST = {
    # --- Oil & Gas Exploration (E&P) ---
    "OGDC": {"name": "Oil & Gas Development Company Limited", "sector": "Oil & Gas Exploration"},
    "PPL": {"name": "Pakistan Petroleum Limited", "sector": "Oil & Gas Exploration"},
    "MARI": {"name": "Mari Petroleum Company Limited", "sector": "Oil & Gas Exploration"},
    "POL": {"name": "Pakistan Oilfields Limited", "sector": "Oil & Gas Exploration"},

    # --- Commercial & Islamic Banks ---
    "MEBL": {"name": "Meezan Bank Limited", "sector": "Islamic Commercial Banks"},
    "MCB": {"name": "MCB Bank Limited", "sector": "Commercial Banks"},
    "UBL": {"name": "United Bank Limited", "sector": "Commercial Banks"},
    "HBL": {"name": "Habib Bank Limited", "sector": "Commercial Banks"},
    "BAFL": {"name": "Bank Alfalah Limited", "sector": "Commercial Banks"},
    "BAHL": {"name": "Bank AL Habib Limited", "sector": "Commercial Banks"},
    "FABL": {"name": "Faysal Bank Limited", "sector": "Islamic Commercial Banks"},
    "BIPL": {"name": "BankIslami Pakistan Limited", "sector": "Islamic Commercial Banks"},
    "AKBL": {"name": "Askari Bank Limited", "sector": "Commercial Banks"},
    "NBP": {"name": "National Bank of Pakistan", "sector": "Commercial Banks"},

    # --- Fertilizer ---
    "FFC": {"name": "Fauji Fertilizer Company Limited", "sector": "Fertilizer"},
    "EFERT": {"name": "Engro Fertilizers Limited", "sector": "Fertilizer"},
    "ENGRO": {"name": "Engro Corporation Limited", "sector": "Fertilizer / Conglomerate"},
    "FATIMA": {"name": "Fatima Fertilizer Company Limited", "sector": "Fertilizer"},
    "FFBL": {"name": "Fauji Fertilizer Bin Qasim Limited", "sector": "Fertilizer"},

    # --- Cement ---
    "LUCK": {"name": "Lucky Cement Limited", "sector": "Cement"},
    "DGKC": {"name": "D.G. Khan Cement Company", "sector": "Cement"},
    "MLCF": {"name": "Maple Leaf Cement Factory", "sector": "Cement"},
    "PIOC": {"name": "Pioneer Cement Limited", "sector": "Cement"},
    "CHCC": {"name": "Cherat Cement Company Limited", "sector": "Cement"},
    "FCCL": {"name": "Fauji Cement Company Limited", "sector": "Cement"},
    "KOHC": {"name": "Kohat Cement Company Limited", "sector": "Cement"},
    "POWER": {"name": "Power Cement Limited", "sector": "Cement"},

    # --- Technology & Telecommunication ---
    "SYS": {"name": "Systems Limited", "sector": "Technology"},
    "TRG": {"name": "TRG Pakistan Limited", "sector": "Technology"},
    "AVN": {"name": "Avanceon Limited", "sector": "Technology"},
    "AIRLINK": {"name": "Air Link Communication Limited", "sector": "Technology"},
    "PTC": {"name": "Pakistan Telecommunication Company", "sector": "Telecommunication"},
    "NETSOL": {"name": "NetSol Technologies Limited", "sector": "Technology"},
    "OCTOPUS": {"name": "Octopus Digital Limited", "sector": "Technology"},
    "WTL": {"name": "WorldCall Telecom Limited", "sector": "Telecommunication"},

    # --- Power Generation & Distribution ---
    "HUBC": {"name": "The Hub Power Company Limited", "sector": "Power Generation"},
    "KAPCO": {"name": "Kot Addu Power Company Limited", "sector": "Power Generation"},
    "KEL": {"name": "K-Electric Limited", "sector": "Power Distribution"},
    "NCPL": {"name": "Nishat Chunian Power Limited", "sector": "Power Generation"},
    "NPL": {"name": "Nishat Power Limited", "sector": "Power Generation"},

    # --- Oil & Gas Marketing (OMCs) & Distribution ---
    "PSO": {"name": "Pakistan State Oil Company", "sector": "Oil & Gas Marketing"},
    "APL": {"name": "Attock Petroleum Limited", "sector": "Oil & Gas Marketing"},
    "SHEL": {"name": "Shell Pakistan Limited", "sector": "Oil & Gas Marketing"},
    "SNGP": {"name": "Sui Northern Gas Pipelines Limited", "sector": "Gas Distribution"},
    "SSGC": {"name": "Sui Southern Gas Company Limited", "sector": "Gas Distribution"},
    "HASCOL": {"name": "Hascol Petroleum Limited", "sector": "Oil & Gas Marketing"},

    # --- Refineries ---
    "ATRL": {"name": "Attock Refinery Limited", "sector": "Refinery"},
    "PRL": {"name": "Pakistan Refinery Limited", "sector": "Refinery"},
    "NRL": {"name": "National Refinery Limited", "sector": "Refinery"},
    "CYNERGICO": {"name": "Cynergico PK Limited", "sector": "Refinery"},

    # --- Pharmaceuticals & Chemicals ---
    "SEARL": {"name": "The Searle Company Limited", "sector": "Pharmaceuticals"},
    "AGP": {"name": "AGP Limited", "sector": "Pharmaceuticals"},
    "CPHL": {"name": "Citi Pharma Limited", "sector": "Pharmaceuticals"},
    "GLAXO": {"name": "GlaxoSmithKline Pakistan", "sector": "Pharmaceuticals"},
    "HINOON": {"name": "Highnoon Laboratories Limited", "sector": "Pharmaceuticals"},
    "LOTCHEM": {"name": "Lotte Chemical Pakistan Limited", "sector": "Chemicals"},
    "EPCL": {"name": "Engro Polymer & Chemicals Limited", "sector": "Chemicals"},
    "GLL": {"name": "Ghani Global Limited", "sector": "Chemicals"},

    # --- Automobile & Engineering ---
    "MTL": {"name": "Millat Tractors Limited", "sector": "Automobile Assembler"},
    "INDU": {"name": "Indus Motor Company Limited", "sector": "Automobile Assembler"},
    "SAZEW": {"name": "Sazgar Engineering Works", "sector": "Automobile Assembler"},
    "AGTL": {"name": "Al-Ghazi Tractors Limited", "sector": "Automobile Assembler"},
    "THALL": {"name": "Thal Limited", "sector": "Automobile Parts"},
    "MUGHAL": {"name": "Mughal Iron & Steel Industries", "sector": "Engineering / Steel"},
    "ISL": {"name": "International Steels Limited", "sector": "Engineering / Steel"},
    "INIL": {"name": "International Industries Limited", "sector": "Engineering / Steel"},
    "TGL": {"name": "Tariq Glass Industries Limited", "sector": "Glass & Ceramics"},

    # --- Textile, Packaging & Consumer Foods ---
    "NML": {"name": "Nishat Mills Limited", "sector": "Textile Composite"},
    "ILP": {"name": "Interloop Limited", "sector": "Textile Composite"},
    "KTML": {"name": "Kohinoor Textile Mills Limited", "sector": "Textile Composite"},
    "GATM": {"name": "Gul Ahmed Textile Mills Limited", "sector": "Textile Composite"},
    "IPAK": {"name": "International Packaging Films Limited", "sector": "Packaging"},
    "UNITY": {"name": "Unity Foods Limited", "sector": "Food & Personal Care"},
    "FCEPL": {"name": "FrieslandCampina Engro Pakistan", "sector": "Food & Personal Care"},
    "NATF": {"name": "National Foods Limited", "sector": "Food & Personal Care"},
}


def get_watchlist() -> dict:
    return PSX_WATCHLIST


def _fetch_from_yahoo(clean_symbol: str, period: str = "6mo") -> pd.DataFrame:
    """Fetches real market data from Yahoo Finance via .KA suffix."""
    import yfinance as yf
    ticker_str = f"{clean_symbol}.KA"
    ticker = yf.Ticker(ticker_str)
    df = ticker.history(period=period, interval="1d")
    if df is not None and not df.empty and len(df) >= 20:
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    return None


def check_has_true_ohlc(df: pd.DataFrame) -> bool:
    """
    Deterministically verifies whether a dataframe contains true intraday
    excursions (wicks) beyond the Open and Close prices.
    If High == max(Open, Close) and Low == min(Open, Close) across all bars,
    the data is synthetic or approximated without true intraday range.
    """
    if df is None or df.empty or len(df) < 5:
        return False
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            return False

    max_oc = df[["Open", "Close"]].max(axis=1)
    min_oc = df[["Open", "Close"]].min(axis=1)

    has_high_wicks = (df["High"] > max_oc + 1e-5).any()
    has_low_wicks = (df["Low"] < min_oc - 1e-5).any()

    return bool(has_high_wicks or has_low_wicks)


# Pakistan Standard Time (PKT = UTC+5) for deterministic date conversion on all servers
PKT_TZ = datetime.timezone(datetime.timedelta(hours=5))


def _fetch_from_psx_dps(clean_symbol: str) -> pd.DataFrame:
    """
    Fetches official end-of-day timeseries directly from the PSX Data Portal (DPS).
    Endpoint: https://dps.psx.com.pk/timeseries/eod/{symbol}

    NOTE ON DATA INTEGRITY:
    The PSX DPS EOD endpoint schema is strictly [timestamp, close, volume, open]
    and lacks true intraday High/Low excursions. High and Low are stored as
    max(o, c) and min(o, c) as candle bounds, but this dataset is strictly flagged
    with has_true_ohlc = False. Downstream signal engines must refuse to trigger
    live setups on this data to prevent distorted ATR and stop-loss levels.
    """
    url = f"https://dps.psx.com.pk/timeseries/eod/{clean_symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=12)
    if resp.status_code != 200:
        return None

    data = resp.json()
    rows = data.get("data", [])
    if not rows or len(rows) < 20:
        return None

    # Schema: [timestamp, close, volume, open]
    records = []
    for item in rows:
        ts, c, v, o = item[0], float(item[1]), float(item[2]), float(item[3])
        # Use explicit PKT (UTC+5) to prevent UTC servers (e.g. GitHub Actions) from shifting dates
        d = datetime.datetime.fromtimestamp(ts, tz=PKT_TZ).date()
        # Bound High/Low to max/min of Open/Close without fabricating fictional intraday wicks
        h = max(o, c)
        l = min(o, c)
        records.append({
            "Date": pd.to_datetime(d),
            "Open": round(o, 2),
            "High": round(h, 2),
            "Low": round(l, 2),
            "Close": round(c, 2),
            "Volume": int(v),
        })

    df = pd.DataFrame(records)
    df.drop_duplicates(subset=["Date"], keep="first", inplace=True)
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    return df


def validate_market_data(df: pd.DataFrame, max_age_days: int = 5, require_true_ohlc: bool = False) -> tuple[bool, str, int]:
    """
    Validates that market data is fresh, non-empty, and has reasonable integrity.
    Accounts for weekends (up to 4-5 days gap over holiday/long weekends).
    If require_true_ohlc is True, rejects datasets lacking true intraday wicks.
    Returns (is_valid, reason, data_age_days).
    """
    if df is None or df.empty or len(df) < 20:
        return False, "Insufficient historical sessions (minimum 20 required)", 999

    latest_date = df.index[-1].date() if hasattr(df.index[-1], "date") else df.index[-1]
    today = datetime.date.today()
    age_days = (today - latest_date).days

    if age_days < 0:
        age_days = 0  # Timestamp timezone difference

    if age_days > max_age_days:
        return False, f"Data is stale ({age_days} days old, max allowed: {max_age_days})", age_days

    # Verify standard columns
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            return False, f"Missing required column: {col}", age_days

    # Check for NaN in latest close
    if pd.isna(df["Close"].iloc[-1]) or df["Close"].iloc[-1] <= 0:
        return False, "Latest close price is invalid or NaN", age_days

    if require_true_ohlc and not check_has_true_ohlc(df):
        return False, "Data lacks verified intraday High/Low wicks (OHLC approximated)", age_days

    return True, "Data valid and verified", age_days


def fetch_psx_stock(
    symbol: str,
    period: str = "6mo",
    max_age_days: int = 5,
    force_refresh: bool = False,
) -> dict:
    """
    Fetches real PSX market data with rigorous source attribution and integrity flags.
    Strictly refuses to fabricate synthetic data in production.

    Returns dict:
      {
        "symbol": str,
        "status": "OK" | "UNAVAILABLE",
        "df": pd.DataFrame | None,
        "source": str,
        "has_true_ohlc": bool,
        "last_date": str,
        "data_age_days": int,
        "error": str | None
      }
    """
    clean_symbol = symbol.strip().upper()
    cache_file = os.path.join(CACHE_DIR, f"{clean_symbol}_real.csv")

    df = None
    source = "None"
    has_true_ohlc = False

    # 1. Try Yahoo Finance (.KA) - Primary Source for verified true OHLCV
    try:
        df_yf = _fetch_from_yahoo(clean_symbol, period=period)
        if df_yf is not None:
            is_valid, reason, age = validate_market_data(df_yf, max_age_days=max_age_days)
            if is_valid:
                df = df_yf
                has_true_ohlc = check_has_true_ohlc(df_yf)
                source = f"Yahoo Finance ({clean_symbol}.KA) — Verified True OHLC" if has_true_ohlc else f"Yahoo Finance ({clean_symbol}.KA) — Flat Wicks"
                df.to_csv(cache_file)
    except Exception:
        df = None

    # 2. Try Local Cache (only if force_refresh is False, recently updated and valid with true OHLC)
    if df is None and not force_refresh and os.path.exists(cache_file):
        try:
            df_cache = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            is_valid, reason, age = validate_market_data(df_cache, max_age_days=max_age_days)
            if is_valid:
                df = df_cache
                has_true_ohlc = check_has_true_ohlc(df_cache)
                source = f"Verified Local Cache ({clean_symbol}) — {'True OHLC' if has_true_ohlc else 'OHLC approximated'}"
        except Exception:
            df = None

    # 3. Try Official PSX Data Portal (DPS) Fallback
    # Note: DPS EOD endpoint schema provides [ts, close, volume, open] without intraday High/Low.
    # We explicitly label source so UI and alerts display 'OHLC approximated'.
    if df is None:
        try:
            df_dps = _fetch_from_psx_dps(clean_symbol)
            if df_dps is not None:
                is_valid, reason, age = validate_market_data(df_dps, max_age_days=max_age_days)
                if is_valid:
                    df = df_dps
                    has_true_ohlc = False  # DPS schema lacks true intraday High/Low wicks
                    source = "PSX Official Portal (DPS) — OHLC approximated (Close/Open only)"
                    df.to_csv(cache_file)
        except Exception:
            df = None

    # 4. Fallback to Local Cache if force_refresh was requested but network failed
    if df is None and force_refresh and os.path.exists(cache_file):
        try:
            df_cache = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            is_valid, reason, age = validate_market_data(df_cache, max_age_days=max_age_days)
            if is_valid:
                df = df_cache
                has_true_ohlc = check_has_true_ohlc(df_cache)
                source = f"Verified Local Cache ({clean_symbol}) — {'True OHLC' if has_true_ohlc else 'OHLC approximated'}"
        except Exception:
            df = None

    # 4. Final Validation & Return
    if df is not None:
        latest_date_str = df.index[-1].strftime("%Y-%m-%d")
        age_days = (datetime.date.today() - df.index[-1].date()).days
        return {
            "symbol": clean_symbol,
            "status": "OK",
            "df": df,
            "source": source,
            "has_true_ohlc": has_true_ohlc,
            "last_date": latest_date_str,
            "data_age_days": max(0, age_days),
            "error": None,
        }

    # Explicit failure: Never fabricate prices!
    return {
        "symbol": clean_symbol,
        "status": "UNAVAILABLE",
        "df": None,
        "source": "None",
        "has_true_ohlc": False,
        "last_date": None,
        "data_age_days": 999,
        "error": f"Real market data unavailable for {clean_symbol}. Alert skipped to maintain data integrity.",
    }


def generate_isolated_test_data(days: int = 120, base_price: float = 100.0) -> pd.DataFrame:
    """
    Quarantined synthetic dataset generator strictly for automated unit tests.
    Never imported or used in production alert pipelines.
    """
    import numpy as np
    dates = pd.date_range(end=datetime.date.today(), periods=days, freq="B")
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.018, size=len(dates))
    prices = base_price * np.cumprod(1 + returns)
    records = []
    for d, c in zip(dates, prices):
        vol = int(np.random.uniform(500000, 2000000))
        records.append({
            "Date": d,
            "Open": round(float(c * 0.995), 2),
            "High": round(float(c * 1.015), 2),
            "Low": round(float(c * 0.985), 2),
            "Close": round(float(c), 2),
            "Volume": vol,
        })
    df = pd.DataFrame(records).set_index("Date")
    return df
