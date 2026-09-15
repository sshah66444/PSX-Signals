"""
backtester.py
Rigorous Historical Simulation Engine using the exact same strategy function
as the live signal engine, with multi-target tracking and ambiguous candle detection.
"""

import pandas as pd
import numpy as np
from signal_engine import compute_all_indicators, evaluate_bar_strategy
from data_engine import check_has_true_ohlc


def run_signal_backtest(
    df: pd.DataFrame,
    symbol: str,
    holding_max_bars: int = 20,
    broker_fee_pct: float = 0.35,  # Round-trip commission + CDC/SECP charges
) -> dict:
    """
    Backtests strategy performance bar-by-bar across historical data.
    Uses evaluate_bar_strategy directly to guarantee identical rules.
    Strictly requires verified intraday High/Low data.
    """
    if df is None or len(df) < 30:
        return {"error": "Insufficient historical data (minimum 30 bars required)."}

    if not check_has_true_ohlc(df):
        return {
            "error": "Backtesting requires verified intraday High/Low prices. "
                     "The provided dataset contains approximated OHLC (Close/Open only), "
                     "which invalidates ATR, stop-loss triggers, and target simulations."
        }

    df_ind = compute_all_indicators(df)
    n_bars = len(df_ind)

    trades = []
    in_trade = False
    active = {}
    unresolved_trade = None

    for i in range(25, n_bars):
        bar = df_ind.iloc[i]
        date = df_ind.index[i]
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])

        if in_trade:
            active["bars_held"] += 1
            tp1 = active["tp1"]
            tp2 = active["tp2"]
            sl = active["stop_loss"]
            entry = active["entry_price"]

            # Ambiguous candle check: touched both target and stop on the same bar
            hit_tp1_this_bar = high >= tp1
            hit_sl_this_bar = low <= sl

            if hit_tp1_this_bar and hit_sl_this_bar and not active["tp1_hit"]:
                # Ambiguous sequence
                active["ambiguous_bars"] += 1
                # Default conservative assumption: Stop Loss triggered first
                gross_return = (sl - entry) / entry
                net_return = gross_return - (broker_fee_pct / 100.0)
                active.update({
                    "exit_date": date,
                    "exit_price": sl,
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
            if low <= sl and not active["tp1_hit"]:
                gross_return = (sl - entry) / entry
                net_return = gross_return - (broker_fee_pct / 100.0)
                active.update({
                    "exit_date": date,
                    "exit_price": sl,
                    "outcome": "STOP LOSS HIT",
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
            if high >= tp1 and not active["tp1_hit"]:
                active["tp1_hit"] = True
                active["stop_loss"] = entry  # Move stop loss to breakeven
                # If high also reached TP2 on same or subsequent bar
                if high >= tp2:
                    active["tp2_hit"] = True
                    # 50% exit at TP1, 50% exit at TP2
                    p1_ret = (tp1 - entry) / entry
                    p2_ret = (tp2 - entry) / entry
                    gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                    net_return = gross_return - (broker_fee_pct / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": tp2,
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

            # Case 3: Already hit TP1, now checking TP2 or Breakeven
            elif active["tp1_hit"]:
                if high >= tp2:
                    active["tp2_hit"] = True
                    p1_ret = (tp1 - entry) / entry
                    p2_ret = (tp2 - entry) / entry
                    gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                    net_return = gross_return - (broker_fee_pct / 100.0)
                    active.update({
                        "exit_date": date,
                        "exit_price": tp2,
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
                elif low <= active["stop_loss"]:
                    # Exits remaining 50% at breakeven
                    p1_ret = (tp1 - entry) / entry
                    p2_ret = 0.0  # Breakeven
                    gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                    net_return = gross_return - (broker_fee_pct / 100.0)
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

            # Case 4: Expiration (Holding limit reached)
            if active["bars_held"] >= holding_max_bars:
                exit_price = close
                if active["tp1_hit"]:
                    p1_ret = (tp1 - entry) / entry
                    p2_ret = (exit_price - entry) / entry
                    gross_return = 0.5 * p1_ret + 0.5 * p2_ret
                else:
                    gross_return = (exit_price - entry) / entry
                net_return = gross_return - (broker_fee_pct / 100.0)
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

        # Look for new entries when not in trade
        if not in_trade:
            setup = evaluate_bar_strategy(df_ind, bar_idx=i)
            if setup.get("status") == "TRIGGERED":
                in_trade = True
                active = {
                    "symbol": symbol,
                    "strategy": setup["strategy"],
                    "entry_date": date,
                    "entry_price": setup["entry_max"],
                    "stop_loss": setup["stop_loss"],
                    "tp1": setup["tp1"],
                    "tp2": setup["tp2"],
                    "bars_held": 0,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "ambiguous_bars": 0,
                }

    # If trade remained active up to the final historical bar
    if in_trade:
        unresolved_trade = active
        unresolved_trade["current_price"] = float(df_ind["Close"].iloc[-1])
        unresolved_trade["unrealized_pnl_pct"] = round(
            ((unresolved_trade["current_price"] - unresolved_trade["entry_price"]) / unresolved_trade["entry_price"]) * 100, 2
        )

    if not trades and not unresolved_trade:
        return {
            "symbol": symbol,
            "total_triggered": 0,
            "resolved_trades": 0,
            "unresolved_trades": 0,
            "unresolved_trade": None,
            "tp1_hits": 0,
            "tp2_hits": 0,
            "sl_hits": 0,
            "ambiguous_trades": 0,
            "tp1_hit_rate_pct": 0.0,
            "tp2_hit_rate_pct": 0.0,
            "stop_loss_rate_pct": 0.0,
            "net_pnl_pct": 0.0,
            "avg_trade_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "trades_df": pd.DataFrame(),
            "message": "No confirmed trade setups triggered in this historical window.",
        }

    df_trades = pd.DataFrame(trades) if trades else pd.DataFrame()
    resolved_count = len(df_trades)
    unresolved_count = 1 if unresolved_trade else 0
    total_triggered = resolved_count + unresolved_count

    if resolved_count > 0:
        tp1_hits = int(df_trades["tp1_reached"].sum())
        tp2_hits = int(df_trades["tp2_reached"].sum())
        sl_hits = len(df_trades[df_trades["outcome"].str.contains("STOP LOSS")])
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
        tp1_hits = tp2_hits = sl_hits = ambiguous_hits = 0
        win_rate_tp1 = win_rate_tp2 = sl_rate = 0.0
        net_pnl_total = avg_trade_pnl = max_drawdown = profit_factor = 0.0

    return {
        "symbol": symbol,
        "total_triggered": total_triggered,
        "resolved_trades": resolved_count,
        "unresolved_trades": unresolved_count,
        "unresolved_trade": unresolved_trade,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "sl_hits": sl_hits,
        "ambiguous_trades": ambiguous_hits,
        "tp1_hit_rate_pct": win_rate_tp1,
        "tp2_hit_rate_pct": win_rate_tp2,
        "stop_loss_rate_pct": sl_rate,
        "net_pnl_pct": net_pnl_total,
        "avg_trade_pnl_pct": avg_trade_pnl,
        "max_drawdown_pct": max_drawdown,
        "profit_factor": profit_factor,
        "trades_df": df_trades,
    }
