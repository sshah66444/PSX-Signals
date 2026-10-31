"""
signal_engine.py
Unified Strategy Engine & Transparent Condition Checklist.
Serves as the single deterministic source of truth for both live alerts and backtesting.
"""

import datetime
import numpy as np
import pandas as pd
from swing_config import get_config, MIN_SCORE_BUY_NOW, MIN_SCORE_BUY_ZONE, SIGNAL_VERSION


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


def _score_evidence(evidence: dict) -> int:
    """
    Converts weighted evidence into a 0-100 conviction score.

    `evidence` maps a human-readable condition to (passed, weight). Missing
    confirmations cost points; they do not veto the setup. Only the hard risk
    gates in `_resolve_state` can veto.
    """
    total = sum(weight for _, weight in evidence.values())
    if total <= 0:
        return 0
    earned = sum(weight for passed, weight in evidence.values() if passed)
    return int(round(earned * 100.0 / total))


def _grade_for(score: int, rr_tp1: float = None) -> str:
    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    else:
        grade = "D"
    # An A-grade needs a first target worth at least 1.5R; a high conviction score
    # on 1.2:1 geometry is a B at best.
    if grade == "A" and rr_tp1 is not None and rr_tp1 < 1.5:
        grade = "B"
    return grade


def _resolve_state(
    blockers: list,
    score: int,
    trigger_met: bool,
    in_zone: bool,
    macro_ok: bool = True,
) -> str:
    """
    Maps (hard-gate blockers, score, trigger, zone) to a tradeable signal state.

    BUY_NOW   - risk gates pass, entry trigger satisfied, conviction >= 80
    BUY_ZONE  - risk gates pass, price is inside the entry zone, conviction >= 60
    WATCH     - risk gates pass but the setup is not tradeable yet
    AVOID     - a hard risk gate failed (bad data, illiquid, R:R too thin, ...)
    """
    if blockers:
        return "AVOID"
    if not macro_ok:
        return "WATCH"
    if trigger_met and score >= MIN_SCORE_BUY_NOW:
        return "BUY_NOW"
    if in_zone and score >= MIN_SCORE_BUY_ZONE:
        return "BUY_ZONE"
    return "WATCH"


def _state_note(state: str, blockers: list, score: int, grade: str, trigger_condition: str, trigger_note: str) -> str:
    """Builds the plain-English reason that accompanies a tiered signal state."""
    if state == "AVOID":
        return "Not tradeable: " + "; ".join(blockers) + "."
    if state == "WATCH":
        if blockers:
            return "Waiting: " + "; ".join(blockers) + "."
        return f"Setup forming ({score}/100, grade {grade}); not yet in the entry zone."
    if state == "BUY_ZONE":
        return f"Price is in the buy zone ({score}/100, grade {grade}) and every risk gate passes; waiting on the confirmation candle."
    return trigger_note


def compute_all_indicators(df: pd.DataFrame, df_kse: pd.DataFrame = None, mode: str = "swing_trade") -> pd.DataFrame:
    """Computes technical indicator series and benchmark relative strength across the price history."""
    df = df.copy()
    close = df["Close"]

    # Get configuration based on trading mode
    config = get_config(mode)

    ema_fast = config["ema_fast"]
    ema_medium = config["ema_medium"]
    ema_slow = config["ema_slow"]
    vol_period = config["volume_period"]
    bb_period = config["bb_period"]
    squeeze_lookback = config["squeeze_lookback"]
    resistance_lookback = config["resistance_lookback"]

    # Calculate EMAs with configurable periods
    df["EMA_20"] = calculate_ema(close, 20)  # Keep for legacy compatibility
    df["EMA_50"] = calculate_ema(close, 50)  # Keep for legacy compatibility
    df["EMA_Fast"] = calculate_ema(close, ema_fast)
    df["EMA_Medium"] = calculate_ema(close, ema_medium)
    df["EMA_200"] = calculate_ema(close, min(ema_slow, max(20, len(df) - 1)))

    macd, sig, hist = calculate_macd(close, config["macd_fast"], config["macd_slow"], config["macd_signal"])
    df["MACD"] = macd
    df["MACD_Signal"] = sig
    df["MACD_Hist"] = hist

    df["RSI"] = calculate_rsi(close, config["rsi_period"])
    df["ATR"] = calculate_atr(df, 14)
    df["Vol_MA20"] = df["Volume"].rolling(20).mean()  # Keep for legacy
    df["Vol_MA_Primary"] = df["Volume"].rolling(vol_period).mean()

    # Dynamic rolling high and low (configurable lookback)
    df["Rolling_High20"] = df["High"].rolling(20).max()  # Keep for legacy
    df["Rolling_Low20"] = df["Low"].rolling(20).min()    # Keep for legacy
    df[f"Rolling_High{resistance_lookback}"] = df["High"].rolling(resistance_lookback).max()
    df[f"Rolling_Low{resistance_lookback}"] = df["Low"].rolling(resistance_lookback).min()

    # Bollinger Bands & Volatility Compression (configurable period)
    df["BB_Middle"] = df["Close"].rolling(bb_period).mean()
    df["BB_Std"] = df["Close"].rolling(bb_period).std()
    df["BB_Upper"] = df["BB_Middle"] + (2.0 * df["BB_Std"])
    df["BB_Lower"] = df["BB_Middle"] - (2.0 * df["BB_Std"])
    df["BB_Width"] = (df["BB_Upper"] - df["BB_Lower"]) / (df["BB_Middle"] + 1e-6)
    df[f"BB_Width_Min{squeeze_lookback}"] = df["BB_Width"].rolling(squeeze_lookback, min_periods=20).min()
    # Volatility squeeze: current bandwidth is within 35% of its lookback-period tightest compression
    df["Is_Squeeze"] = df["BB_Width"] <= (1.35 * df[f"BB_Width_Min{squeeze_lookback}"])

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


