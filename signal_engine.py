"""
signal_engine.py
Unified Strategy Engine & Transparent Condition Checklist.
Serves as the single deterministic source of truth for both live alerts and backtesting.
"""

import datetime
import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def compute_all_indicators(df: pd.DataFrame, df_kse: pd.DataFrame = None) -> pd.DataFrame:
    """Computes technical indicator series and benchmark relative strength across the price history."""
    df = df.copy()
    close = df["Close"]

    df["EMA_20"] = calculate_ema(close, 20)
    df["EMA_50"] = calculate_ema(close, 50)
    df["EMA_200"] = calculate_ema(close, min(200, max(20, len(df) - 1)))

    macd, sig, hist = calculate_macd(close, 12, 26, 9)
    df["MACD"] = macd
    df["MACD_Signal"] = sig
    df["MACD_Hist"] = hist

    df["RSI"] = calculate_rsi(close, 14)
    df["ATR"] = calculate_atr(df, 14)
    df["Vol_MA20"] = df["Volume"].rolling(20).mean()

    # Dynamic rolling 20-day high and low
    df["Rolling_High20"] = df["High"].rolling(20).max()
    df["Rolling_Low20"] = df["Low"].rolling(20).min()

    # Bollinger Bands & Volatility Compression (Squeeze Base)
    df["BB_Middle"] = df["Close"].rolling(20).mean()
    df["BB_Std"] = df["Close"].rolling(20).std()
    df["BB_Upper"] = df["BB_Middle"] + (2.0 * df["BB_Std"])
    df["BB_Lower"] = df["BB_Middle"] - (2.0 * df["BB_Std"])
    df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / (df["BB_Middle"] + 1e-6)
    df["BB_Width_Min60"] = df["BB_Width"].rolling(60, min_periods=20).min()
    # Volatility squeeze: current bandwidth is within 35% of its 60-day tightest compression
    df["Is_Squeeze"] = df["BB_Width"] <= (1.35 * df["BB_Width_Min60"])

    # Classical Daily Floor Pivot Points (calculated from prior session High, Low, Close without lookahead)
    prev_h = df["High"].shift(1)
    prev_l = df["Low"].shift(1)
    prev_c = df["Close"].shift(1)
    df["Pivot"] = (prev_h + prev_l + prev_c) / 3.0
    df["Pivot_S1"] = (2.0 * df["Pivot"]) - prev_h
    df["Pivot_R1"] = (2.0 * df["Pivot"]) - prev_l
    df["Pivot_S2"] = df["Pivot"] - (prev_h - prev_l)
    df["Pivot_R2"] = df["Pivot"] + (prev_h - prev_l)

    # Relative Strength vs KSE-100 Benchmark
    if df_kse is not None and not df_kse.empty:
        kse_series = df_kse["Close"].copy()
        if kse_series.index.tz is not None and df.index.tz is None:
            kse_series.index = kse_series.index.tz_localize(None)
        elif kse_series.index.tz is None and df.index.tz is not None:
            kse_series.index = kse_series.index.tz_localize(df.index.tz)
        elif kse_series.index.tz != df.index.tz:
            kse_series.index = kse_series.index.tz_convert(df.index.tz)
        kse_aligned = kse_series.reindex(df.index, method="ffill")
        df["RS_Ratio"] = df["Close"] / (kse_aligned + 1e-6)
        df["RS_MA20"] = df["RS_Ratio"].rolling(20, min_periods=10).mean()
        df["Is_RS_Leader"] = df["RS_Ratio"] >= df["RS_MA20"]
    else:
        df["RS_Ratio"] = 1.0
        df["RS_MA20"] = 1.0
        df["Is_RS_Leader"] = True

    return df


