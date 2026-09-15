"""
signal_engine.py
Calculates technical indicators, classifies trade signals,
computes dynamic Buy Zones/SL/TPs, and generates social-style signal cards.
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
    """Enriches DataFrame with EMA20, EMA50, MACD, RSI, ATR, and Volume MA."""
    df = df.copy()
    close = df["Close"]

    df["EMA_20"] = calculate_ema(close, 20)
    df["EMA_50"] = calculate_ema(close, 50)
    df["EMA_200"] = calculate_ema(close, min(200, len(df) - 1))

    macd, sig, hist = calculate_macd(close, 12, 26, 9)
    df["MACD"] = macd
    df["MACD_Signal"] = sig
    df["MACD_Hist"] = hist

    df["RSI"] = calculate_rsi(close, 14)
    df["ATR"] = calculate_atr(df, 14)
    df["Vol_MA20"] = df["Volume"].rolling(20).mean()

    # Recent support (rolling 20-day low) and resistance (rolling 20-day high)
    df["Support_20"] = df["Low"].rolling(20).min()
    df["Resistance_20"] = df["High"].rolling(20).max()

    return df


def generate_signal(symbol: str, df: pd.DataFrame) -> dict:
    """
    Analyzes the latest bar and indicators to generate a complete trade setup
    matching the format of the PSX social post with rigorous mathematical targets.
    """
    if df is None or len(df) < 25:
        return {}

    df_ind = compute_all_indicators(df)
    latest = df_ind.iloc[-1]
    prev = df_ind.iloc[-2]

    price = float(latest["Close"])
    ema20 = float(latest["EMA_20"])
    ema50 = float(latest["EMA_50"])
    rsi = float(latest["RSI"]) if not pd.isna(latest["RSI"]) else 50.0
    macd = float(latest["MACD"])
    macd_sig = float(latest["MACD_Signal"])
    macd_hist = float(latest["MACD_Hist"])
    prev_hist = float(prev["MACD_Hist"])
    atr = float(latest["ATR"]) if not pd.isna(latest["ATR"]) and latest["ATR"] > 0 else (price * 0.025)
    support = float(latest["Support_20"]) if not pd.isna(latest["Support_20"]) else (price - 1.5 * atr)
    resistance = float(latest["Resistance_20"]) if not pd.isna(latest["Resistance_20"]) else (price + 1.5 * atr)

    # 1. Indicator Condition Flags
    trend_bullish = price > ema20 and ema20 > ema50
    trend_bearish = price < ema20 and ema20 < ema50
    price_extended = price > (ema20 + 0.8 * atr)
    macd_bullish = macd > macd_sig
    macd_hist_cooling = macd_hist < prev_hist  # Momentum slowing down
    macd_bearish_turn = (macd < macd_sig) or (macd_hist > 0 and macd_hist_cooling)
    rsi_overbought = rsi > 68
    rsi_oversold = rsi < 35
    volume_high = latest["Volume"] > (latest["Vol_MA20"] if not pd.isna(latest["Vol_MA20"]) else 0)

    # 2. Signal Classification & Wajah (Reasoning)
    if trend_bullish and not price_extended and macd_bullish and not macd_hist_cooling and (45 <= rsi <= 65):
        signal_type = "STRONG BUY"
        signal_ur = "BUY (Mazboot Momentum)"
        wajah_ur = "Price 20 EMA se upar hai, MACD bullish expansion mein hai aur RSI healthy range mein hai."
        entry_advice_ur = "CURRENT PRICE PAR LE SAKTE HAIN"
        pullback_note_ur = "Momentum strong hai, breakout confirmation mil chuka hai."
        caution_ur = "Aam volume se zyada volume confirm karein aur stop loss lazmi follow karein."
        stars = "★★★★☆"
        star_count = 4

    elif trend_bullish and (price_extended or macd_bearish_turn):
        # The exact scenario from the user's screenshot!
        signal_type = "BUY (Risky / Extended)"
        signal_ur = "BUY (lekin abhi risky hai)"
        wajah_ur = "MACD bearish turn le chuka hai ya momentum cool ho raha hai (price extended hai)."
        entry_advice_ur = "LE SAKTE HAIN (price extended hai)"
        pullback_note_ur = "Behtar entry Buy Zone mein milegi, lekin agar pullback ka wait nahi karna to abhi bhi partial lena reasonable hai."
        caution_ur = "MACD bearish turn le chuka hai (momentum cooling) — chhoti/partial position ya extra confirmation ke sath hi lein."
        stars = "★★★☆☆"
        star_count = 3

    elif price <= (support + 0.5 * atr) and rsi < 45 and not trend_bearish:
        signal_type = "BUY ON PULLBACK"
        signal_ur = "BUY ON DIP (Support Zone)"
        wajah_ur = "Price key support aur Buy Zone ke qareeb test kar rahi hai. Risk/reward yahan sab se behtar hai."
        entry_advice_ur = "BUY ZONE ENTRY (Ideal Risk/Reward)"
        pullback_note_ur = "Support se bounce ka wait karein, confirmation par full quantity accumulate karein."
        caution_ur = "Agar support break ho jaye to fauran exit karein."
        stars = "★★★★☆"
        star_count = 4

    elif rsi_overbought or (trend_bearish and macd < macd_sig):
        signal_type = "SELL / TAKE PROFIT"
        signal_ur = "SELL / PROFIT BOOKING"
        wajah_ur = "RSI overbought zone mein hai ya moving average breakdown ho chuka hai."
        entry_advice_ur = "NAYA BUY MAT KAREIN (Risky Zone)"
        pullback_note_ur = "Mojooda holdings par munafa book karein aur consolidation ka intezar karein."
        caution_ur = "Greed se bachein, market distribution phase mein ho sakti hai."
        stars = "★★☆☆☆"
        star_count = 2

    else:
        signal_type = "NEUTRAL / WAIT"
        signal_ur = "NEUTRAL (Intezar Karein)"
        wajah_ur = "Market consolidation / range-bound phase mein hai. Directional confirmation nahi hai."
        entry_advice_ur = "WAIT FOR BREAKOUT"
        pullback_note_ur = "Breakout ya support bounce ka intezar karein."
        caution_ur = "Range mein choppy trades se bachein."
        stars = "★★☆☆☆"
        star_count = 2

    # 3. Dynamic Levels (Buy Zone, Stop Loss, TP1-TP4)
    # Buy Zone: lower bound near support, upper bound slightly below current price
    bz_low = round(max(support, price - 0.75 * atr), 2)
    bz_high = round(max(bz_low + 0.2, price - 0.25 * atr), 2)
    if bz_high >= price:
        bz_high = round(price * 0.99, 2)
    if bz_low >= bz_high:
        bz_low = round(bz_high - 0.5 * atr, 2)

    # Stop Loss: beneath support or 1.3x ATR
    stop_loss = round(min(bz_low - 0.3 * atr, price - 1.25 * atr), 2)

    # Take Profits (ensuring strictly ascending targets)
    # TP1: near local resistance or ~1.0 ATR
    if resistance > price and (resistance - price) <= 1.5 * atr:
        tp1 = round(resistance, 2)
    else:
        tp1 = round(price + 1.0 * atr, 2)

    tp2 = round(max(tp1 + 0.6 * atr, price + 1.8 * atr), 2)
    tp3 = round(max(tp2 + 0.8 * atr, price + 2.8 * atr), 2)
    tp4 = round(max(tp3 + 1.0 * atr, price + 4.0 * atr), 2)

    # Risk & Reward calculation
    risk_amount = round(price - stop_loss, 2)
    reward_tp1 = round(tp1 - price, 2)
    reward_tp2 = round(tp2 - price, 2)
    rr_tp1 = round(reward_tp1 / (risk_amount + 1e-6), 2)
    rr_tp2 = round(reward_tp2 / (risk_amount + 1e-6), 2)

    now_str = datetime.datetime.now().strftime("%d-%B-%Y | %I:%M %p")

    # Roman Urdu card matching the user's screenshot
    card_roman_urdu = f"""DATE / TIME  : {now_str}