# --- TWO-TRACK ARCHITECTURAL FILTERS & DEFINITIONS ---
SWING_FILTER = {
    "allowed_strategies": {"BREAKOUT", "PULLBACK"},
    "require_trend_alignment": True,        # price > EMA20 > EMA50 for BREAKOUT or > EMA50 for PULLBACK
    "require_rs_leader": True,              # RS_Ratio >= RS_MA20
    "require_macro_bullish": True,          # KSE-100 > 50 EMA
    "min_vol_ma20": 80_000,
    "min_rr_tp1": 1.2,
    "exit_mode": "trailing_ema",            # Trailing 20 EMA
    "time_stop_bars": 4,
    "holding_max_bars": 30,
}

TACTICAL_FILTER = {
    "allowed_strategies": {"MEAN_REVERSION"},
    "exit_mode": "time_stop",               # Strict 48h bounce rule
    "time_stop_bars": 2,                    # 2-day limit
    "holding_max_bars": 5,
    "min_vol_ma20": 80_000,
}


def swing_filter(candidate: dict) -> tuple[bool, str]:
    """
    Validates whether a candidate qualifies for the SWING TRACK.
    Enforces macro alignment, trend continuation, benchmark leadership, and R:R >= 1.2:1.
    """
    if not candidate or candidate.get("status") in ["INSUFFICIENT_DATA", "INDEX_OUT_OF_BOUNDS", "NONE"]:
        return False, "invalid candidate"
    strategy = candidate.get("strategy")
    if strategy not in SWING_FILTER["allowed_strategies"]:
        return False, f"strategy {strategy} not in swing set"

    checklist = candidate.get("checklist", {})

    if SWING_FILTER["require_macro_bullish"] and not checklist.get("Macro Market Gate (KSE-100 > 50 EMA)"):
        return False, "market in correction"

    if SWING_FILTER["require_rs_leader"] and not checklist.get("Relative Strength Leader (vs KSE-100)"):
        return False, "lagging benchmark"

    # Handles both BREAKOUT ("Trend Alignment...") and PULLBACK ("Macro Trend Intact...")
    trend_aligned = (
        checklist.get("Trend Alignment (Price > 20 & 50 EMA)")
        or checklist.get("Macro Trend Intact (Price > 50 EMA)")
    )
    if SWING_FILTER["require_trend_alignment"] and not trend_aligned:
        return False, "trend not aligned"

    if not checklist.get("Minimum Liquidity (20MA Vol >= 80k)"):
        return False, "insufficient liquidity"

    if candidate.get("rr_tp1", 0) < SWING_FILTER["min_rr_tp1"]:
        return False, "reward:risk below threshold"

    return True, "passed"


def tactical_filter(candidate: dict) -> tuple[bool, str]:
    """
    Validates whether a candidate qualifies for the TACTICAL TRACK.
    Enforces oversold mean-reversion with verified OHLC and adequate liquidity.
    """
    if not candidate or candidate.get("status") in ["INSUFFICIENT_DATA", "INDEX_OUT_OF_BOUNDS", "NONE"]:
        return False, "invalid candidate"
    strategy = candidate.get("strategy")
    if strategy not in TACTICAL_FILTER["allowed_strategies"]:
        return False, f"strategy {strategy} not in tactical set"

    checklist = candidate.get("checklist", {})
    if not checklist.get("Minimum Liquidity (20MA Vol >= 80k)"):
        return False, "insufficient liquidity"

    if not checklist.get("Verified Intraday OHLC (True High/Low)"):
        return False, "unverified OHLC"

    return True, "passed"