def evaluate_bar_strategy(
    df_ind: pd.DataFrame,
    bar_idx: int = -1,
    has_true_ohlc: bool = True,
    market_regime: dict = None,
    params: dict = None,
) -> dict:
    """
    Evaluates strategy conditions on a specific historical bar.
    Single source of truth used identically by both the screener and the backtester.

    Strategies supported:
    1. BREAKOUT: Consolidation near 20-day resistance, breakout with volume & RS leadership.
    2. PULLBACK: Healthy uptrend, price pulling back to 20 EMA / support band.
    """
    if df_ind is None or len(df_ind) < 25:
        return {"status": "INSUFFICIENT_DATA"}

    # Configurable Strategy Parameters with institutional defaults
    p_vol_mult = params.get("vol_surge_mult", 1.20) if params else 1.20
    p_rsi_min = params.get("rsi_min", 42.0) if params else 42.0
    p_rsi_max = params.get("rsi_max", 68.0) if params else 68.0
    p_pb_rsi_max = params.get("pullback_rsi_max", 58.0) if params else 58.0
    p_oversold_rsi_max = params.get("oversold_rsi_max", 38.0) if params else 38.0
    p_rr_min = params.get("rr_min", 1.20) if params else 1.20

    # Normalize negative index
    n = len(df_ind)
    idx = (n + bar_idx) if bar_idx < 0 else bar_idx
    if idx < 20 or idx >= n:
        return {"status": "INDEX_OUT_OF_BOUNDS"}

    bar = df_ind.iloc[idx]
    prev = df_ind.iloc[idx - 1]

    price = float(bar["Close"])
    open_price = float(bar["Open"])
    high = float(bar["High"])
    low = float(bar["Low"])
    ema20 = float(bar["EMA_20"])
    ema50 = float(bar["EMA_50"])
    ema200 = float(bar["EMA_200"]) if "EMA_200" in bar and not pd.isna(bar["EMA_200"]) else ema50
    rsi = float(bar["RSI"]) if not pd.isna(bar["RSI"]) else 50.0
    prev_rsi = float(prev["RSI"]) if not pd.isna(prev["RSI"]) else rsi
    macd_hist = float(bar["MACD_Hist"])
    prev_macd_hist = float(prev["MACD_Hist"])
    atr = float(bar["ATR"]) if not pd.isna(bar["ATR"]) and bar["ATR"] > 0 else (price * 0.025)
    volume = float(bar["Volume"])
    vol_ma = float(bar["Vol_MA20"]) if not pd.isna(bar["Vol_MA20"]) and bar["Vol_MA20"] > 0 else 1.0

    # Classical Floor Pivots and Bollinger Lower
    pivot = float(bar["Pivot"]) if "Pivot" in bar and not pd.isna(bar["Pivot"]) else price
    pivot_s1 = float(bar["Pivot_S1"]) if "Pivot_S1" in bar and not pd.isna(bar["Pivot_S1"]) else (price * 0.98)
    pivot_s2 = float(bar["Pivot_S2"]) if "Pivot_S2" in bar and not pd.isna(bar["Pivot_S2"]) else (price * 0.96)
    pivot_r1 = float(bar["Pivot_R1"]) if "Pivot_R1" in bar and not pd.isna(bar["Pivot_R1"]) else (price * 1.02)
    pivot_r2 = float(bar["Pivot_R2"]) if "Pivot_R2" in bar and not pd.isna(bar["Pivot_R2"]) else (price * 1.04)
    bb_lower = float(bar["BB_Lower"]) if "BB_Lower" in bar and not pd.isna(bar["BB_Lower"]) else (price * 0.95)

    # 20-day resistance & support measured from preceding bars
    res20 = float(df_ind["High"].iloc[max(0, idx - 20):idx].max())
    sup20 = float(df_ind["Low"].iloc[max(0, idx - 20):idx].min())

    date_val = df_ind.index[idx]
    date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)

    # Macro & Benchmark Conditions
    is_market_bullish = market_regime.get("is_bullish", True) if market_regime else True
    regime_name = market_regime.get("regime", "UNKNOWN") if market_regime else "UNKNOWN"
    c_market_gate = bool(is_market_bullish)
    c_rs_leader = bool(bar["Is_RS_Leader"]) if "Is_RS_Leader" in bar else True
    c_squeeze = bool(bar["Is_Squeeze"]) if "Is_Squeeze" in bar else True

    # Technical Condition checks
    c_true_ohlc = bool(has_true_ohlc)
    c_trend = price > ema20 and ema20 > ema50
    c_macd_turn = macd_hist > prev_macd_hist
    c_volume_surge = volume >= (p_vol_mult * vol_ma)
    c_rsi_healthy = p_rsi_min <= rsi <= p_rsi_max
    c_breakout_level = price >= res20
    c_liquidity = vol_ma >= 80_000
    is_oversold = (rsi <= p_oversold_rsi_max) or (price <= bb_lower * 1.015)

    # --- STRATEGY 1: 20-Day Range Breakout ---
    dist_to_res = (res20 - price) / price
    if c_trend and (-0.015 <= dist_to_res <= 0.03) and not is_oversold:
        strategy = "BREAKOUT"
        entry_min = round(max(res20 * 0.995, price - 0.3 * atr), 2)
        entry_max = round(max(price, res20 * 1.01), 2)
        # Structural Invalidation: Below 50 EMA support (or res20 - 0.8*ATR, whichever is more defensive)
        invalidation = round(min(res20 - 0.8 * atr, ema50 * 0.98), 2) if ema50 * 0.98 < entry_max else round(res20 - 0.8 * atr, 2)
        risk = max(entry_max - invalidation, 0.01)
        tp1 = round(entry_max + max(1.35 * risk, 1.4 * atr), 2)
        tp2 = round(entry_max + max(2.5 * risk, 2.6 * atr), 2)
        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        checklist = {
            "Macro Market Gate (KSE-100 > 50 EMA)": c_market_gate,
            "Relative Strength Leader (vs KSE-100)": c_rs_leader,
            "Volatility Compression (Tight Base)": c_squeeze,
            "Verified Intraday OHLC (True High/Low)": c_true_ohlc,
            "Trend Alignment (Price > 20 & 50 EMA)": c_trend,
            "Resistance Test / Clearance": c_breakout_level,
            f"Volume Surge (Vol >= {p_vol_mult:.2f}x 20MA)": c_volume_surge,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            "Momentum Health (MACD Histogram Expanding)": c_macd_turn,
            f"Healthy RSI Range ({p_rsi_min:.0f} - {p_rsi_max:.0f})": c_rsi_healthy,
            f"Favorable Risk:Reward (>= {p_rr_min:.1f}:1)": rr_tp1 >= p_rr_min,
        }

        # Status determination (gated strictly on ALL checklist conditions)
        if all(checklist.values()):
            status = "TRIGGERED"
            trigger_note = f"All criteria verified: daily close ({price:.2f}) cleared resistance ({res20:.2f}) with {volume/vol_ma:.1f}x volume, RS leadership, tight base, and R:R {rr_tp1}:1."
        elif not c_market_gate:
            status = "WATCHING"
            trigger_note = f"Disqualified from TRIGGERED: Broad market in correction ({regime_name}). Long breakouts suppressed to preserve cash."
        elif not c_rs_leader:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Stock is lagging behind KSE-100 index (lack of institutional sponsorship)."
        elif not c_squeeze:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Volatility is loose/erratic. Setup requires tight volatility compression base before breakout."
        elif not c_true_ohlc:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Data source lacks verified intraday High/Low wicks. ATR and resistance levels cannot be reliably calculated."
        elif not (rr_tp1 >= 1.2):
            status = "WATCHING"
            trigger_note = f"Price cleared resistance, but disqualified: Risk:Reward ({rr_tp1}:1) is below 1.2:1 minimum threshold."
        elif not c_rsi_healthy:
            status = "WATCHING"
            trigger_note = f"Price cleared resistance, but disqualified: RSI ({rsi:.1f}) is outside healthy range (42 - 68)."
        elif c_breakout_level and not c_volume_surge:
            status = "WATCHING"
            trigger_note = f"Price cleared resistance ({res20:.2f}), but volume ({volume/vol_ma:.1f}x MA) requires confirmation."
        elif not c_liquidity:
            status = "WATCHING"
            trigger_note = f"Approaching resistance ({res20:.2f}), but average liquidity ({vol_ma:.0f} shares) is below 80k threshold."
        else:
            status = "WATCHING"
            trigger_note = f"Approaching 20-day resistance ({res20:.2f}). Waiting for close breakout with volume."

        return {
            "strategy": strategy,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(ema20, 2),
            "trailing_rule": "Exit on daily close below 20 EMA (Trailing Trend Model)",
            "risk_pct": round((risk / entry_max) * 100, 1),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "trigger_note": trigger_note,
            "checklist": checklist,
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "vol_ratio": round(volume / vol_ma, 2),
            "has_true_ohlc": c_true_ohlc,
            "is_rs_leader": c_rs_leader,
            "is_squeeze": c_squeeze,
        }

    # --- STRATEGY 2: Pullback to 20 EMA / Support ---
    dist_to_ema20 = (price - ema20) / ema20
    is_in_pullback_zone = price > ema50 and (-0.02 <= dist_to_ema20 <= 0.02)
    if is_in_pullback_zone and rsi < p_pb_rsi_max and not is_oversold:
        strategy = "PULLBACK"
        entry_min = round(min(ema20 - 0.2 * atr, price * 0.99), 2)
        entry_max = round(max(price, ema20 + 0.2 * atr), 2)
        invalidation = round(min(ema20 - 0.8 * atr, ema50 * 0.98), 2) if ema50 * 0.98 < entry_max else round(ema20 - 0.8 * atr, 2)
        risk = max(entry_max - invalidation, 0.01)
        tp1 = round(entry_max + max(1.35 * risk, 1.4 * atr), 2)
        tp2 = round(entry_max + max(2.5 * risk, 2.6 * atr), 2)
        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        c_bounce_candle = price >= open_price  # Green close off support

        checklist = {
            "Macro Market Gate (KSE-100 > 50 EMA)": c_market_gate,
            "Relative Strength Leader (vs KSE-100)": c_rs_leader,
            "Verified Intraday OHLC (True High/Low)": c_true_ohlc,
            "Macro Trend Intact (Price > 50 EMA)": price > ema50,
            "Testing 20 EMA Support Zone": True,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            f"Cooling RSI (< {p_pb_rsi_max:.0f}, Not Overbought)": rsi < p_pb_rsi_max,
            "Bounce Confirmation (Bullish Close)": c_bounce_candle,
            "MACD Momentum Stabilization": c_macd_turn,
            f"Favorable Risk:Reward (>= {p_rr_min:.1f}:1)": rr_tp1 >= p_rr_min,
        }

        # Status determination (gated strictly on ALL checklist conditions)
        if all(checklist.values()):
            status = "TRIGGERED"
            trigger_note = f"All criteria verified: bullish bounce at 20 EMA ({ema20:.2f}) with RS leadership, stabilizing MACD, and R:R {rr_tp1}:1."
        elif not c_market_gate:
            status = "WATCHING"
            trigger_note = f"Disqualified from TRIGGERED: Broad market in correction ({regime_name}). Long pullbacks suppressed to preserve cash."
        elif not c_rs_leader:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Stock is lagging behind KSE-100 index."
        elif not c_true_ohlc:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Data source lacks verified intraday High/Low wicks. ATR and stop-loss distance cannot be reliably calculated."
        elif not (rr_tp1 >= p_rr_min):
            status = "WATCHING"
            trigger_note = f"Support bounce detected, but disqualified: Risk:Reward ({rr_tp1}:1) is below {p_rr_min:.1f}:1 minimum threshold."
        elif not (rsi < p_pb_rsi_max):
            status = "WATCHING"
            trigger_note = f"Testing support, but RSI ({rsi:.1f}) is elevated (> {p_pb_rsi_max:.0f})."
        elif not c_liquidity:
            status = "WATCHING"
            trigger_note = f"Testing 20 EMA support, but liquidity ({vol_ma:.0f} shares) is below 80k threshold."
        else:
            status = "WATCHING"
            trigger_note = f"Testing 20 EMA support ({ema20:.2f}). Waiting for bullish reversal confirmation."

        return {
            "strategy": strategy,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(ema20, 2),
            "trailing_rule": "Exit on daily close below 20 EMA (Trailing Trend Model)",
            "risk_pct": round((risk / entry_max) * 100, 1),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "trigger_note": trigger_note,
            "checklist": checklist,
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "vol_ratio": round(volume / vol_ma, 2),
            "has_true_ohlc": c_true_ohlc,
            "is_rs_leader": c_rs_leader,
            "is_squeeze": c_squeeze,
        }

    # --- STRATEGY 3: Mean-Reversion / Oversold Rebound (Broker Model) ---
    is_oversold = (rsi <= p_oversold_rsi_max) or (price <= bb_lower * 1.015)
    # Test support confluence: near S1/S2 pivots, or 20-day rolling low, or 200 EMA
    near_pivot_s1 = (low <= pivot_s1 * 1.025) or (price <= pivot_s1 * 1.015)
    near_pivot_s2 = (low <= pivot_s2 * 1.025) or (price <= pivot_s2 * 1.015)
    near_rolling_low = ((price - sup20) / (price + 1e-6)) <= 0.025
    near_ema200 = (abs(price - ema200) / (ema200 + 1e-6)) <= 0.025
    is_near_support = near_pivot_s1 or near_pivot_s2 or near_rolling_low or near_ema200

    if is_oversold and is_near_support:
        strategy = "MEAN_REVERSION"
        entry_min = round(min(open_price, price) * 0.995, 2)
        entry_max = round(max(open_price, price) * 1.005, 2)
        # Structural Invalidation: below recent bar low minus buffer or below S2
        invalidation = round(max(low - (0.4 * atr), pivot_s2 * 0.99), 2)
        if invalidation >= entry_max:
            invalidation = round(entry_max - max(1.0 * atr, entry_max * 0.025), 2)
        risk = max(entry_max - invalidation, 0.01)

        # Target 1 (Scalp): Classical Pivot (P) or R1 (fast mean reversion)
        if pivot > entry_max * 1.012:
            tp1 = round(pivot, 2)
        elif pivot_r1 > entry_max * 1.015:
            tp1 = round(pivot_r1, 2)
        else:
            tp1 = round(entry_max + max(1.25 * risk, 1.2 * atr), 2)

        # Target 2: Mean Reversion to 20 EMA
        if ema20 > tp1 * 1.015:
            tp2 = round(ema20, 2)
        else:
            tp2 = round(max(pivot_r2, entry_max + 2.2 * risk), 2)

        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        # Reversal Confirmation: Green close OR lower-wick rejection (hammer)
        bar_range = max(high - low, 0.01)
        lower_wick_ratio = (min(open_price, price) - low) / bar_range
        c_bounce_candle = (price >= open_price) or (lower_wick_ratio >= 0.40) or ((price - low) / bar_range >= 0.45)
        # Momentum curl: MACD histogram rising OR RSI hooking up from low
        c_momentum_turn = (macd_hist > prev_macd_hist) or (rsi > prev_rsi)

        checklist = {
            f"Oversold Momentum (RSI <= {p_oversold_rsi_max:.0f} or Lower BB)": is_oversold,
            "Classical Support Confluence (Near S1/S2/20-Low)": is_near_support,
            "Intraday Reversal / Bounce Confirmation": c_bounce_candle,
            "Momentum Stabilization (RSI / MACD Curl)": c_momentum_turn,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            "Verified Intraday OHLC (True High/Low)": c_true_ohlc,
            "Favorable Mean-Reversion Potential (R:R >= 0.8:1 TP1 or >= 1.5:1 TP2)": (rr_tp1 >= 0.80 or rr_tp2 >= 1.50),
        }

        if all(checklist.values()):
            status = "TRIGGERED"
            trigger_note = f"All criteria verified: oversold rebound (RSI {rsi:.1f}) off support ({pivot_s1:.2f}) with reversal candle, curling momentum, and R:R {rr_tp1}:1 to TP1 ({tp1:.2f})."
        elif not c_true_ohlc:
            status = "WATCHING"
            trigger_note = "Disqualified from TRIGGERED: Data source lacks verified intraday High/Low wicks. Support levels cannot be reliably calculated."
        elif not c_liquidity:
            status = "WATCHING"
            trigger_note = f"Oversold near support, but 20-day liquidity ({vol_ma:.0f} shares) is below 80k threshold."
        elif not c_bounce_candle:
            status = "WATCHING"
            trigger_note = f"Oversold near support (RSI {rsi:.1f}), but waiting for green close or lower-wick absorption candle."
        elif not c_momentum_turn:
            status = "WATCHING"
            trigger_note = f"Oversold near support (RSI {rsi:.1f}), but momentum has not yet curled upward."
        elif not (rr_tp1 >= 0.80 or rr_tp2 >= 1.50):
            status = "WATCHING"
            trigger_note = f"Oversold bounce detected, but Risk:Reward ({rr_tp1}:1 to TP1, {rr_tp2}:1 to TP2) is below threshold."
        else:
            status = "WATCHING"
            trigger_note = f"Oversold setup developing near support ({pivot_s1:.2f}). Waiting for confirmation."

        return {
            "strategy": strategy,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(ema20, 2),
            "trailing_rule": "Exit at Target 1 (Scalp) / Target 2 (20 EMA) or 3-Day Time Stop",
            "risk_pct": round((risk / entry_max) * 100, 1),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "trigger_note": trigger_note,
            "checklist": checklist,
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "vol_ratio": round(volume / vol_ma, 2),
            "has_true_ohlc": c_true_ohlc,
            "is_rs_leader": c_rs_leader,
            "is_squeeze": c_squeeze,
            "pivot": round(pivot, 2),
            "pivot_s1": round(pivot_s1, 2),
            "pivot_s2": round(pivot_s2, 2),
            "pivot_r1": round(pivot_r1, 2),
            "pivot_r2": round(pivot_r2, 2),
        }

    # Default: No clear actionable setup
    return {
        "strategy": "NONE",
        "status": "NEUTRAL",
        "date": date_str,
        "price": price,
        "trigger_note": "Consolidating / Choppy. No high-probability breakout or pullback setup.",
        "checklist": {
            "Verified Intraday OHLC": c_true_ohlc,
            "Trend Alignment": c_trend,
            "Volume Confirmation": c_volume_surge,
            "Momentum Health": c_macd_turn,
            "RSI in Range": c_rsi_healthy,
        },
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "vol_ratio": round(volume / vol_ma, 2),
        "has_true_ohlc": c_true_ohlc,
    }


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    data_meta: dict = None,
    df_kse: pd.DataFrame = None,
    market_regime: dict = None,
    params: dict = None,
) -> dict:
    """
    Evaluates the latest completed session and formats a comprehensive setup package.
    """
    if df is None or len(df) < 25:
        return {}

    has_true_ohlc = data_meta.get("has_true_ohlc", True) if data_meta else True
    df_ind = compute_all_indicators(df, df_kse=df_kse)
    setup = evaluate_bar_strategy(
        df_ind,
        bar_idx=-1,
        has_true_ohlc=has_true_ohlc,
        market_regime=market_regime,
        params=params,
    )
    setup["symbol"] = symbol
    setup["df_indicators"] = df_ind
    setup["has_true_ohlc"] = has_true_ohlc

    # Attach data metadata
    if data_meta:
        setup["source"] = data_meta.get("source", "PSX Direct")
        setup["last_date"] = data_meta.get("last_date", str(df.index[-1].date()))
        setup["data_age_days"] = data_meta.get("data_age_days", 0)
    else:
        setup["source"] = "PSX Direct"
        setup["last_date"] = str(df.index[-1].date())
        setup["data_age_days"] = 0

    return setup