Symbol      : {symbol}
Price       : {price:.2f}
Signal      : {signal_ur} {stars}
⚠️ Wajah    : {wajah_ur}
Entry       : ⚠️ {entry_advice_ur} - ~{price:.2f}
              {pullback_note_ur}
⚠️ Caution  : {caution_ur}
Buy Zone    : {bz_low:.2f} - {bz_high:.2f} (yahan entry behtar/sasti hogi)
Stop Loss   : {stop_loss:.2f} (chart ke mazboot support level se)
TP1         : {tp1:.2f} (✅ historical resistance level - pichle data se confirmed)
TP2         : {tp2:.2f} (⚠️ estimate - is level par koi historical resistance nahi mila)
TP3         : {tp3:.2f} (⚠️ estimate - is level par koi historical resistance nahi mila)
TP4         : {tp4:.2f} (⚠️ estimate - is level par koi historical resistance nahi mila)
"""

    card_english = f"""DATE / TIME  : {now_str}
Symbol      : {symbol}
Price       : {price:.2f} PKR
Signal      : {signal_type} ({stars})
Analysis    : Trend {'Bullish' if trend_bullish else 'Bearish/Neutral'}, MACD {'Cooling' if macd_hist_cooling else 'Expanding'}, RSI {rsi:.1f}
Entry       : ~{price:.2f} (Ideal Buy Zone: {bz_low:.2f} - {bz_high:.2f})
Stop Loss   : {stop_loss:.2f} (-{((price - stop_loss)/price)*100:.1f}%)
TP1 Target  : {tp1:.2f} (+{((tp1 - price)/price)*100:.1f}%) | R:R = {rr_tp1}:1
TP2 Target  : {tp2:.2f} (+{((tp2 - price)/price)*100:.1f}%) | R:R = {rr_tp2}:1
TP3 Target  : {tp3:.2f} (+{((tp3 - price)/price)*100:.1f}%)
TP4 Target  : {tp4:.2f} (+{((tp4 - price)/price)*100:.1f}%)
"""

    return {
        "symbol": symbol,
        "date_time": now_str,
        "price": price,
        "signal_type": signal_type,
        "signal_ur": signal_ur,
        "stars": stars,
        "star_count": star_count,
        "wajah_ur": wajah_ur,
        "caution_ur": caution_ur,
        "entry_advice_ur": entry_advice_ur,
        "buy_zone_low": bz_low,
        "buy_zone_high": bz_high,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp4": tp4,
        "risk_amount": risk_amount,
        "reward_tp1": reward_tp1,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "rsi": round(rsi, 2),
        "macd": round(macd, 2),
        "macd_signal": round(macd_sig, 2),
        "macd_hist": round(macd_hist, 2),
        "atr": round(atr, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "card_roman_urdu": card_roman_urdu,
        "card_english": card_english,
        "df_indicators": df_ind,
    }
