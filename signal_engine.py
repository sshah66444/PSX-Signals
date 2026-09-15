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


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical indicator series across the entire price history."""
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

    # Dynamic rolling 20-day high and low (excluding current bar if needed, or including)
    df["Rolling_High20"] = df["High"].rolling(20).max()
    df["Rolling_Low20"] = df["Low"].rolling(20).min()

    return df


def evaluate_bar_strategy(df_ind: pd.DataFrame, bar_idx: int = -1) -> dict:
    """
    Evaluates strategy conditions on a specific historical bar.
    Single source of truth used identically by both the screener and the backtester.

    Strategies supported:
    1. BREAKOUT: Consolidation near 20-day resistance, breakout with volume.
    2. PULLBACK: Healthy uptrend, price pulling back to 20 EMA / support band.
    """
    if df_ind is None or len(df_ind) < 25:
        return {"status": "INSUFFICIENT_DATA"}

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
    rsi = float(bar["RSI"]) if not pd.isna(bar["RSI"]) else 50.0
    macd_hist = float(bar["MACD_Hist"])
    prev_macd_hist = float(prev["MACD_Hist"])
    atr = float(bar["ATR"]) if not pd.isna(bar["ATR"]) and bar["ATR"] > 0 else (price * 0.025)
    volume = float(bar["Volume"])
    vol_ma = float(bar["Vol_MA20"]) if not pd.isna(bar["Vol_MA20"]) and bar["Vol_MA20"] > 0 else 1.0

    # 20-day resistance & support measured from preceding bars
    res20 = float(df_ind["High"].iloc[max(0, idx - 20):idx].max())
    sup20 = float(df_ind["Low"].iloc[max(0, idx - 20):idx].min())

    date_val = df_ind.index[idx]
    date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)

    # Condition checks
    c_trend = price > ema20 and ema20 > ema50
    c_macd_turn = macd_hist > prev_macd_hist
    c_volume_surge = volume >= (1.20 * vol_ma)
    c_rsi_healthy = 42.0 <= rsi <= 68.0
    c_breakout_level = price >= res20
    c_liquidity = vol_ma >= 80_000

    # --- STRATEGY 1: 20-Day Range Breakout ---
    dist_to_res = (res20 - price) / price
    if c_trend and (-0.015 <= dist_to_res <= 0.03):
        strategy = "BREAKOUT"
        entry_min = round(max(res20 * 0.995, price - 0.3 * atr), 2)
        entry_max = round(max(price, res20 * 1.01), 2)
        invalidation = round(min(res20 - 0.8 * atr, ema20 - 0.3 * atr), 2)
        tp1 = round(entry_max + 1.2 * atr, 2)
        tp2 = round(entry_max + 2.4 * atr, 2)

        risk = entry_max - invalidation
        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        checklist = {
            "Trend Alignment (Price > 20 & 50 EMA)": c_trend,
            "Resistance Test / Clearance": c_breakout_level,
            "Volume Surge (Vol >= 1.20x 20MA)": c_volume_surge,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            "Momentum Health (MACD Histogram Expanding)": c_macd_turn,
            "Healthy RSI Range (42 - 68)": c_rsi_healthy,
            "Favorable Risk:Reward (>= 1.2:1)": rr_tp1 >= 1.2,
        }

        # Status determination (gated strictly on ALL checklist conditions)
        if all(checklist.values()):
            status = "TRIGGERED"
            trigger_note = f"All criteria verified: daily close ({price:.2f}) cleared resistance ({res20:.2f}) with {volume/vol_ma:.1f}x volume and R:R {rr_tp1}:1."
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
            "risk_pct": round((risk / entry_max) * 100, 1),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "trigger_note": trigger_note,
            "checklist": checklist,
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "vol_ratio": round(volume / vol_ma, 2),
        }

    # --- STRATEGY 2: Pullback to 20 EMA / Support ---
    dist_to_ema20 = (price - ema20) / ema20
    is_in_pullback_zone = price > ema50 and (-0.02 <= dist_to_ema20 <= 0.02)
    if is_in_pullback_zone and rsi < 58:
        strategy = "PULLBACK"
        entry_min = round(min(ema20 - 0.2 * atr, price * 0.99), 2)
        entry_max = round(max(price, ema20 + 0.2 * atr), 2)
        invalidation = round(min(ema50 - 0.2 * atr, entry_min - 0.9 * atr), 2)
        tp1 = round(entry_max + 1.2 * atr, 2)
        tp2 = round(entry_max + 2.2 * atr, 2)

        risk = entry_max - invalidation
        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        c_bounce_candle = price >= open_price  # Green close off support

        checklist = {
            "Macro Trend Intact (Price > 50 EMA)": price > ema50,
            "Testing 20 EMA Support Zone": True,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            "Cooling RSI (< 58, Not Overbought)": rsi < 58,
            "Bounce Confirmation (Bullish Close)": c_bounce_candle,
            "MACD Momentum Stabilization": c_macd_turn,
            "Favorable Risk:Reward (>= 1.2:1)": rr_tp1 >= 1.2,
        }

        # Status determination (gated strictly on ALL checklist conditions)
        if all(checklist.values()):
            status = "TRIGGERED"
            trigger_note = f"All criteria verified: bullish bounce at 20 EMA ({ema20:.2f}) with stabilizing MACD and R:R {rr_tp1}:1."
        elif not (rr_tp1 >= 1.2):
            status = "WATCHING"
            trigger_note = f"Support bounce detected, but disqualified: Risk:Reward ({rr_tp1}:1) is below 1.2:1 minimum threshold."
        elif not (rsi < 58):
            status = "WATCHING"
            trigger_note = f"Testing support, but RSI ({rsi:.1f}) is elevated (> 58)."
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
            "risk_pct": round((risk / entry_max) * 100, 1),
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "trigger_note": trigger_note,
            "checklist": checklist,
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "vol_ratio": round(volume / vol_ma, 2),
        }

    # Default: No clear actionable setup
    return {
        "strategy": "NONE",
        "status": "NEUTRAL",
        "date": date_str,
        "price": price,
        "trigger_note": "Consolidating / Choppy. No high-probability breakout or pullback setup.",
        "checklist": {
            "Trend Alignment": c_trend,
            "Volume Confirmation": c_volume_surge,
            "Momentum Health": c_macd_turn,
            "RSI in Range": c_rsi_healthy,
        },
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "vol_ratio": round(volume / vol_ma, 2),
    }


def generate_signal(symbol: str, df: pd.DataFrame, data_meta: dict = None) -> dict:
    """
    Evaluates the latest completed session and formats a comprehensive setup package.
    """
    if df is None or len(df) < 25:
        return {}

    df_ind = compute_all_indicators(df)
    setup = evaluate_bar_strategy(df_ind, bar_idx=-1)
    setup["symbol"] = symbol
    setup["df_indicators"] = df_ind

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
        f"⚖️ <b>Reward:Risk:</b> {setup.get('rr_tp1', 0)}:1 (to TP1) | {setup.get('rr_tp2', 0)}:1 (to TP2)\n"
        f"📋 <b>Condition Checklist:</b>\n{checklist_lines}\n"
        f"⚡ <b>Status:</b> <b>{status}</b>\n"
    )
    return card
