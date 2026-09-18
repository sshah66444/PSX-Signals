"""
backtester.py
Institutional Quantitative Backtesting & Validation Suite for PSX AlphaSignals.

Features:
- Realistic PSX Microstructure: Next-Day Open (T+1 Open) fills, volume-tiered spread & slippage, PSX ±7.5% circuit breakers.
- Non-parametric Bootstrap Resampling (1,000 iterations) for 95% Confidence Intervals.
- Minimum Sample Size (N >= 30) statistical adequacy gating.
- Multi-dimensional Parameter Sensitivity Grid (Stability Plateau analysis).
- Rolling Walk-Forward Cross-Validation (In-Sample vs Out-of-Sample testing).
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from signal_engine import compute_all_indicators, evaluate_bar_strategy
from data_engine import check_has_true_ohlc, get_symbol_sector, get_symbol_name


def calculate_execution_friction(adv_20: float) -> tuple[float, float, str]:
    """
    Computes realistic bid-ask spread and execution slippage based on 20-day ADV.
    PSX market microstructure:
    - Tier 1 (ADV >= 2,000,000 shares): Highly liquid blue chips (OGDC, PPL, PRL, KEL).
      Spread: 0.15%, Slippage: 0.10%.
    - Tier 2 (500,000 <= ADV < 2,000,000 shares): Medium liquid blue/mid-caps (MEBL, SYS, LUCK).
      Spread: 0.35%, Slippage: 0.20%.
    - Tier 3 (ADV < 500,000 shares): Thinly traded stocks.
      Spread: 0.65%, Slippage: 0.35%.
    Returns (spread_pct, slippage_pct, tier_name).
    """
    if adv_20 >= 2_000_000:
        return 0.15, 0.10, "Tier 1 (High Liquidity, ADV >= 2M)"
    elif adv_20 >= 500_000:
        return 0.35, 0.20, "Tier 2 (Moderate Liquidity, 500k-2M)"
    else:
        return 0.65, 0.35, "Tier 3 (Thin Liquidity, ADV < 500k)"


def evaluate_sample_adequacy(n_trades: int) -> dict:
    """
    Evaluates whether the sample size of closed trades is sufficient
    for statistical validity.
    """
    if n_trades == 0:
        return {
            "status": "NO_TRADES",
            "badge": "⚪ No Trades",
            "is_adequate": False,
            "warning": "No historical setups triggered in this period.",
        }
    elif n_trades < 15:
        return {
            "status": "CRITICAL_INSUFFICIENT_SAMPLE",
            "badge": "🔴 Sample Size Critically Low",
            "is_adequate": False,
            "warning": f"Only {n_trades} closed trade(s). Results are statistically dominated by noise. Minimum 30 closed trades required for meaningful confidence.",
        }
    elif n_trades < 30:
        return {
            "status": "LOW_STATISTICAL_CONFIDENCE",
            "badge": "🟡 Sample Size Low",
            "is_adequate": False,
            "warning": f"Only {n_trades} closed trade(s). High variance in win-rate estimates (N < 30). Interpret with caution.",
        }
    else:
        return {
            "status": "STATISTICALLY_ADEQUATE",
            "badge": "🟢 Sample Size Adequate",
            "is_adequate": True,
            "warning": f"Sample size ({n_trades} trades) meets statistical significance standards (N >= 30).",
        }


def run_bootstrap_simulation(
    trades_df: pd.DataFrame,
    n_iterations: int = 1000,
    confidence_level: float = 0.95,
) -> dict:
    """
    Performs non-parametric bootstrap resampling over closed trade returns
    to calculate robust confidence intervals for Win Rate, Net P&L, and Profit Factor.
    """
    if trades_df is None or len(trades_df) < 3:
        return {
            "iterations": n_iterations,
            "confidence_level": confidence_level,
            "win_rate_ci": (0.0, 0.0),
            "tp1_rate_ci": (0.0, 0.0),
            "net_pnl_ci": (0.0, 0.0),
            "profit_factor_ci": (0.0, 0.0),
            "median_win_rate": 0.0,
            "median_net_pnl": 0.0,
        }

    returns = trades_df["net_pnl_pct"].values
    wins = (returns > 0).astype(int)
    tp1s = trades_df["tp1_reached"].values.astype(int)
    n = len(returns)

    np.random.seed(42)
    boot_wins = []
    boot_tp1s = []
    boot_pnls = []
    boot_pfs = []

    for _ in range(n_iterations):
        idx = np.random.choice(n, size=n, replace=True)
        sample_ret = returns[idx]
        boot_wins.append(np.mean(wins[idx]) * 100.0)
        boot_tp1s.append(np.mean(tp1s[idx]) * 100.0)
        boot_pnls.append(np.sum(sample_ret))
        gp = np.sum(sample_ret[sample_ret > 0])
        gl = abs(np.sum(sample_ret[sample_ret < 0]))
        boot_pfs.append(round(float(gp / gl), 2) if gl > 0 else 99.0)

    alpha = (1.0 - confidence_level) / 2.0
    low_pct = alpha * 100.0
    high_pct = (1.0 - alpha) * 100.0

    return {
        "iterations": n_iterations,
        "confidence_level": confidence_level,
        "win_rate_ci": (round(float(np.percentile(boot_wins, low_pct)), 1), round(float(np.percentile(boot_wins, high_pct)), 1)),
        "tp1_rate_ci": (round(float(np.percentile(boot_tp1s, low_pct)), 1), round(float(np.percentile(boot_tp1s, high_pct)), 1)),
        "net_pnl_ci": (round(float(np.percentile(boot_pnls, low_pct)), 2), round(float(np.percentile(boot_pnls, high_pct)), 2)),
        "profit_factor_ci": (round(float(np.percentile(boot_pfs, low_pct)), 2), round(float(np.percentile(boot_pfs, high_pct)), 2)),
        "median_win_rate": round(float(np.median(boot_wins)), 1),
        "median_net_pnl": round(float(np.median(boot_pnls)), 2),
    }


def run_signal_backtest(
    df: pd.DataFrame,
    symbol: str,
    holding_max_bars: int = 30,
    broker_fee_pct: float = 0.35,  # Round-trip commission + CDC/SECP charges
    df_kse: pd.DataFrame = None,
    time_stop_bars: int = 4,
    use_next_day_open: bool = True,
    enforce_circuit_limits: bool = True,
    simulate_circuit_trapping: bool = False,
    strategy_params: dict = None,
    exit_mode: str = "trailing_ema",  # "trailing_ema" (default, lets winners run) or "fixed_tp" (legacy)
) -> dict:
    """
    Backtests strategy performance bar-by-bar across historical data.
    Uses evaluate_bar_strategy directly to guarantee identical rules.
    Strictly requires verified intraday High/Low data.

    Execution Realism:
    - exit_mode:
      * "trailing_ema" (default): Lets winners compound along 20 EMA trend; exits on daily close < 20 EMA.
      * "fixed_tp": Legacy scale-out model (50% booked at TP1, SL moved to breakeven, remainder to TP2).
    - use_next_day_open: When True, signal at close of bar T triggers entry on Open of bar T+1.
    - enforce_circuit_limits: PSX ±7.5% (min PKR 1.00) limits enforce realistic fills:
      * Upper-circuit locked opens are discarded as unfillable.
      * Overnight gap-downs or limit-down stops are filled at limit down prices.
    - Dynamic spread and slippage deducted per trade based on 20MA volume tier.
    """
    if df is None or len(df) < 30:
        return {"error": "Insufficient historical data (minimum 30 bars required)."}

    if not check_has_true_ohlc(df):
        return {
            "error": "Backtesting requires verified intraday High/Low prices. "
                     "The provided dataset contains approximated OHLC (Close/Open only), "
                     "which invalidates ATR, stop-loss triggers, and target simulations."
        }

    df_ind = compute_all_indicators(df, df_kse=df_kse)
    n_bars = len(df_ind)

    # Pre-align KSE-100 series for fast O(1) regime lookup if provided
    kse_close_series = None
    kse_ema50_series = None
    if df_kse is not None and not df_kse.empty:
        if "EMA_50" not in df_kse.columns:
            df_kse = df_kse.copy()
            df_kse["EMA_50"] = df_kse["Close"].ewm(span=50, adjust=False).mean()
        kse_close_series = df_kse["Close"].reindex(df_ind.index, method="ffill")
        kse_ema50_series = df_kse["EMA_50"].reindex(df_ind.index, method="ffill")

    trades = []
    in_trade = False
    active = {}
    pending_entry = None
    unresolved_trade = None
    circuit_lock_discards = 0
    gap_discards = 0

    for i in range(25, n_bars):
        bar = df_ind.iloc[i]
        date = df_ind.index[i]
        open_p = float(bar["Open"])
        high_p = float(bar["High"])
        low_p = float(bar["Low"])
        close_p = float(bar["Close"])
        adv_p = float(bar["Vol_MA20"]) if (not pd.isna(bar["Vol_MA20"]) and bar["Vol_MA20"] > 0) else 100_000.0
        prev_c = float(df_ind["Close"].iloc[i - 1])

        # PSX Circuit Limits for this session: ±7.5% or PKR 1.00 whichever is higher
        limit_offset = max(prev_c * 0.075, 1.0)
        limit_up = prev_c + limit_offset
        limit_dn = max(prev_c - limit_offset, 0.01)

        # -------------------------------------------------------------------------
        # 1. PROCESS PENDING NEXT-DAY OPEN ENTRY (FROM PREVIOUS SESSION TRIGGER)
        # -------------------------------------------------------------------------
        if pending_entry is not None and not in_trade:
            # Check Upper Circuit Lock on Open:
            # If stock opens locked at or near upper limit with zero selling liquidity
            is_upper_locked = enforce_circuit_limits and (open_p >= limit_up - 0.05 and low_p >= limit_up - 0.05)
            if is_upper_locked:
                circuit_lock_discards += 1
                pending_entry = None
            # Check Gap past profit target:
            elif open_p >= pending_entry["tp1"]:
                gap_discards += 1
                pending_entry = None
            # Check Gap below initial stop loss:
            elif open_p <= pending_entry["stop_loss"]:
                gap_discards += 1
                pending_entry = None
            else:
                # Valid Next-Day Open Entry with volume-tiered friction
                spread_pct, slip_pct, tier = calculate_execution_friction(adv_p)
                entry_friction_pct = (spread_pct / 2.0) + slip_pct
                fill_price = round(open_p * (1.0 + entry_friction_pct / 100.0), 2)
                sl = pending_entry["stop_loss"]

                if fill_price > sl:
                    risk = fill_price - sl
                    tp1 = round(fill_price + max(1.35 * risk, 1.4 * pending_entry["atr"]), 2)
                    tp2 = round(fill_price + max(2.5 * risk, 2.6 * pending_entry["atr"]), 2)
                    active = {
                        "symbol": symbol,
                        "strategy": pending_entry["strategy"],
                        "signal_date": pending_entry["signal_date"],
                        "entry_date": date,
                        "entry_price": fill_price,
                        "raw_open": open_p,
                        "spread_pct": spread_pct,
                        "slippage_pct": slip_pct,
                        "liquidity_tier": tier,
                        "stop_loss": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "atr": pending_entry["atr"],
                        "bars_held": 0,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "ambiguous_bars": 0,
                    }
                    in_trade = True
                pending_entry = None

        # -------------------------------------------------------------------------
        # 2. ACTIVE TRADE MANAGEMENT & LIFECYCLE AUDITING
        # -------------------------------------------------------------------------
        if in_trade:
            active["bars_held"] += 1
            tp1 = active["tp1"]
            tp2 = active["tp2"]
            sl = active["stop_loss"]
            entry = active["entry_price"]

            # Exit friction costs
            exit_spread, exit_slip, _ = calculate_execution_friction(adv_p)
            total_exit_friction = broker_fee_pct + (exit_spread / 2.0) + exit_slip

            # Circuit breaker check: Was the day locked at lower circuit?
            is_lower_locked = enforce_circuit_limits and (close_p <= limit_dn + 0.05 and high_p <= limit_dn + 0.05)

            # If active trade was trapped in a lower circuit lock in prior session(s)
            if simulate_circuit_trapping and active.get("trapped_exit"):
                if is_lower_locked:
                    active["trapped_exit"]["lock_bars"] += 1
                    continue
                else:
                    lock_bars = active["trapped_exit"]["lock_bars"]
                    orig_reason = active["trapped_exit"]["reason"]
                    actual_exit = min(open_p, limit_up)
                    emergency_friction = total_exit_friction + 0.20
                    gross_return = (actual_exit - entry) / entry
                    net_return = gross_return - (emergency_friction / 100.0)
                    outcome_msg = f"{orig_reason} (Trapped in Lower Lock {lock_bars} bar(s), Unlocked)"
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_exit,
                        "outcome": outcome_msg,
                        "tp1_reached": active["tp1_hit"],
                        "tp2_reached": active["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                        "lock_bars_trapped": lock_bars,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

            # -----------------------------------------------------------------
            # A. TRAILING 20 EMA EXIT MODE (PSX TREND MODEL)
            # -----------------------------------------------------------------
            if exit_mode == "trailing_ema":
                # Snapshot milestone state from BEFORE this bar's touches are applied.
                # This is what lets us tell "genuinely reached the target on an earlier,
                # unambiguous bar" apart from "only touched it on the same bar the stop
                # was also breached" -- the latter must not be credited as a target hit.
                tp1_hit_before = active["tp1_hit"]
                tp2_hit_before = active["tp2_hit"]

                hit_tp1_this_bar = high_p >= tp1
                hit_tp2_this_bar = high_p >= tp2
                hit_sl_this_bar = low_p <= sl

                # Ambiguous same-bar event: this bar is the FIRST time either target is
                # touched AND the stop is also breached in the same session. Mirrors the
                # legacy fixed_tp branch's conservative "stop hit first" assumption --
                # without this check, a losing stopped-out trade could still be counted
                # as a TP1/TP2 "hit" in the aggregate hit-rate stats.
                newly_ambiguous = hit_sl_this_bar and (
                    (hit_tp1_this_bar and not tp1_hit_before) or (hit_tp2_this_bar and not tp2_hit_before)
                )

                if newly_ambiguous:
                    active["ambiguous_bars"] += 1
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "STOP LOSS HIT (Ambiguous Bar)",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                        outcome_msg = "STOP LOSS HIT (Ambiguous Bar, Lower Circuit)"
                    else:
                        outcome_msg = "STOP LOSS HIT (Ambiguous Bar)"
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_sl,
                        "outcome": outcome_msg,
                        # Only credit target touches confirmed on a strictly earlier,
                        # non-ambiguous bar -- never the ambiguous bar's own touch.
                        "tp1_reached": tp1_hit_before,
                        "tp2_reached": tp2_hit_before,
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": True,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

                # Milestone touch checks (track without capping upside prematurely).
                # Safe to apply now: reaching this point means any touch this bar did
                # NOT coincide with a same-bar stop breach.
                if hit_tp1_this_bar:
                    active["tp1_hit"] = True
                if hit_tp2_this_bar:
                    active["tp2_hit"] = True

                # Case 1: Hard Stop Loss Hit (unambiguous -- no new target touch this bar)
                if hit_sl_this_bar:
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "STOP LOSS HIT",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                        outcome_msg = "STOP LOSS HIT (Lower Circuit Limit)"
                    else:
                        outcome_msg = "STOP LOSS HIT"
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_sl,
                        "outcome": outcome_msg,
                        "tp1_reached": active["tp1_hit"],
                        "tp2_reached": active["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

                # Case 2: Trailing 20 EMA Exit (after minimum 3-bar initial holding grace period)
                ema20_val = float(bar["EMA_20"]) if "EMA_20" in bar and pd.notna(bar["EMA_20"]) else None
                if active["bars_held"] >= 3 and ema20_val is not None and close_p < ema20_val:
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "TRAIL_20EMA EXIT",
                            "lock_bars": 1,
                        }
                        continue
                    actual_exit = close_p
                    if is_lower_locked:
                        actual_exit = min(actual_exit, limit_dn)
                    gross_return = (actual_exit - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_exit,
                        "outcome": "TRAIL_20EMA EXIT",
                        # Reflects only genuine price-level touches, never profitability --
                        # a trade can be profitable at exit without ever reaching TP1.
                        "tp1_reached": active["tp1_hit"],
                        "tp2_reached": active["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

                # Case 3: Optional Time-Stop Exit (if time_stop_bars > 0 explicitly requested or MEAN_REVERSION 3-day limit)
                is_mr_stalled = (active.get("strategy") == "MEAN_REVERSION" and active["bars_held"] >= 3)
                if (time_stop_bars > 0 and not active["tp1_hit"] and active["bars_held"] >= time_stop_bars) or is_mr_stalled:
                    stalled_threshold = entry + (0.15 * active.get("atr", 0.0))
                    should_exit_stalled = is_mr_stalled or (close_p <= stalled_threshold)
                    if should_exit_stalled:
                        reason_label = "TIME-STOP EXIT (Mean Reversion 3-Day Limit)" if is_mr_stalled else "TIME-STOP EXIT (Stalled Momentum)"
                        if is_lower_locked and simulate_circuit_trapping:
                            active["trapped_exit"] = {
                                "trigger_date": date,
                                "reason": reason_label,
                                "lock_bars": 1,
                            }
                            continue
                        exit_price = close_p
                        if is_lower_locked:
                            exit_price = min(exit_price, limit_dn)
                        gross_return = (exit_price - entry) / entry
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": exit_price,
                            "outcome": reason_label,
                            "tp1_reached": active.get("tp1_hit", False),
                            "tp2_reached": active.get("tp2_hit", False),
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": False,
                        })
                        trades.append(active)
                        in_trade = False
                        continue

                # Case 4: Maximum Holding Limit Reached
                if active["bars_held"] >= holding_max_bars:
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "EXPIRED (Holding Limit)",
                            "lock_bars": 1,
                        }
                        continue
                    exit_price = close_p
                    if is_lower_locked:
                        exit_price = min(exit_price, limit_dn)
                    gross_return = (exit_price - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": exit_price,
                        "outcome": "EXPIRED (Holding Limit)",
                        # Reflects only genuine price-level touches, never profitability.
                        "tp1_reached": active["tp1_hit"],
                        "tp2_reached": active["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

            # -----------------------------------------------------------------
            # B. LEGACY FIXED TP SCALE-OUT MODE ("fixed_tp")
            # -----------------------------------------------------------------
            else:
                # Ambiguous candle check: touched both target and stop on the same bar
                hit_tp1_this_bar = high_p >= tp1
                hit_sl_this_bar = low_p <= sl

                if hit_tp1_this_bar and hit_sl_this_bar and not active["tp1_hit"]:
                    active["ambiguous_bars"] += 1
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "STOP LOSS HIT (Ambiguous Bar)",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_sl,
                        "outcome": "STOP LOSS HIT (Ambiguous Bar)",
                        "tp1_reached": False,
                        "tp2_reached": False,
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": True,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

                # Case 1: Stop Loss Hit (before TP1)
                if low_p <= sl and not active["tp1_hit"]:
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "STOP LOSS HIT",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                        outcome_msg = "STOP LOSS HIT (Lower Circuit Limit)"
                    else:
                        outcome_msg = "STOP LOSS HIT"
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": actual_sl,
                        "outcome": outcome_msg,
                        "tp1_reached": False,
                        "tp2_reached": False,
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

                # Case 2: TP1 Reached (Scale-out model: 50% booked, SL moved to breakeven)
                if high_p >= tp1 and not active["tp1_hit"]:
                    active["tp1_hit"] = True
                    active["stop_loss"] = entry  # Trailing stop to breakeven
                    tp1_fill = max(tp1, open_p)  # Account for favorable opening gaps

                    # Subcase 2a: If high also reached TP2 on same bar
                    if high_p >= tp2:
                        active["tp2_hit"] = True
                        tp2_fill = max(tp2, open_p)
                        p1_ret = (tp1_fill - entry) / entry
                        p2_ret = (tp2_fill - entry) / entry
                        gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": tp2_fill,
                            "outcome": "TP1 & TP2 HIT (Full Target)",
                            "tp1_reached": True,
                            "tp2_reached": True,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": False,
                        })
                        trades.append(active)
                        in_trade = False
                        continue

                    # Subcase 2b: Same-bar retracement to Breakeven (Low <= entry)
                    if low_p <= entry:
                        active["ambiguous_bars"] += 1
                        p1_ret = (tp1_fill - entry) / entry
                        p2_ret = 0.0  # Breakeven on second half
                        gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": entry,
                            "outcome": "TP1 HIT + SAME-DAY BREAKEVEN",
                            "tp1_reached": True,
                            "tp2_reached": False,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": True,
                        })
                        trades.append(active)
                        in_trade = False
                        continue

                # Case 3: Already hit TP1, now checking TP2 or Breakeven
                elif active["tp1_hit"]:
                    if high_p >= tp2:
                        active["tp2_hit"] = True
                        tp2_fill = max(tp2, open_p)
                        p1_ret = (tp1 - entry) / entry
                        p2_ret = (tp2_fill - entry) / entry
                        gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": tp2_fill,
                            "outcome": "TP1 + TP2 HIT",
                            "tp1_reached": True,
                            "tp2_reached": True,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": False,
                        })
                        trades.append(active)
                        in_trade = False
                        continue
                    elif low_p <= active["stop_loss"]:
                        if is_lower_locked and simulate_circuit_trapping:
                            active["trapped_exit"] = {
                                "trigger_date": date,
                                "reason": "TP1 HIT (Trailing Stopped at BE)",
                                "lock_bars": 1,
                            }
                            continue
                        p1_ret = (tp1 - entry) / entry
                        p2_ret = 0.0  # Stopped at Breakeven
                        gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": active["stop_loss"],
                            "outcome": "TP1 HIT (Trailing Stopped at BE)",
                            "tp1_reached": True,
                            "tp2_reached": False,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": False,
                        })
                        trades.append(active)
                        in_trade = False
                        continue

                # Case 4: Time-Stop Exit (Stalled Momentum before TP1 or MEAN_REVERSION 3-day limit)
                is_mr_stalled_fix = (active.get("strategy") == "MEAN_REVERSION" and active["bars_held"] >= 3)
                if (time_stop_bars > 0 and not active["tp1_hit"] and active["bars_held"] >= time_stop_bars) or is_mr_stalled_fix:
                    stalled_threshold = entry + (0.15 * active.get("atr", 0.0))
                    should_exit_stalled = is_mr_stalled_fix or (close_p <= stalled_threshold)
                    if should_exit_stalled:
                        reason_label = "TIME-STOP EXIT (Mean Reversion 3-Day Limit)" if is_mr_stalled_fix else "TIME-STOP EXIT (Stalled Momentum)"
                        if is_lower_locked and simulate_circuit_trapping:
                            active["trapped_exit"] = {
                                "trigger_date": date,
                                "reason": reason_label,
                                "lock_bars": 1,
                            }
                            continue
                        exit_price = close_p
                        if is_lower_locked:
                            exit_price = min(exit_price, limit_dn)
                        gross_return = (exit_price - entry) / entry
                        net_return = gross_return - (total_exit_friction / 100.0)
                        active.update({
                            "exit_date": date,
                            "exit_price": exit_price,
                            "outcome": reason_label,
                            "tp1_reached": active.get("tp1_hit", False),
                            "tp2_reached": False,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "is_ambiguous": False,
                        })
                        trades.append(active)
                        in_trade = False
                        continue

                # Case 5: Expiration (Holding limit reached)
                if active["bars_held"] >= holding_max_bars:
                    if is_lower_locked and simulate_circuit_trapping:
                        active["trapped_exit"] = {
                            "trigger_date": date,
                            "reason": "EXPIRED (Holding Limit)",
                            "lock_bars": 1,
                        }
                        continue
                    exit_price = close_p
                    if is_lower_locked:
                        exit_price = min(exit_price, limit_dn)
                    if active["tp1_hit"]:
                        p1_ret = (tp1 - entry) / entry
                        p2_ret = (exit_price - entry) / entry
                        gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                    else:
                        gross_return = (exit_price - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": exit_price,
                        "outcome": "EXPIRED (Holding Limit)",
                        "tp1_reached": active["tp1_hit"],
                        "tp2_reached": False,
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "is_ambiguous": False,
                    })
                    trades.append(active)
                    in_trade = False
                    continue

        # -------------------------------------------------------------------------
        # 3. EVALUATE BAR FOR NEW TRADE TRIGGER
        # -------------------------------------------------------------------------
        if not in_trade and pending_entry is None:
            hist_regime = None
            if kse_close_series is not None and i < len(kse_close_series):
                kse_c = kse_close_series.iloc[i]
                kse_e50 = kse_ema50_series.iloc[i]
                if pd.notna(kse_c) and pd.notna(kse_e50):
                    is_bull = bool(kse_c >= kse_e50)
                    hist_regime = {
                        "is_bullish": is_bull,
                        "regime": "BULL_MARKET" if is_bull else "MARKET_CORRECTION",
                    }

            setup = evaluate_bar_strategy(
                df_ind,
                bar_idx=i,
                market_regime=hist_regime,
                params=strategy_params,
            )
            if setup.get("status") == "TRIGGERED":
                if use_next_day_open:
                    pending_entry = {
                        "strategy": setup["strategy"],
                        "signal_date": date,
                        "stop_loss": setup["stop_loss"],
                        "tp1": setup["tp1"],
                        "tp2": setup["tp2"],
                        "atr": setup.get("atr", 0.0),
                    }
                else:
                    # Legacy same-bar close execution
                    in_trade = True
                    active = {
                        "symbol": symbol,
                        "strategy": setup["strategy"],
                        "signal_date": date,
                        "entry_date": date,
                        "entry_price": setup["entry_max"],
                        "stop_loss": setup["stop_loss"],
                        "tp1": setup["tp1"],
                        "tp2": setup["tp2"],
                        "atr": setup.get("atr", 0.0),
                        "bars_held": 0,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "ambiguous_bars": 0,
                    }

    # If trade remained active up to the final historical bar
    if in_trade:
        if simulate_circuit_trapping and active.get("trapped_exit"):
            lock_bars = active["trapped_exit"]["lock_bars"]
            orig_reason = active["trapped_exit"]["reason"]
            actual_exit = float(df_ind["Close"].iloc[-1])
            gross_return = (actual_exit - active["entry_price"]) / active["entry_price"]
            net_return = gross_return - (total_exit_friction / 100.0)
            active.update({
                "exit_date": df_ind.index[-1],
                "exit_price": actual_exit,
                "outcome": f"{orig_reason} (Trapped in Lower Lock {lock_bars} bar(s) at End of History)",
                "tp1_reached": active["tp1_hit"],
                "tp2_reached": active["tp2_hit"],
                "gross_pnl_pct": round(gross_return * 100, 2),
                "net_pnl_pct": round(net_return * 100, 2),
                "is_ambiguous": False,
                "lock_bars_trapped": lock_bars,
            })
            trades.append(active)
            in_trade = False
        else:
            unresolved_trade = active
            unresolved_trade["current_price"] = float(df_ind["Close"].iloc[-1])
            unresolved_trade["unrealized_pnl_pct"] = round(
                ((unresolved_trade["current_price"] - unresolved_trade["entry_price"]) / unresolved_trade["entry_price"]) * 100, 2
            )

    df_trades = pd.DataFrame(trades) if trades else pd.DataFrame()
    resolved_count = len(df_trades)
    unresolved_count = 1 if unresolved_trade else 0
    total_triggered = resolved_count + unresolved_count

    # Statistical sample adequacy
    sample_adequacy = evaluate_sample_adequacy(resolved_count)

    # 1,000-Iteration Bootstrap Simulation for Confidence Intervals
    bootstrap_res = run_bootstrap_simulation(df_trades, n_iterations=1000)

    if resolved_count > 0:
        tp1_hits = int(df_trades["tp1_reached"].sum())
        tp2_hits = int(df_trades["tp2_reached"].sum())
        sl_hits = len(df_trades[df_trades["outcome"].str.contains("STOP LOSS")])
        time_stop_hits = len(df_trades[df_trades["outcome"].str.contains("TIME-STOP")])
        ambiguous_hits = int(df_trades["is_ambiguous"].sum())

        win_rate_tp1 = round((tp1_hits / resolved_count) * 100, 1)
        win_rate_tp2 = round((tp2_hits / resolved_count) * 100, 1)
        sl_rate = round((sl_hits / resolved_count) * 100, 1)

        net_pnl_total = round(float(df_trades["net_pnl_pct"].sum()), 2)
        avg_trade_pnl = round(float(df_trades["net_pnl_pct"].mean()), 2)

        # Max drawdown calculation on equity curve
        df_trades["cum_pnl"] = df_trades["net_pnl_pct"].cumsum()
        peak = df_trades["cum_pnl"].cummax()
        drawdown = df_trades["cum_pnl"] - peak
        max_drawdown = round(float(drawdown.min()), 2)

        winning_trades = df_trades[df_trades["net_pnl_pct"] > 0]
        losing_trades = df_trades[df_trades["net_pnl_pct"] <= 0]
        gross_profit = float(winning_trades["gross_pnl_pct"].sum()) if not winning_trades.empty else 0.0
        gross_loss = abs(float(losing_trades["gross_pnl_pct"].sum())) if not losing_trades.empty else 1e-6
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0
    else:
        tp1_hits = tp2_hits = sl_hits = time_stop_hits = ambiguous_hits = 0
        win_rate_tp1 = win_rate_tp2 = sl_rate = 0.0
        net_pnl_total = avg_trade_pnl = max_drawdown = profit_factor = 0.0

    return {
        "symbol": symbol,
        "total_triggered": total_triggered,
        "resolved_trades": resolved_count,
        "unresolved_trades": unresolved_count,
        "unresolved_trade": unresolved_trade,
        "pending_setup": pending_entry,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "sl_hits": sl_hits,
        "time_stop_hits": time_stop_hits,
        "ambiguous_trades": ambiguous_hits,
        "circuit_lock_discards": circuit_lock_discards,
        "gap_discards": gap_discards,
        "trapped_lock_trades": int(df_trades["lock_bars_trapped"].fillna(0).gt(0).sum()) if ("lock_bars_trapped" in df_trades.columns and not df_trades.empty) else 0,
        "tp1_hit_rate_pct": win_rate_tp1,
        "tp2_hit_rate_pct": win_rate_tp2,
        "stop_loss_rate_pct": sl_rate,
        "net_pnl_pct": net_pnl_total,
        "avg_trade_pnl_pct": avg_trade_pnl,
        "max_drawdown_pct": max_drawdown,
        "profit_factor": profit_factor,
        "trades_df": df_trades,
        "sample_adequacy": sample_adequacy,
        "bootstrap": bootstrap_res,
        "microstructure": {
            "use_next_day_open": use_next_day_open,
            "enforce_circuit_limits": enforce_circuit_limits,
            "simulate_circuit_trapping": simulate_circuit_trapping,
            "broker_fee_pct": broker_fee_pct,
            "exit_mode": exit_mode,
        },
    }


def run_sensitivity_grid(
    df: pd.DataFrame,
    symbol: str,
    df_kse: pd.DataFrame = None,
    holding_max_bars: int = 20,
    time_stop_bars: int = 4,
    exit_mode: str = "trailing_ema",
) -> dict:
    """
    Evaluates strategy parameter sensitivity across a 9-point grid.
    Determines whether the trading edge sits on a stable parameter plateau
    or an overfitted needle peak.
    """
    variations = [
        {"name": "Baseline (Optimal)", "rsi_max": 68.0, "vol_surge_mult": 1.20, "rr_min": 1.20},
        {"name": "Conservative RSI (64)", "rsi_max": 64.0, "vol_surge_mult": 1.20, "rr_min": 1.20},
        {"name": "Permissive RSI (72)", "rsi_max": 72.0, "vol_surge_mult": 1.20, "rr_min": 1.20},
        {"name": "High Volume Surge (1.30x)", "rsi_max": 68.0, "vol_surge_mult": 1.30, "rr_min": 1.20},
        {"name": "Low Volume Surge (1.10x)", "rsi_max": 68.0, "vol_surge_mult": 1.10, "rr_min": 1.20},
        {"name": "Strict R:R (1.30:1)", "rsi_max": 68.0, "vol_surge_mult": 1.20, "rr_min": 1.30},
        {"name": "Relaxed R:R (1.15:1)", "rsi_max": 68.0, "vol_surge_mult": 1.20, "rr_min": 1.15},
        {"name": "Strict Combo (64 RSI, 1.30x Vol)", "rsi_max": 64.0, "vol_surge_mult": 1.30, "rr_min": 1.30},
        {"name": "Relaxed Combo (72 RSI, 1.10x Vol)", "rsi_max": 72.0, "vol_surge_mult": 1.10, "rr_min": 1.15},
    ]

    records = []
    for var in variations:
        p_dict = {k: v for k, v in var.items() if k != "name"}
        bt = run_signal_backtest(
            df,
            symbol=symbol,
            holding_max_bars=holding_max_bars,
            df_kse=df_kse,
            time_stop_bars=time_stop_bars,
            strategy_params=p_dict,
            exit_mode=exit_mode,
        )
        records.append({
            "Variation": var["name"],
            "RSI Max": var["rsi_max"],
            "Vol Mult": var["vol_surge_mult"],
            "Min R:R": var["rr_min"],
            "Trades": bt.get("resolved_trades", 0),
            "Win Rate (%)": bt.get("tp1_hit_rate_pct", 0.0),
            "Net P&L (%)": bt.get("net_pnl_pct", 0.0),
            "Profit Factor": bt.get("profit_factor", 0.0),
            "Max DD (%)": bt.get("max_drawdown_pct", 0.0),
        })

    grid_df = pd.DataFrame(records)
    pnls = grid_df["Net P&L (%)"].values
    profitable_count = int((pnls > 0).sum())
    profitable_pct = round((profitable_count / len(pnls)) * 100.0, 1)
    mean_pnl = round(float(np.mean(pnls)), 2)
    std_pnl = round(float(np.std(pnls)), 2)

    if profitable_pct >= 70.0 and mean_pnl > 0:
        stability_badge = "🟢 Stable Parameter Plateau"
        assessment = "Strategy displays strong robustness across adjacent parameter variations (low overfitting risk)."
    elif profitable_pct >= 40.0:
        stability_badge = "🟡 Moderate Sensitivity"
        assessment = "Strategy performance is sensitive to parameter choices. Exercise caution in live execution."
    else:
        stability_badge = "🔴 Fragile / Likely Overfit"
        assessment = "Strategy returns collapse when parameters are nudged. Indicates curve-fitting to historical noise."

    return {
        "grid_df": grid_df,
        "profitable_pct": profitable_pct,
        "mean_pnl": mean_pnl,
        "std_pnl": std_pnl,
        "stability_badge": stability_badge,
        "assessment": assessment,
    }


# run_signal_backtest's own hard floor (loop starts at bar index 25, plus a small
# margin of evaluable bars). Any OOS window shorter than this is silently discarded
# by run_signal_backtest as "Insufficient historical data" before a single bar is
# evaluated -- which is exactly what happened with the old test_bars=25 default.
MIN_OOS_WINDOW_BARS = 35


def run_walk_forward_analysis(
    df: pd.DataFrame,
    symbol: str,
    df_kse: pd.DataFrame = None,
    train_bars: int = 70,
    test_bars: int = 35,
    exit_mode: str = "trailing_ema",
) -> dict:
    """
    Executes a rolling walk-forward validation of the strategy's fixed rule set.

    NOTE ON NAMING: despite the "WFO" shorthand used elsewhere in this project,
    this function does NOT refit or optimize strategy parameters on each in-sample
    (train) window -- it applies the same fixed default rules to every out-of-sample
    (test) window and reports how they performed on data the rules were never tuned
    against. That is a genuine and useful out-of-sample robustness check, but it is
    not parameter optimization; treat "in-sample" window boundaries as a training
    corpus reserved for a future parameter-search extension, not as evidence that
    parameters were actually re-fit here.

    test_bars is clamped to MIN_OOS_WINDOW_BARS: run_signal_backtest refuses to
    evaluate any window shorter than 30 bars, so a test_bars value below that would
    silently produce zero out-of-sample trades in every window, which is easy to
    misread as "the strategy has no out-of-sample edge" rather than "the window was
    too short to run at all."
    """
    if test_bars < MIN_OOS_WINDOW_BARS:
        test_bars = MIN_OOS_WINDOW_BARS

    n = len(df)
    if n < (train_bars + test_bars):
        return {
            "status": "INSUFFICIENT_HISTORY",
            "message": f"Requires at least {train_bars + test_bars} bars for walk-forward testing (dataset has {n}). Consider switching lookback to 1 Year.",
            "windows_evaluated": 0,
            "total_oos_trades": 0,
            "total_oos_net_pnl": 0.0,
            "oos_win_rate_pct": 0.0,
            "oos_trades_df": pd.DataFrame(),
        }

    oos_trades = []
    step = test_bars
    window_count = 0

    for start in range(0, n - train_bars - test_bars + 1, step):
        train_end = start + train_bars
        test_end = min(train_end + test_bars, n)
        window_count += 1

        # df_train is the in-sample segment for this window; reserved for a future
        # per-window parameter-fit step (see docstring). Not currently used to
        # alter the rules applied to df_test.
        df_train = df.iloc[start:train_end]
        df_test = df.iloc[train_end:test_end]

        # Run out-of-sample forward test on unseen window
        bt_oos = run_signal_backtest(
            df_test,
            symbol=symbol,
            df_kse=df_kse,
            exit_mode=exit_mode,
        )
        if not bt_oos.get("trades_df", pd.DataFrame()).empty:
            t_df = bt_oos["trades_df"].copy()
            t_df["wfo_window"] = window_count
            oos_trades.append(t_df)

    if oos_trades:
        df_oos_all = pd.concat(oos_trades, ignore_index=True)
        total_oos_pnl = round(float(df_oos_all["net_pnl_pct"].sum()), 2)
        oos_wins = int(df_oos_all["tp1_reached"].sum())
        oos_win_rate = round((oos_wins / len(df_oos_all)) * 100.0, 1)
    else:
        df_oos_all = pd.DataFrame()
        total_oos_pnl = 0.0
        oos_win_rate = 0.0

    return {
        "status": "OK",
        "windows_evaluated": window_count,
        "total_oos_trades": len(df_oos_all),
        "total_oos_net_pnl": total_oos_pnl,
        "oos_win_rate_pct": oos_win_rate,
        "oos_trades_df": df_oos_all,
    }


def run_portfolio_backtest(
    data_dict: dict[str, pd.DataFrame],
    initial_capital: float = 1_000_000.0,
    max_positions: int = 5,
    max_sector_exposure: int = 2,
    broker_fee_pct: float = 0.35,
    df_kse: pd.DataFrame = None,
    use_next_day_open: bool = True,
    enforce_circuit_limits: bool = True,
    simulate_circuit_trapping: bool = True,
    exit_mode: str = "trailing_ema",
    time_stop_bars: int = 4,
    holding_max_bars: int = 30,
    strategy_params: dict = None,
) -> dict:
    """
    Multi-symbol portfolio simulation with realistic account cash management,
    sector diversification caps, dynamic position sizing, and PSX market microstructure.

    Key Features:
    - Account Cash Constraints: Fixed slot allocation (equity / max_positions).
      New setups are skipped or queued if all slots are filled.
    - Sector Exposure Limits: Restricts concurrent holdings in any single sector (e.g. max 2 banks).
    - True Cash Drag: Unallocated cash earns 0% while awaiting setups, producing realistic account equity.
    - Portfolio Metrics: Equity curve, CAGR, Sharpe ratio, Sortino ratio, Calmar ratio, and Max Drawdown.
    """
    if not data_dict:
        return {"error": "No market data provided for portfolio simulation."}

    # 1. Validate datasets and filter to true OHLC
    valid_data = {}
    for sym, df in data_dict.items():
        if df is not None and len(df) >= 30 and check_has_true_ohlc(df):
            valid_data[sym] = df

    if not valid_data:
        return {"error": "No symbols passed true OHLC data validation."}

    # 2. Precompute indicators and map sectors
    processed_data = {}
    symbol_sectors = {}
    for sym, df in valid_data.items():
        processed_data[sym] = compute_all_indicators(df, df_kse=df_kse)
        symbol_sectors[sym] = get_symbol_sector(sym)

    # 4. Synchronize unified chronological calendar
    all_dates = sorted(list(set.union(*[set(df.index) for df in processed_data.values()])))

    # 3. Pre-align KSE-100 series if provided
    kse_close_series = None
    kse_ema50_series = None
    if df_kse is not None and not df_kse.empty:
        df_kse_aligned = df_kse.copy()
        first_tz = all_dates[0].tz if len(all_dates) > 0 and hasattr(all_dates[0], "tz") else None
        if df_kse_aligned.index.tz is not None and first_tz is None:
            df_kse_aligned.index = df_kse_aligned.index.tz_localize(None)
        elif df_kse_aligned.index.tz is None and first_tz is not None:
            df_kse_aligned.index = df_kse_aligned.index.tz_localize(first_tz)
        elif df_kse_aligned.index.tz != first_tz:
            df_kse_aligned.index = df_kse_aligned.index.tz_convert(first_tz)

        if "EMA_50" not in df_kse_aligned.columns:
            df_kse_aligned["EMA_50"] = df_kse_aligned["Close"].ewm(span=50, adjust=False).mean()
        kse_close_series = df_kse_aligned["Close"]
        kse_ema50_series = df_kse_aligned["EMA_50"]

    cash = float(initial_capital)
    open_positions = {}
    pending_entries = []
    closed_trades = []
    skipped_signals = []
    equity_records = []
    circuit_lock_discards = 0
    gap_discards = 0

    for current_date in all_dates:
        # Determine KSE-100 Macro Regime for this session
        hist_regime = None
        if kse_close_series is not None and current_date in kse_close_series.index:
            kse_c = kse_close_series.loc[current_date]
            kse_e50 = kse_ema50_series.loc[current_date]
            if pd.notna(kse_c) and pd.notna(kse_e50):
                is_bull = bool(kse_c >= kse_e50)
                hist_regime = {
                    "is_bullish": is_bull,
                    "regime": "BULL_MARKET" if is_bull else "MARKET_CORRECTION",
                }

        # -----------------------------------------------------------------
        # A. PROCESS PENDING ENTRIES ON TODAY'S OPEN
        # -----------------------------------------------------------------
        for pending in pending_entries:
            sym = pending["symbol"]
            df_sym = processed_data[sym]
            if current_date not in df_sym.index:
                continue

            bar = df_sym.loc[current_date]
            open_p = float(bar["Open"])
            low_p = float(bar["Low"])
            prev_c = pending["prev_close"]
            adv_p = float(bar["Vol_MA20"]) if (not pd.isna(bar["Vol_MA20"]) and bar["Vol_MA20"] > 0) else 100_000.0

            limit_offset = max(prev_c * 0.075, 1.0)
            limit_up = prev_c + limit_offset
            limit_dn = max(prev_c - limit_offset, 0.01)

            # Check Upper Circuit Lock
            if enforce_circuit_limits and (open_p >= limit_up - 0.05 and low_p >= limit_up - 0.05):
                circuit_lock_discards += 1
                skipped_signals.append({
                    "date": current_date,
                    "symbol": sym,
                    "reason": "UPPER_CIRCUIT_LOCKED",
                    "sector": pending["sector"],
                })
                continue

            # Check Gap Discards
            if open_p >= pending["tp1"] or open_p <= pending["stop_loss"]:
                gap_discards += 1
                skipped_signals.append({
                    "date": current_date,
                    "symbol": sym,
                    "reason": "GAP_DISCARD",
                    "sector": pending["sector"],
                })
                continue

            # Check Portfolio Capacity
            if len(open_positions) >= max_positions:
                skipped_signals.append({
                    "date": current_date,
                    "symbol": sym,
                    "reason": "MAX_POSITIONS_REACHED",
                    "sector": pending["sector"],
                })
                continue

            # Check Sector Exposure Cap
            sec = pending["sector"]
            sec_count = sum(1 for p in open_positions.values() if p["sector"] == sec)
            if sec_count >= max_sector_exposure:
                skipped_signals.append({
                    "date": current_date,
                    "symbol": sym,
                    "reason": "SECTOR_LIMIT_EXCEEDED",
                    "sector": sec,
                })
                continue

            # Dynamic Position Sizing based on current total portfolio equity
            curr_invested = sum(
                p["shares"] * float(processed_data[s].loc[current_date]["Open"] if current_date in processed_data[s].index else p["entry_price"])
                for s, p in open_positions.items()
            )
            curr_equity = cash + curr_invested
            target_alloc = curr_equity / max_positions
            alloc_cash = min(target_alloc, cash)

            if alloc_cash < 5_000.0:
                skipped_signals.append({
                    "date": current_date,
                    "symbol": sym,
                    "reason": "INSUFFICIENT_CASH",
                    "sector": sec,
                })
                continue

            spread_pct, slip_pct, tier = calculate_execution_friction(adv_p)
            entry_friction_pct = (spread_pct / 2.0) + slip_pct
            fill_price = round(open_p * (1.0 + entry_friction_pct / 100.0), 2)
            sl = pending["stop_loss"]

            if fill_price <= sl:
                continue

            shares = int(alloc_cash / (fill_price * (1.0 + broker_fee_pct / 100.0)))
            if shares <= 0:
                continue

            trade_cost = shares * fill_price * (1.0 + broker_fee_pct / 100.0)
            cash -= trade_cost

            risk = fill_price - sl
            tp1 = round(fill_price + max(1.35 * risk, 1.4 * pending["atr"]), 2)
            tp2 = round(fill_price + max(2.5 * risk, 2.6 * pending["atr"]), 2)

            open_positions[sym] = {
                "symbol": sym,
                "strategy": pending["strategy"],
                "sector": sec,
                "signal_date": pending["signal_date"],
                "entry_date": current_date,
                "entry_price": fill_price,
                "raw_open": open_p,
                "shares": shares,
                "capital_allocated": round(trade_cost, 2),
                "spread_pct": spread_pct,
                "slippage_pct": slip_pct,
                "liquidity_tier": tier,
                "stop_loss": sl,
                "tp1": tp1,
                "tp2": tp2,
                "atr": pending["atr"],
                "bars_held": 0,
                "tp1_hit": False,
                "tp2_hit": False,
                "ambiguous_bars": 0,
            }

        pending_entries = []

        # -----------------------------------------------------------------
        # B. MANAGE ACTIVE POSITIONS ON TODAY'S BAR
        # -----------------------------------------------------------------
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df_sym = processed_data[sym]
            if current_date not in df_sym.index:
                continue

            bar = df_sym.loc[current_date]
            open_p = float(bar["Open"])
            high_p = float(bar["High"])
            low_p = float(bar["Low"])
            close_p = float(bar["Close"])
            adv_p = float(bar["Vol_MA20"]) if (not pd.isna(bar["Vol_MA20"]) and bar["Vol_MA20"] > 0) else 100_000.0

            idx_in_sym = df_sym.index.get_loc(current_date)
            prev_c = float(df_sym["Close"].iloc[idx_in_sym - 1]) if idx_in_sym > 0 else open_p

            limit_offset = max(prev_c * 0.075, 1.0)
            limit_up = prev_c + limit_offset
            limit_dn = max(prev_c - limit_offset, 0.01)
            is_lower_locked = enforce_circuit_limits and (close_p <= limit_dn + 0.05 and high_p <= limit_dn + 0.05)

            pos["bars_held"] += 1
            tp1 = pos["tp1"]
            tp2 = pos["tp2"]
            sl = pos["stop_loss"]
            entry = pos["entry_price"]
            shares = pos["shares"]

            exit_spread, exit_slip, _ = calculate_execution_friction(adv_p)
            total_exit_friction = broker_fee_pct + (exit_spread / 2.0) + exit_slip

            # 1. Check Trapped Circuit Exit from prior session
            if simulate_circuit_trapping and pos.get("trapped_exit"):
                if is_lower_locked:
                    pos["trapped_exit"]["lock_bars"] += 1
                    continue
                else:
                    lock_bars = pos["trapped_exit"]["lock_bars"]
                    orig_reason = pos["trapped_exit"]["reason"]
                    actual_exit = min(open_p, limit_up)
                    emergency_friction = total_exit_friction + 0.20
                    gross_return = (actual_exit - entry) / entry
                    net_return = gross_return - (emergency_friction / 100.0)
                    net_proceeds = shares * actual_exit * (1.0 - emergency_friction / 100.0)
                    cash += net_proceeds
                    pos.update({
                        "exit_date": current_date,
                        "exit_price": actual_exit,
                        "outcome": f"{orig_reason} (Trapped in Lower Lock {lock_bars} bar(s), Unlocked)",
                        "tp1_reached": pos["tp1_hit"],
                        "tp2_reached": pos["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                        "is_ambiguous": False,
                        "lock_bars_trapped": lock_bars,
                    })
                    closed_trades.append(pos)
                    del open_positions[sym]
                    continue

            # 2. Check Strategy Exits
            if exit_mode == "trailing_ema":
                tp1_hit_before = pos["tp1_hit"]
                tp2_hit_before = pos["tp2_hit"]
                hit_tp1_this_bar = high_p >= tp1
                hit_tp2_this_bar = high_p >= tp2
                hit_sl_this_bar = low_p <= sl

                newly_ambiguous = hit_sl_this_bar and (
                    (hit_tp1_this_bar and not tp1_hit_before) or (hit_tp2_this_bar and not tp2_hit_before)
                )

                if newly_ambiguous:
                    pos["ambiguous_bars"] += 1
                    if is_lower_locked and simulate_circuit_trapping:
                        pos["trapped_exit"] = {
                            "trigger_date": current_date,
                            "reason": "STOP LOSS HIT (Ambiguous Bar)",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                        outcome_msg = "STOP LOSS HIT (Ambiguous Bar, Lower Circuit)"
                    else:
                        outcome_msg = "STOP LOSS HIT (Ambiguous Bar)"
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    net_proceeds = shares * actual_sl * (1.0 - total_exit_friction / 100.0)
                    cash += net_proceeds
                    pos.update({
                        "exit_date": current_date,
                        "exit_price": actual_sl,
                        "outcome": outcome_msg,
                        "tp1_reached": tp1_hit_before,
                        "tp2_reached": tp2_hit_before,
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                        "is_ambiguous": True,
                    })
                    closed_trades.append(pos)
                    del open_positions[sym]
                    continue

                if hit_tp1_this_bar:
                    pos["tp1_hit"] = True
                if hit_tp2_this_bar:
                    pos["tp2_hit"] = True

                if hit_sl_this_bar:
                    if is_lower_locked and simulate_circuit_trapping:
                        pos["trapped_exit"] = {
                            "trigger_date": current_date,
                            "reason": "STOP LOSS HIT",
                            "lock_bars": 1,
                        }
                        continue
                    actual_sl = min(sl, open_p)
                    if is_lower_locked:
                        actual_sl = min(actual_sl, limit_dn)
                        outcome_msg = "STOP LOSS HIT (Lower Circuit Limit)"
                    else:
                        outcome_msg = "STOP LOSS HIT"
                    gross_return = (actual_sl - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    net_proceeds = shares * actual_sl * (1.0 - total_exit_friction / 100.0)
                    cash += net_proceeds
                    pos.update({
                        "exit_date": current_date,
                        "exit_price": actual_sl,
                        "outcome": outcome_msg,
                        "tp1_reached": pos["tp1_hit"],
                        "tp2_reached": pos["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                        "is_ambiguous": False,
                    })
                    closed_trades.append(pos)
                    del open_positions[sym]
                    continue

                ema20_val = float(bar["EMA_20"]) if "EMA_20" in bar and pd.notna(bar["EMA_20"]) else None
                if pos["bars_held"] >= 3 and ema20_val is not None and close_p < ema20_val:
                    if is_lower_locked and simulate_circuit_trapping:
                        pos["trapped_exit"] = {
                            "trigger_date": current_date,
                            "reason": "TRAIL_20EMA EXIT",
                            "lock_bars": 1,
                        }
                        continue
                    actual_exit = close_p
                    if is_lower_locked:
                        actual_exit = min(actual_exit, limit_dn)
                    gross_return = (actual_exit - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    net_proceeds = shares * actual_exit * (1.0 - total_exit_friction / 100.0)
                    cash += net_proceeds
                    pos.update({
                        "exit_date": current_date,
                        "exit_price": actual_exit,
                        "outcome": "TRAIL_20EMA EXIT",
                        "tp1_reached": pos["tp1_hit"],
                        "tp2_reached": pos["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                        "is_ambiguous": False,
                    })
                    closed_trades.append(pos)
                    del open_positions[sym]
                    continue

                if time_stop_bars > 0 and not pos["tp1_hit"] and pos["bars_held"] >= time_stop_bars:
                    stalled_threshold = entry + (0.15 * pos.get("atr", 0.0))
                    if close_p <= stalled_threshold:
                        if is_lower_locked and simulate_circuit_trapping:
                            pos["trapped_exit"] = {
                                "trigger_date": current_date,
                                "reason": "TIME-STOP EXIT (Stalled Momentum)",
                                "lock_bars": 1,
                            }
                            continue
                        exit_price = close_p
                        if is_lower_locked:
                            exit_price = min(exit_price, limit_dn)
                        gross_return = (exit_price - entry) / entry
                        net_return = gross_return - (total_exit_friction / 100.0)
                        net_proceeds = shares * exit_price * (1.0 - total_exit_friction / 100.0)
                        cash += net_proceeds
                        pos.update({
                            "exit_date": current_date,
                            "exit_price": exit_price,
                            "outcome": "TIME-STOP EXIT (Stalled Momentum)",
                            "tp1_reached": False,
                            "tp2_reached": False,
                            "gross_pnl_pct": round(gross_return * 100, 2),
                            "net_pnl_pct": round(net_return * 100, 2),
                            "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                            "is_ambiguous": False,
                        })
                        closed_trades.append(pos)
                        del open_positions[sym]
                        continue

                if pos["bars_held"] >= holding_max_bars:
                    if is_lower_locked and simulate_circuit_trapping:
                        pos["trapped_exit"] = {
                            "trigger_date": current_date,
                            "reason": "EXPIRED (Holding Limit)",
                            "lock_bars": 1,
                        }
                        continue
                    exit_price = close_p
                    if is_lower_locked:
                        exit_price = min(exit_price, limit_dn)
                    gross_return = (exit_price - entry) / entry
                    net_return = gross_return - (total_exit_friction / 100.0)
                    net_proceeds = shares * exit_price * (1.0 - total_exit_friction / 100.0)
                    cash += net_proceeds
                    pos.update({
                        "exit_date": current_date,
                        "exit_price": exit_price,
                        "outcome": "EXPIRED (Holding Limit)",
                        "tp1_reached": pos["tp1_hit"],
                        "tp2_reached": pos["tp2_hit"],
                        "gross_pnl_pct": round(gross_return * 100, 2),
                        "net_pnl_pct": round(net_return * 100, 2),
                        "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
                        "is_ambiguous": False,
                    })
                    closed_trades.append(pos)
                    del open_positions[sym]
                    continue

        # -----------------------------------------------------------------
        # C. SCAN FOR NEW SIGNALS AT TODAY'S CLOSE
        # -----------------------------------------------------------------
        for sym, df_sym in processed_data.items():
            if sym in open_positions or any(p["symbol"] == sym for p in pending_entries):
                continue
            if current_date not in df_sym.index:
                continue
            bar_idx = df_sym.index.get_loc(current_date)
            if bar_idx < 25:
                continue

            setup = evaluate_bar_strategy(
                df_sym,
                bar_idx=bar_idx,
                market_regime=hist_regime,
                params=strategy_params,
            )
            if setup.get("status") == "TRIGGERED":
                pending_entries.append({
                    "symbol": sym,
                    "strategy": setup["strategy"],
                    "sector": symbol_sectors[sym],
                    "signal_date": current_date,
                    "stop_loss": setup["stop_loss"],
                    "tp1": setup["tp1"],
                    "tp2": setup["tp2"],
                    "atr": setup.get("atr", 0.0),
                    "prev_close": float(df_sym["Close"].iloc[bar_idx]),
                })

        # -----------------------------------------------------------------
        # D. RECORD DAILY PORTFOLIO VALUATION (MARK TO MARKET)
        # -----------------------------------------------------------------
        daily_invested = sum(
            pos["shares"] * float(processed_data[s].loc[current_date]["Close"] if current_date in processed_data[s].index else pos["entry_price"])
            for s, pos in open_positions.items()
        )
        total_equity = cash + daily_invested
        equity_records.append({
            "Date": current_date,
            "Cash": round(cash, 2),
            "Invested": round(daily_invested, 2),
            "Total_Equity": round(total_equity, 2),
            "Open_Positions": len(open_positions),
        })

    # Close any positions remaining open at end of data
    had_unresolved_positions = bool(open_positions)
    for sym, pos in list(open_positions.items()):
        df_sym = processed_data[sym]
        last_c = float(df_sym["Close"].iloc[-1])
        adv_val = float(df_sym["Vol_MA20"].iloc[-1]) if ("Vol_MA20" in df_sym and pd.notna(df_sym["Vol_MA20"].iloc[-1])) else 100_000.0
        exit_spread, exit_slip, _ = calculate_execution_friction(adv_val)
        total_exit_friction = broker_fee_pct + (exit_spread / 2.0) + exit_slip
        gross_return = (last_c - pos["entry_price"]) / pos["entry_price"]
        net_return = gross_return - (total_exit_friction / 100.0)
        net_proceeds = pos["shares"] * last_c * (1.0 - total_exit_friction / 100.0)
        cash += net_proceeds
        pos.update({
            "exit_date": df_sym.index[-1],
            "exit_price": last_c,
            "outcome": "UNRESOLVED_AT_END (Mark-to-Market)",
            "tp1_reached": pos["tp1_hit"],
            "tp2_reached": pos["tp2_hit"],
            "gross_pnl_pct": round(gross_return * 100, 2),
            "net_pnl_pct": round(net_return * 100, 2),
            "net_pnl_pkr": round(net_proceeds - pos["capital_allocated"], 2),
            "is_ambiguous": False,
        })
        closed_trades.append(pos)

    # The equity curve built during the day-by-day loop marks any still-open
    # positions at their raw close price with NO exit friction applied (that's
    # correct for a running mark-to-market view). But the forced closure above
    # DOES deduct real exit friction from `cash` for those same positions -- so
    # if we left the equity curve's last row untouched, `final_equity` (and
    # everything derived from it: net_pnl_pkr, CAGR, Sharpe/Sortino/Calmar,
    # max drawdown) would be systematically optimistic by exactly the unwind
    # cost of whatever was still open when the simulation ended, while the
    # trade log itself would correctly show that cost. Overwrite the last
    # equity row with the now fully-liquidated cash position so the two agree.
    if had_unresolved_positions and equity_records:
        equity_records[-1] = {
            "Date": equity_records[-1]["Date"],
            "Cash": round(cash, 2),
            "Invested": 0.0,
            "Total_Equity": round(cash, 2),
            "Open_Positions": 0,
        }

    open_positions = {}
    df_trades = pd.DataFrame(closed_trades) if closed_trades else pd.DataFrame()
    df_skipped = pd.DataFrame(skipped_signals) if skipped_signals else pd.DataFrame()
    df_equity = pd.DataFrame(equity_records).set_index("Date") if equity_records else pd.DataFrame()

    final_equity = round(float(df_equity["Total_Equity"].iloc[-1]) if not df_equity.empty else initial_capital, 2)
    net_pnl_pkr = round(final_equity - initial_capital, 2)
    net_return_pct = round((net_pnl_pkr / initial_capital) * 100.0, 2)

    # Calculate Drawdown
    if not df_equity.empty:
        peak = df_equity["Total_Equity"].cummax()
        dd_pkr = df_equity["Total_Equity"] - peak
        dd_pct = (dd_pkr / peak) * 100.0
        df_equity["Drawdown_Pct"] = round(dd_pct, 2)
        max_dd_pct = round(float(dd_pct.min()), 2)
        max_dd_pkr = round(float(dd_pkr.min()), 2)

        # Sharpe, Sortino, CAGR
        days_span = max((df_equity.index[-1] - df_equity.index[0]).days, 1)
        years = days_span / 365.25
        cagr_pct = round(((final_equity / initial_capital) ** (1.0 / max(years, 0.1)) - 1.0) * 100.0, 2) if final_equity > 0 else -100.0

        daily_returns = df_equity["Total_Equity"].pct_change().dropna()
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe_ratio = round(float(daily_returns.mean() / daily_returns.std() * np.sqrt(252)), 2)
            downside_ret = daily_returns[daily_returns < 0]
            sortino_ratio = round(float(daily_returns.mean() / downside_ret.std() * np.sqrt(252)), 2) if (len(downside_ret) > 0 and downside_ret.std() > 0) else sharpe_ratio
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0
        calmar_ratio = round(abs(cagr_pct / max_dd_pct), 2) if max_dd_pct < 0 else 0.0
    else:
        max_dd_pct = max_dd_pkr = cagr_pct = sharpe_ratio = sortino_ratio = calmar_ratio = 0.0

    # Trade stats
    if not df_trades.empty:
        winning = df_trades[df_trades["net_pnl_pct"] > 0]
        losing = df_trades[df_trades["net_pnl_pct"] <= 0]
        win_count = len(winning)
        loss_count = len(losing)
        win_rate_pct = round((win_count / len(df_trades)) * 100.0, 1)
        gross_profit_pkr = float(winning["net_pnl_pkr"].sum()) if not winning.empty else 0.0
        gross_loss_pkr = abs(float(losing["net_pnl_pkr"].sum())) if not losing.empty else 1e-6
        profit_factor = round(gross_profit_pkr / gross_loss_pkr, 2) if gross_loss_pkr > 0 else 99.0
        avg_trade_pnl = round(float(df_trades["net_pnl_pct"].mean()), 2)
    else:
        win_count = loss_count = 0
        win_rate_pct = profit_factor = avg_trade_pnl = 0.0

    skipped_counts = df_skipped["reason"].value_counts().to_dict() if not df_skipped.empty else {}

    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "net_pnl_pkr": net_pnl_pkr,
        "net_return_pct": net_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_pkr": max_dd_pkr,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "total_trades_closed": len(df_trades),
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "avg_trade_pnl_pct": avg_trade_pnl,
        "total_signals_triggered": len(df_trades) + len(df_skipped),
        "executed_trades": len(df_trades),
        "skipped_summary": skipped_counts,
        "equity_df": df_equity,
        "trades_df": df_trades,
        "skipped_df": df_skipped,
        "microstructure": {
            "max_positions": max_positions,
            "max_sector_exposure": max_sector_exposure,
            "use_next_day_open": use_next_day_open,
            "enforce_circuit_limits": enforce_circuit_limits,
            "simulate_circuit_trapping": simulate_circuit_trapping,
            "broker_fee_pct": broker_fee_pct,
            "exit_mode": exit_mode,
        },
    }
