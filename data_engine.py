"""
data_engine.py
Handles PSX stock data fetching, caching, and fallback data generation.
"""

import os
import datetime
import numpy as np
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Prominent PSX tickers across key sectors
PSX_WATCHLIST = {
    "IPAK": {
        "name": "International Packaging Films Limited",
        "sector": "Packaging",
        "base_price": 38.0,
    },
    "OGDC": {
        "name": "Oil & Gas Development Company Limited",
        "sector": "Oil & Gas Exploration",
        "base_price": 162.5,
    },
    "PPL": {
        "name": "Pakistan Petroleum Limited",
        "sector": "Oil & Gas Exploration",
        "base_price": 128.0,
    },
    "SYS": {
        "name": "Systems Limited",
        "sector": "Technology",
        "base_price": 435.0,
    },
    "LUCK": {
        "name": "Lucky Cement Limited",
        "sector": "Cement",
        "base_price": 890.0,
    },
    "ENGRO": {
        "name": "Engro Corporation Limited",
        "sector": "Fertilizer / Conglomerate",
        "base_price": 345.0,
    },
    "FFC": {
        "name": "Fauji Fertilizer Company Limited",
        "sector": "Fertilizer",
        "base_price": 195.0,
    },
    "HUBC": {
        "name": "The Hub Power Company Limited",
        "sector": "Power Generation",
        "base_price": 142.0,
    },
    "MCB": {
        "name": "MCB Bank Limited",
        "sector": "Commercial Banks",
        "base_price": 218.0,
    },
    "MEBL": {
        "name": "Meezan Bank Limited",
        "sector": "Islamic Commercial Banks",
        "base_price": 240.0,
    },
    "UBL": {
        "name": "United Bank Limited",
        "sector": "Commercial Banks",
        "base_price": 265.0,
    },
    "PSO": {
        "name": "Pakistan State Oil Company",
        "sector": "Oil & Gas Marketing",
        "base_price": 182.0,
    },
    "ATRL": {
        "name": "Attock Refinery Limited",
        "sector": "Refinery",
        "base_price": 375.0,
    },
    "DGKC": {
        "name": "D.G. Khan Cement Company",
        "sector": "Cement",
        "base_price": 88.5,
    },
    "EFERT": {
        "name": "Engro Fertilizers Limited",
        "sector": "Fertilizer",
        "base_price": 168.0,
    },
    "PIOC": {
        "name": "Pioneer Cement Limited",
        "sector": "Cement",
        "base_price": 148.0,
    },
    "SEARL": {
        "name": "The Searle Company Limited",
        "sector": "Pharmaceuticals",
        "base_price": 64.0,
    },
    "TRG": {
        "name": "TRG Pakistan Limited",
        "sector": "Technology",
        "base_price": 62.5,
    },
    "PRL": {
        "name": "Pakistan Refinery Limited",
        "sector": "Refinery",
        "base_price": 28.5,
    },
    "MLCF": {
        "name": "Maple Leaf Cement Factory",
        "sector": "Cement",
        "base_price": 41.5,
    },
}


def get_watchlist():
    return PSX_WATCHLIST


def _generate_synthetic_psx_data(symbol: str, days: int = 180, base_price: float = None) -> pd.DataFrame:
    """
    Generates realistic historical daily OHLCV data modeled on real PSX price action
    for a given symbol when offline or when yfinance data is unavailable.
    """
    if base_price is None:
        info = PSX_WATCHLIST.get(symbol.upper(), {})
        base_price = info.get("base_price", 100.0)

    # Seed with deterministic symbol hash for consistency across app reloads
    seed = sum(ord(c) for c in symbol)
    np.random.seed(seed)

    end_date = datetime.date.today()
    # Generate business days
    dates = pd.date_range(end=end_date, periods=days, freq="B")

    # Generate daily returns with slight upward drift and realistic PSX volatility (2.2% daily std)
    daily_returns = np.random.normal(0.0008, 0.022, size=len(dates))

    # Add occasional momentum swings
    for i in range(20, len(daily_returns), 30):
        trend_direction = 1 if (i // 30) % 2 == 0 else -0.8
        daily_returns[i : min(i + 8, len(daily_returns))] += 0.012 * trend_direction

    price_series = base_price * np.cumprod(1 + daily_returns)
    # Re-anchor so latest close roughly matches base_price
    price_series = price_series * (base_price / price_series[-1])

    records = []
    for d, c in zip(dates, price_series):
        daily_vol = np.random.uniform(0.01, 0.035) * c
        o = c + np.random.uniform(-0.5, 0.5) * daily_vol
        h = max(o, c) + np.random.uniform(0.1, 0.8) * daily_vol
        l = min(o, c) - np.random.uniform(0.1, 0.8) * daily_vol
        vol = int(np.random.lognormal(mean=13.5, sigma=0.8))  # ~700k - 2M shares

        records.append({
            "Date": d,
            "Open": round(float(o), 2),
            "High": round(float(h), 2),
            "Low": round(float(l), 2),
            "Close": round(float(c), 2),
            "Volume": vol,
        })

    df = pd.DataFrame(records)
    df.set_index("Date", inplace=True)
    return df


def fetch_psx_stock(symbol: str, period: str = "6mo", use_live: bool = True) -> tuple[pd.DataFrame, str]:
    """
    Fetches stock data for a given PSX symbol.
    Returns (DataFrame, source_status).
    """
    clean_symbol = symbol.strip().upper()
    cache_file = os.path.join(CACHE_DIR, f"{clean_symbol}_{period}.csv")

    if use_live:
        try:
            import yfinance as yf
            # PSX tickers on Yahoo Finance have .KA suffix
            yf_ticker = f"{clean_symbol}.KA"
            ticker = yf.Ticker(yf_ticker)
            df = ticker.history(period=period, interval="1d")

            if df is not None and not df.empty and len(df) > 15:
                df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                # Cache to disk
                df.to_csv(cache_file)
                return df, f"Live PSX Feed ({yf_ticker})"
        except Exception:
            pass  # Fallback to local cache or synthetic data

    # Try reading from disk cache
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col="Date", parse_dates=True)
            if not df.empty and len(df) > 15:
                return df, "Local PSX Cache"
        except Exception:
            pass

    # Use deterministic PSX modeling
    days_map = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
    days = days_map.get(period, 180)
    df = _generate_synthetic_psx_data(clean_symbol, days=days)
    return df, "PSX Synthetic Model (Offline Ready)"