def evaluate_bar_strategy(
    df_ind: pd.DataFrame,
    bar_idx: int = -1,
    has_true_ohlc: bool = True,
    market_regime: dict = None,
    params: dict = None,
    mode: str = "swing_trade",
) -> dict:
    """
    Evaluates strategy conditions on a specific historical bar.
    Single source of truth used identically by both the screener and the backtester.

    Strategies supported:
    1. BREAKOUT: Consolidation near 20-day resistance, breakout with volume & RS leadership (SWING TRACK).
    2. PULLBACK: Healthy uptrend, price pulling back to 20 EMA / support band (SWING TRACK).
    3. MEAN_REVERSION: Oversold bounce off classical support confluence (TACTICAL TRACK).
    """
    if df_ind is None or len(df_ind) < 25:
        return {"status": "INSUFFICIENT_DATA"}

    # Get trading mode configuration
    config = get_config(mode)

    # Configurable Strategy Parameters with institutional defaults
    # Allow params override, otherwise use mode config, finally fall back to legacy defaults
    p_vol_mult = params.get("vol_surge_mult") if params and "vol_surge_mult" in params else config["volume_surge_threshold"]
    p_rsi_min = params.get("rsi_min", 42.0) if params else 42.0
    p_rsi_max = params.get("rsi_max", 68.0) if params else 68.0
    p_pb_rsi_max = params.get("pullback_rsi_max", 58.0) if params else 58.0
    p_oversold_rsi_max = params.get("oversold_rsi_max", 38.0) if params else 38.0
    p_mr_regime = params.get("mean_rev_regime", "all") if params else "all"
    p_mr_time_stop = params.get("mr_time_stop_bars") if params and "mr_time_stop_bars" in params else config["mr_time_stop_bars"]
    p_rr_min = params.get("rr_min") if params and "rr_min" in params else 1.20
    p_time_stop_bars = config["time_stop_bars"]
    p_trailing_ema = config["trailing_ema"]
    p_min_rr_tp1 = config["min_rr_tp1"]
    p_min_rr_tp2 = config["min_rr_tp2"]
    p_entry_zone_atr = config["entry_zone_atr"]

    # Target multipliers from mode config
    tp1_risk_mult = config["tp1_risk_multiplier"]
    tp2_risk_mult = config["tp2_risk_multiplier"]
    tp1_atr_mult = config["tp1_atr_multiplier"]
    tp2_atr_mult = config["tp2_atr_multiplier"]
    stop_atr_mult = config["stop_atr_multiplier"]

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

    # Mode-accurate trailing trend anchor: 20 EMA (day_trade), 50 EMA (swing_trade),
    # 100 EMA (position_trade). Computed from the same series so the trailing level in
    # the alert always matches the configured exit model instead of a fixed 20 EMA.
    trailing_ema_series = calculate_ema(df_ind["Close"], p_trailing_ema)
    trailing_ema_val = float(trailing_ema_series.iloc[idx])
    if pd.isna(trailing_ema_val) or trailing_ema_val <= 0:
        trailing_ema_val = ema50
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
    # Relative strength is only meaningful when the benchmark is fresh; scoring it
    # against a month-old KSE-100 would silently dock 15 points from every name.
    benchmark_stale = bool((market_regime or {}).get("benchmark_stale"))

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
        # Structural Invalidation: Below 50 EMA support (or res20 - stop_atr_mult*ATR, whichever is more defensive)
        invalidation = round(min(res20 - stop_atr_mult * atr, ema50 * 0.98), 2) if ema50 * 0.98 < entry_max else round(res20 - stop_atr_mult * atr, 2)
        risk = max(entry_max - invalidation, 0.01)
        tp1 = round(entry_max + max(tp1_risk_mult * risk, tp1_atr_mult * atr), 2)
        tp2 = round(entry_max + max(tp2_risk_mult * risk, tp2_atr_mult * atr), 2)
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

        # --- Tiered model (signal v2): hard risk gates + scored evidence ---
        # Hard gates are tradability/risk vetoes only. Everything else is
        # evidence that moves the 0-100 conviction score, so a stock that is
        # 8/11 - rather than 11/11 - is still surfaced as BUY ZONE / WATCH
        # with an explicit trigger price instead of being silently withheld.
        hard_gates = {
            "Verified intraday OHLC": c_true_ohlc,
            "20-day liquidity >= 80k shares": c_liquidity,
            f"Reward:Risk >= {p_min_rr_tp1:.1f}:1 to TP1": rr_tp1 >= p_min_rr_tp1,
            "Stop distance within 0.4-12% of price": 0.4 <= (risk / entry_max * 100) <= 12.0,
            "RSI not overbought (<= 80)": rsi <= 80.0,
        }
        blockers = [f"failed gate: {msg}" for msg, passed in hard_gates.items() if not passed]

        evidence = {
            "Trend Alignment (Price > 20 EMA > 50 EMA)": (c_trend, 20),
            **({} if benchmark_stale else {"Relative Strength Leader (vs KSE-100)": (c_rs_leader, 15)}),
            "Volume Surge": (c_volume_surge, 15),
            "Momentum Health (MACD Expanding)": (c_macd_turn, 15),
            "Healthy RSI": (c_rsi_healthy, 10),
            "Volatility Compression (Tight Base)": (c_squeeze, 10),
            "Within 1 ATR of the Breakout Level": (abs(res20 - price) <= atr, 10),
            "Bullish Close": (price >= open_price, 5),
        }
        score = _score_evidence(evidence)
        grade = _grade_for(score, rr_tp1)
        trigger_met = c_breakout_level and c_volume_surge
        in_zone = (-0.015 <= dist_to_res <= 0.03)
        state = _resolve_state(blockers, score, trigger_met, in_zone, macro_ok=c_market_gate)
        entry_trigger = round(res20 * 1.005, 2)
        trigger_condition = f"daily close above {entry_trigger:.2f} on volume >= {p_vol_mult:.2f}x the 50-day average"
        status = "TRIGGERED" if state == "BUY_NOW" else "WATCHING"
        trigger_note = _state_note(state, blockers, score, grade, trigger_condition, (
            f"Breakout confirmed: close {price:.2f} cleared {res20:.2f} on {volume / vol_ma:.1f}x volume "
            f"with R:R {rr_tp1}:1 (grade {grade})."
        ))
        if not c_market_gate and not blockers:
            trigger_note = f"Macro gate: KSE-100 regime is {regime_name}. Long breakouts suppressed until the index reclaims its 50 EMA."

        return {
            "strategy": strategy,
            "track": "swing",
            "exit_mode": "trailing_ema",
            "time_stop_bars": p_time_stop_bars,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(trailing_ema_val, 2),
            "trailing_ema_period": p_trailing_ema,
            "trailing_rule": f"Exit on daily close below EMA-{p_trailing_ema} (Trailing Trend Model)",
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
            "signal_version": SIGNAL_VERSION,
            "state": state,
            "score": score,
            "grade": grade,
            "entry_trigger": entry_trigger,
            "trigger_condition": trigger_condition,
            "blockers": blockers,
            "hard_gates": hard_gates,
            "evidence": {k: {"passed": v[0], "weight": v[1]} for k, v in evidence.items()},
            "mode": mode,
        }

    # --- STRATEGY 2: Pullback to the 20 EMA (shallow) or the 50 EMA (deep) ---
    # Shallow: uptrend intact, price tags the 20 EMA. Deep: long-term uptrend
    # intact, price retraces into the 50 EMA. Without the deep variant, "buy the
    # dip" can never fire while the broad market sits below its 50-day average.
    dist_to_ema20 = (price - ema20) / ema20
    dist_to_ema50 = (price - ema50) / ema50
    is_ema20_pullback = price > ema50 and (-0.02 <= dist_to_ema20 <= 0.02)
    is_ema50_pullback = price > ema200 and (-0.03 <= dist_to_ema50 <= 0.03)
    is_in_pullback_zone = is_ema20_pullback or is_ema50_pullback
    if is_in_pullback_zone and rsi < p_pb_rsi_max and not is_oversold:
        strategy = "PULLBACK"
        support_ref = ema20 if is_ema20_pullback else ema50
        support_label = "20 EMA" if is_ema20_pullback else "50 EMA"
        dist_to_support = dist_to_ema20 if is_ema20_pullback else dist_to_ema50
        entry_min = round(min(support_ref - 0.2 * atr, price * 0.99), 2)
        entry_max = round(max(price, support_ref + 0.2 * atr), 2)
        invalidation = round(min(support_ref - stop_atr_mult * atr, ema50 * 0.98), 2) if ema50 * 0.98 < entry_max else round(support_ref - stop_atr_mult * atr, 2)
        risk = max(entry_max - invalidation, 0.01)
        tp1 = round(entry_max + max(tp1_risk_mult * risk, tp1_atr_mult * atr), 2)
        tp2 = round(entry_max + max(tp2_risk_mult * risk, tp2_atr_mult * atr), 2)
        rr_tp1 = round((tp1 - entry_max) / (risk + 1e-6), 2)
        rr_tp2 = round((tp2 - entry_max) / (risk + 1e-6), 2)

        c_bounce_candle = price >= open_price  # Green close off support

        checklist = {
            "Macro Market Gate (KSE-100 > 50 EMA)": c_market_gate,
            "Relative Strength Leader (vs KSE-100)": c_rs_leader,
            "Verified Intraday OHLC (True High/Low)": c_true_ohlc,
            "Macro Trend Intact (Price > 50 EMA)": price > ema50,
            f"Testing {support_label} Support Zone": True,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            f"Cooling RSI (< {p_pb_rsi_max:.0f}, Not Overbought)": rsi < p_pb_rsi_max,
            "Bounce Confirmation (Bullish Close)": c_bounce_candle,
            "MACD Momentum Stabilization": c_macd_turn,
            f"Favorable Risk:Reward (>= {p_rr_min:.1f}:1)": rr_tp1 >= p_rr_min,
        }

        # --- Tiered model (signal v2) for "buy the dip" swing entries ---
        # A pullback no longer has to also be a breakout, an RS leader, a MACD
        # turn and a green candle on the same session. Trend structure and the
        # other confirmations are scored; only risk/tradability can veto.
        hard_gates = {
            "Verified intraday OHLC": c_true_ohlc,
            "20-day liquidity >= 80k shares": c_liquidity,
            f"Reward:Risk >= {p_min_rr_tp1:.1f}:1 to TP1": rr_tp1 >= p_min_rr_tp1,
            "Stop distance within 0.4-12% of price": 0.4 <= (risk / entry_max * 100) <= 12.0,
            "RSI not overbought (<= 80)": rsi <= 80.0,
        }
        blockers = [f"failed gate: {msg}" for msg, passed in hard_gates.items() if not passed]

        evidence = {
            "Macro Trend Intact (Price > 50 EMA)": (price > ema50, 20),
            "Long-Term Uptrend (Price > 200 EMA)": (price > ema200, 10),
            **({} if benchmark_stale else {"Relative Strength Leader (vs KSE-100)": (c_rs_leader, 15)}),
            "Volume Support": (volume >= 1.0 * vol_ma, 10),
            "MACD Momentum Stabilization": (c_macd_turn, 15),
            f"Cooling RSI (< {p_pb_rsi_max:.0f})": (rsi < p_pb_rsi_max, 10),
            "Bounce Confirmation (Bullish Close)": (c_bounce_candle, 10),
            "Reward:Risk >= 2:1": (rr_tp1 >= 2.0, 10),
        }
        score = _score_evidence(evidence)
        grade = _grade_for(score, rr_tp1)
        trigger_met = c_bounce_candle and rsi < p_pb_rsi_max and price > support_ref * 0.995
        in_zone = abs(dist_to_support) <= 0.03
        state = _resolve_state(blockers, score, trigger_met, in_zone, macro_ok=c_market_gate)
        entry_trigger = round(max(price, support_ref), 2)
        trigger_condition = f"daily close back above {entry_trigger:.2f} (the {support_label}) holding the trend"
        status = "TRIGGERED" if state == "BUY_NOW" else "WATCHING"
        trigger_note = _state_note(state, blockers, score, grade, trigger_condition, (
            f"Pullback entry confirmed: bullish close at the {support_label} ({support_ref:.2f}) "
            f"with trend structure intact and R:R {rr_tp1}:1 (grade {grade})."
        ))
        if not c_market_gate and not blockers:
            trigger_note = f"Macro gate: KSE-100 regime is {regime_name}. Long pullbacks suppressed until the index reclaims its 50 EMA."

        return {
            "strategy": strategy,
            "track": "swing",
            "exit_mode": "trailing_ema",
            "time_stop_bars": p_time_stop_bars,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(trailing_ema_val, 2),
            "trailing_ema_period": p_trailing_ema,
            "trailing_rule": f"Exit on daily close below EMA-{p_trailing_ema} (Trailing Trend Model)",
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
            "signal_version": SIGNAL_VERSION,
            "state": state,
            "score": score,
            "grade": grade,
            "entry_trigger": entry_trigger,
            "trigger_condition": trigger_condition,
            "blockers": blockers,
            "hard_gates": hard_gates,
            "evidence": {k: {"passed": v[0], "weight": v[1]} for k, v in evidence.items()},
            "mode": mode,
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

        # Macro Regime Context & Gating
        if p_mr_regime == "correction_only":
            c_mr_macro = not is_market_bullish
            macro_rule_label = "Macro Regime Gate (Market in Correction)"
        else:
            c_mr_macro = True
            macro_tag = "BULL_MARKET (High Probability Dip)" if is_market_bullish else "MARKET_CORRECTION (Counter-Trend Scalp)"
            macro_rule_label = f"Macro Context ({macro_tag})"

        checklist = {
            macro_rule_label: c_mr_macro,
            f"Oversold Momentum (RSI <= {p_oversold_rsi_max:.0f} or Lower BB)": is_oversold,
            "Classical Support Confluence (Near S1/S2/20-Low)": is_near_support,
            "Intraday Reversal / Bounce Confirmation": c_bounce_candle,
            "Momentum Stabilization (RSI / MACD Curl)": c_momentum_turn,
            "Minimum Liquidity (20MA Vol >= 80k)": c_liquidity,
            "Verified Intraday OHLC (True High/Low)": c_true_ohlc,
            f"Favorable Mean-Reversion Potential (R:R >= 1.0:1 TP1 and >= {p_min_rr_tp2:.1f}:1 TP2)": (rr_tp1 >= 1.0 and rr_tp2 >= p_min_rr_tp2),
        }

        # --- Tiered model (signal v2) for the tactical mean-reversion track ---
        # A relief bounce is judged on conviction, not on a perfect 8/8 day. The
        # risk gates below are what protect capital; the rest is scored so a
        # "one confirmation away" setup still reaches the trader as BUY ZONE
        # with the exact candle/level it needs.
        hard_gates = {
            "Verified intraday OHLC": c_true_ohlc,
            "20-day liquidity >= 80k shares": c_liquidity,
            f"Reward:Risk >= {p_min_rr_tp2:.1f}:1 to TP2": rr_tp2 >= p_min_rr_tp2,
            "First-target payoff >= 1.0:1": rr_tp1 >= 1.0,
            "Stop distance within 0.4-12% of price": 0.4 <= (risk / entry_max * 100) <= 12.0,
            "Strategy B regime condition satisfied": c_mr_macro,
        }
        blockers = [f"failed gate: {msg}" for msg, passed in hard_gates.items() if not passed]

        evidence = {
            f"Oversold Momentum (RSI <= {p_oversold_rsi_max:.0f} or Lower BB)": (is_oversold, 15),
            "Classical Support Confluence (S1/S2/20-Low)": (is_near_support, 15),
            "Intraday Reversal / Bounce Confirmation": (c_bounce_candle, 15),
            "Momentum Stabilization (RSI / MACD Curl)": (c_momentum_turn, 15),
            **({} if benchmark_stale else {"Relative Strength Leader (vs KSE-100)": (c_rs_leader, 10)}),
            "Volume Support": (volume >= 1.0 * vol_ma, 10),
            "Reward:Risk >= 2:1 to TP2": (rr_tp2 >= 2.0, 10),
            "First-Target Payoff >= 1.5:1": (rr_tp1 >= 1.5, 10),
            "Counter-Trend Context (Market in Correction)": (not is_market_bullish, 10),
        }
        score = _score_evidence(evidence)
        grade = _grade_for(score, rr_tp1)
        # A tactical relief bounce only earns BUY NOW with a real first-target
        # payoff: 1.25:1 scalp geometry stays in the buy zone instead.
        trigger_met = c_bounce_candle and c_momentum_turn and rr_tp1 >= 1.5
        in_zone = is_near_support
        state = _resolve_state(blockers, score, trigger_met, in_zone, macro_ok=True)
        entry_trigger = round(max(open_price, price) * 1.005, 2)
        confirmation_txt = "a green close or lower-wick absorption candle above the prior day's low"
        trigger_condition = f"{confirmation_txt} (confirm at/above {entry_trigger:.2f})"
        status = "TRIGGERED" if state == "BUY_NOW" else "WATCHING"
        trigger_note = _state_note(state, blockers, score, grade, trigger_condition, (
            f"Oversold {('bull-market dip' if is_market_bullish else 'counter-trend bounce')} "
            f"(RSI {rsi:.1f}) off support {pivot_s1:.2f} with a reversal candle and curling momentum; "
            f"R:R {rr_tp1}:1 to TP1, {rr_tp2}:1 to TP2 (grade {grade})."
        ))
        if p_mr_regime == "correction_only" and not c_mr_macro:
            trigger_note = (
                f"Strategy B is configured for correction_only mode, but KSE-100 is in {regime_name}. "
                "Mean-reversion bounces are suppressed."
            )

        return {
            "strategy": strategy,
            "track": "tactical",
            "exit_mode": "time_stop",
            "time_stop_bars": p_mr_time_stop,
            "status": status,
            "date": date_str,
            "price": price,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "trailing_stop_ema": round(ema20, 2),
            "trailing_rule": f"Exit at Target 1 (Scalp) / Target 2 (20 EMA) or {p_mr_time_stop}-Day Time Stop ({p_mr_time_stop * 24}h)",
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
            "signal_version": SIGNAL_VERSION,
            "state": state,
            "score": score,
            "grade": grade,
            "entry_trigger": entry_trigger,
            "trigger_condition": trigger_condition,
            "blockers": blockers,
            "hard_gates": hard_gates,
            "evidence": {k: {"passed": v[0], "weight": v[1]} for k, v in evidence.items()},
            "mode": mode,
            "trailing_ema_period": p_trailing_ema,
            "pivot": round(pivot, 2),
            "pivot_s1": round(pivot_s1, 2),
            "pivot_s2": round(pivot_s2, 2),
            "pivot_r1": round(pivot_r1, 2),
            "pivot_r2": round(pivot_r2, 2),
        }

    # Default: No clear actionable setup
    return {
        "strategy": "NONE",
        "track": "none",
        "exit_mode": "none",
        "time_stop_bars": 0,
        "status": "NEUTRAL",
        "signal_version": SIGNAL_VERSION,
        "state": "WATCH",
        "score": 0,
        "grade": "D",
        "entry_trigger": None,
        "trigger_condition": "No qualifying setup.",
        "blockers": ["no qualifying setup"],
        "evidence": {},
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
    mode: str = "swing_trade",
) -> dict:
    """
    Evaluates the latest completed session and formats a comprehensive setup package.
    """
    if df is None or len(df) < 25:
        return {}

    has_true_ohlc = data_meta.get("has_true_ohlc", True) if data_meta else True
    df_ind = compute_all_indicators(df, df_kse=df_kse, mode=mode)
    setup = evaluate_bar_strategy(
        df_ind,
        bar_idx=-1,
        has_true_ohlc=has_true_ohlc,
        market_regime=market_regime,
        params=params,
        mode=mode,
    )
    setup["symbol"] = symbol
    setup["df_indicators"] = df_ind
    setup["has_true_ohlc"] = has_true_ohlc

    # Attach data metadata
    if data_meta:
        setup["source"] = data_meta.get("source", "PSX Direct")
        setup["last_date"] = data_meta.get("last_date", str(df.index[-1].date()))
        setup["data_age_days"] = data_meta.get("data_age_days", 0)
        setup["is_settled"] = data_meta.get("is_settled", True)
        setup["expected_date"] = data_meta.get("expected_date", setup["last_date"])
        setup["freshness_note"] = data_meta.get("freshness_note", "")
    else:
        setup["source"] = "PSX Direct"
        setup["last_date"] = str(df.index[-1].date())
        setup["data_age_days"] = 0
        setup["is_settled"] = True
        setup["expected_date"] = setup["last_date"]
        setup["freshness_note"] = ""

    # Strict Session Freshness Guard:
    # If today's market has already closed, but the feed is still on the previous session,
    # prevent triggering fresh entry alerts on stale data. A BUY NOW is demoted to
    # BUY ZONE (levels stay visible, execution is paused) rather than disappearing.
    if not setup["is_settled"]:
        if setup.get("state") == "BUY_NOW":
            setup["state"] = "BUY_ZONE"
        if setup.get("status") == "TRIGGERED":
            setup["status"] = "WATCHING"
        if setup.get("state") in ("BUY_NOW", "BUY_ZONE"):
            setup["trigger_note"] = (
                f"Setup matched on {setup['last_date']} close, but today's EOD bar ({setup['expected_date']}) "
                f"is pending exchange settlement. Levels shown; execution paused until the latest bar lands."
            )

    return setup


def format_actionable_card(setup: dict, company_name: str = "", mode: str = None) -> str:
    """
    Formats the transparent, actionable Telegram card requested by the user.

    The exit model is rendered from the active trading mode, so a swing alert
    always quotes the swing trailing EMA (default 50) and time stop instead of
    inheriting day-trading defaults.
    """
    # Resolve the mode used for this setup: explicit argument -> setup payload -> default
    resolved_mode = mode or setup.get("mode") or "swing_trade"
    try:
        cfg = get_config(resolved_mode)
    except ValueError:
        cfg = get_config()
        resolved_mode = "swing_trade"
    trailing_period = setup.get("trailing_ema_period", cfg["trailing_ema"])
    time_stop_bars = setup.get("time_stop_bars", cfg["time_stop_bars"])
    holding_max_bars = cfg["holding_max_bars"]

    sym = setup.get("symbol", "")
    strategy = setup.get("strategy", "NONE")
    status = setup.get("status", "NEUTRAL")
    price = setup.get("price", 0.0)
    source = setup.get("source", "PSX Feed")
    last_date = setup.get("last_date", "Today")
    age = setup.get("data_age_days", 0)
    is_settled = setup.get("is_settled", True)
    expected_date = setup.get("expected_date", "")

    if status == "NEUTRAL" or strategy == "NONE":
        return f"⚪ <b>${sym}</b> — {company_name}\nStatus: NEUTRAL (No high-probability setup)."

    badge_emoji = "🟢" if status == "TRIGGERED" else "🟡"
    status_label = "Triggered (Confirmed)" if status == "TRIGGERED" else "Watching (Waiting for Confirmation)"
    import html

    def esc(text: str) -> str:
        return html.escape(str(text), quote=False)

    # --- Tiered signal presentation (v2) ---
    state = setup.get("state")
    score = setup.get("score")
    grade = setup.get("grade")
    entry_trigger = setup.get("entry_trigger")
    trigger_condition = setup.get("trigger_condition") or ""
    blockers = setup.get("blockers") or []

    if state:
        state_display = {
            "BUY_NOW": ("🟢", "BUY NOW"),
            "BUY_ZONE": ("🟡", "BUY ZONE — awaiting confirmation"),
            "WATCH": ("⚪", "WATCH"),
            "AVOID": ("⛔", "NOT TRADEABLE"),
        }.get(state, ("⚪", state))
        badge_emoji, status_label = state_display[0], state_display[1]

    checklist = setup.get("checklist", {})
    evidence = setup.get("evidence") or {}
    hard_gates = setup.get("hard_gates") or {}
    evidence_lines = []
    for label, meta in evidence.items():
        passed = bool((meta or {}).get("passed"))
        weight = (meta or {}).get("weight") or 0
        mark = "[✓]" if passed else "[✗]"
        evidence_lines.append(f"   {mark} {esc(label)}  <i>(+{weight})</i>")
    gate_lines = []
    for label, passed in hard_gates.items():
        mark = "[✓]" if passed else "[✗]"
        gate_lines.append(f"   {mark} {esc(label)}")
    checklist_lines = "\n".join(evidence_lines) if evidence_lines else "\n".join(
        f"   {'[✓]' if passed else '[✗]'} {esc(k)}" for k, passed in checklist.items()
    )
    gate_block = "\n".join(gate_lines)

    trigger_note = esc(setup.get('trigger_note', ''))
    source_esc = esc(source)
    company_esc = esc(company_name)

    if strategy == "MEAN_REVERSION":
        exit_model_str = setup.get(
            "trailing_rule",
            f"Exit at TP1 (scalp) / TP2, or {time_stop_bars}-session time stop",
        )
        mode_detail = f"time stop {time_stop_bars} sessions, mean-reversion exit on TP1/TP2"
    else:
        exit_model_str = (
            f"Trail daily close below EMA-{trailing_period} "
            f"({setup.get('trailing_stop_ema', 0):.2f} PKR); partial exits at TP1/TP2; "
            f"hard time stop after {time_stop_bars} sessions (max hold {holding_max_bars})"
        )
        mode_detail = f"time stop {time_stop_bars} sessions, trail EMA-{trailing_period}"

    pending_banner = ""
    if not is_settled:
        pending_banner = (
            f"⏳ <b>NOTICE:</b> Today's ({esc(expected_date)}) EOD settlement is pending. "
            f"Showing {esc(last_date)} data.\n"
        )

    track = setup.get("track", "swing" if strategy in ["BREAKOUT", "PULLBACK"] else "tactical")
    track_badge = "SWING TRACK" if track == "swing" else "TACTICAL TRACK"

    # Conviction + the exact price at which this becomes a buy
    signal_line = ""
    if state and score is not None:
        signal_line = f"🎚 <b>Conviction:</b> grade {grade} ({score}/100)\n"

    trigger_line = ""
    if state == "BUY_NOW":
        trigger_line = "✅ <b>Trigger:</b> MET — setup is confirmed for entry\n"
    elif state == "BUY_ZONE" and entry_trigger:
        trigger_line = f"🔔 <b>Buy Trigger:</b> {esc(trigger_condition)}\n"
    elif state == "AVOID":
        trigger_line = f"⛔ <b>Blocked by:</b> {esc('; '.join(blockers))}\n"

    card = (
        f"{badge_emoji} <b>${sym} — {status_label}</b>\n"
        f"🏢 {company_esc}\n"
        f"{pending_banner}"
        f"📅 <b>Data:</b> {last_date} Close ({source_esc}) | Age: {age}d\n"
        f"🎯 <b>Strategy:</b> {strategy.title()} Setup | <b>{track_badge}</b>\n"
        f"⚙️ <b>Mode:</b> {cfg['name']} ({mode_detail})\n"
        f"{signal_line}"
        f"{trigger_line}"
        f"📍 <b>Reasoning:</b> {trigger_note}\n"
        f"💰 <b>Current Price:</b> {price:.2f} PKR\n"
        f"🛒 <b>Planned Entry:</b> {setup.get('entry_min', 0):.2f} – {setup.get('entry_max', 0):.2f} PKR\n"
        f"🛑 <b>Invalidation / SL:</b> {setup.get('stop_loss', 0):.2f} PKR (Risk: -{setup.get('risk_pct', 0)}%)\n"
        f"🎯 <b>Targets:</b> TP1: {setup.get('tp1', 0):.2f} PKR | TP2: {setup.get('tp2', 0):.2f} PKR\n"
        f"📈 <b>Exit Model:</b> {exit_model_str}\n"
        f"⚖️ <b>Reward:Risk:</b> {setup.get('rr_tp1', 0)}:1 (to TP1) | {setup.get('rr_tp2', 0)}:1 (to TP2)\n"
        f"{'🛡 <b>Risk Gates:</b>' + chr(10) + gate_block + chr(10) if gate_block else ''}"
        f"📋 <b>Evidence (weighted):</b>\n{checklist_lines}\n"
        f"⚡ <b>Status:</b> <b>{status}</b> | <b>{state or 'n/a'}</b>\n"
    )
    return card