def format_actionable_card(setup: dict, company_name: str = "") -> str:
    """
    Formats the transparent, actionable Telegram card requested by the user.
    """
    sym = setup.get("symbol", "")
    strategy = setup.get("strategy", "NONE")
    status = setup.get("status", "NEUTRAL")
    price = setup.get("price", 0.0)
    source = setup.get("source", "PSX Feed")
    last_date = setup.get("last_date", "Today")
    age = setup.get("data_age_days", 0)

    if status == "NEUTRAL" or strategy == "NONE":
        return f"⚪ <b>${sym}</b> — {company_name}\nStatus: NEUTRAL (No high-probability setup)."

    badge_emoji = "🟢" if status == "TRIGGERED" else "🟡"
    status_label = "Triggered (Confirmed)" if status == "TRIGGERED" else "Watching (Waiting for Confirmation)"
    import html

    def esc(text: str) -> str:
        return html.escape(str(text), quote=False)

    checklist = setup.get("checklist", {})
    checklist_lines = "\n".join([
        f"   {'[✓]' if passed else '[✗]'} {esc(k)}" for k, passed in checklist.items()
    ])

    trigger_note = esc(setup.get('trigger_note', ''))
    source_esc = esc(source)
    company_esc = esc(company_name)

    if strategy == "MEAN_REVERSION":
        exit_model_str = setup.get("trailing_rule", "Exit at Target 1 (Scalp) / Target 2 (20 EMA) or 3-Day Time Stop")
    else:
        exit_model_str = f"Trailing 20 EMA ({setup.get('trailing_stop_ema', 0):.2f} PKR) or Targets"

    card = (
        f"{badge_emoji} <b>${sym} — {status_label}</b>\n"
        f"🏢 {company_esc}\n"
        f"📅 <b>Data:</b> {last_date} Close ({source_esc}) | Age: {age}d\n"
        f"🎯 <b>Strategy:</b> {strategy.title()} Setup\n"
        f"📍 <b>Trigger Condition:</b> {trigger_note}\n"
        f"💰 <b>Current Price:</b> {price:.2f} PKR\n"
        f"🛒 <b>Planned Entry:</b> {setup.get('entry_min', 0):.2f} – {setup.get('entry_max', 0):.2f} PKR\n"
        f"🛑 <b>Invalidation / SL:</b> {setup.get('stop_loss', 0):.2f} PKR (Risk: -{setup.get('risk_pct', 0)}%)\n"
        f"🎯 <b>Targets:</b> TP1: {setup.get('tp1', 0):.2f} PKR | TP2: {setup.get('tp2', 0):.2f} PKR\n"
        f"📈 <b>Exit Model:</b> {exit_model_str}\n"
        f"⚖️ <b>Reward:Risk:</b> {setup.get('rr_tp1', 0)}:1 (to TP1) | {setup.get('rr_tp2', 0)}:1 (to TP2)\n"
        f"📋 <b>Condition Checklist:</b>\n{checklist_lines}\n"
        f"⚡ <b>Status:</b> <b>{status}</b>\n"
    )
    return card
