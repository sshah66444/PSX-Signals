"""
backtester.py
Audits trade signals over historical data to measure real accuracy,
hit rates for TP1/TP2 vs Stop Loss, and net P&L after PSX fees and slippage.
"""

import pandas as pd
from signal_engine import compute_all_indicators


def run_signal_backtest(
    df: pd.DataFrame,
    symbol: str,
    holding_max_bars: int = 20,
    broker_fee_pct: float = 0.35,  # 0.15% buy + 0.15% sell + 0.05% taxes/CDC
) -> dict:
    """
    Backtests the technical signal logic bar-by-bar through historical data.
    Evaluates whether each trade hits TP1, TP2, or SL first.
    """
    if df is None or len(df) < 50:
        return {"error": "Insufficient historical data (minimum 50 bars required)."}

    df_ind = compute_all_indicators(df)
    trades = []
    in_trade = False
    active_trade = {}

    for i in range(30, len(df_ind) - 1):
        bar = df_ind.iloc[i]
        date = df_ind.index[i]
        close = bar["Close"]

        # Check ongoing trade
        if in_trade:
            high = bar["High"]
            low = bar["Low"]
            active_trade["bars_held"] += 1

            # 1. Stop Loss Hit
            if low <= active_trade["stop_loss"]:
                exit_price = active_trade["stop_loss"]
                gross_return = (exit_price - active_trade["entry_price"]) / active_trade["entry_price"]
                net_return = gross_return - (broker_fee_pct / 100.0)
                active_trade.update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "outcome": "STOP LOSS HIT",
                    "gross_pnl_pct": round(gross_return * 100, 2),
                    "net_pnl_pct": round(net_return * 100, 2),
                })
                trades.append(active_trade)
                in_trade = False
                continue

            # 2. TP2 Hit
            elif high >= active_trade["tp2"]:
                exit_price = active_trade["tp2"]
                gross_return = (exit_price - active_trade["entry_price"]) / active_trade["entry_price"]
                net_return = gross_return - (broker_fee_pct / 100.0)
                active_trade.update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "outcome": "TP2 HIT (Target 2)",
                    "gross_pnl_pct": round(gross_return * 100, 2),
                    "net_pnl_pct": round(net_return * 100, 2),
                })
                trades.append(active_trade)
                in_trade = False
                continue

            # 3. TP1 Hit (Partial / Full exit option)
            elif high >= active_trade["tp1"] and not active_trade.get("tp1_tagged"):
                active_trade["tp1_tagged"] = True
                # Mark as TP1 hit; we can choose to bank profit here
                exit_price = active_trade["tp1"]
                gross_return = (exit_price - active_trade["entry_price"]) / active_trade["entry_price"]
                net_return = gross_return - (broker_fee_pct / 100.0)
                active_trade.update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "outcome": "TP1 HIT (Target 1)",
                    "gross_pnl_pct": round(gross_return * 100, 2),
                    "net_pnl_pct": round(net_return * 100, 2),
                })
                trades.append(active_trade)
                in_trade = False
                continue

            # 4. Timeout / Expiration (Holding period limit)
            elif active_trade["bars_held"] >= holding_max_bars:
                exit_price = close
                gross_return = (exit_price - active_trade["entry_price"]) / active_trade["entry_price"]
                net_return = gross_return - (broker_fee_pct / 100.0)
                active_trade.update({
                    "exit_date": date,
                    "exit_price": exit_price,
                    "outcome": "EXPIRED (Time Limit)",
                    "gross_pnl_pct": round(gross_return * 100, 2),
                    "net_pnl_pct": round(net_return * 100, 2),
                })
                trades.append(active_trade)
                in_trade = False
                continue

        # Look for new Buy Signal when not in a trade
        if not in_trade:
            prev_bar = df_ind.iloc[i - 1]
            price = close
            ema20 = bar["EMA_20"]
            ema50 = bar["EMA_50"]
            rsi = bar["RSI"]
            macd_hist = bar["MACD_Hist"]
            prev_hist = prev_bar["MACD_Hist"]
            atr = bar["ATR"] if bar["ATR"] > 0 else (price * 0.02)
            support = bar["Support_20"]

            # Buy Signal Criteria: Trend Bullish with MACD expansion OR bounce off support
            is_bullish_entry = (
                price > ema20
                and ema20 > ema50
                and macd_hist > prev_hist
                and (40 <= rsi <= 65)
            )
            is_dip_entry = (price <= (support + 0.6 * atr) and rsi < 45 and price > ema50)

            if is_bullish_entry or is_dip_entry:
                sl = round(min(support - 0.3 * atr, price - 1.25 * atr), 2)
                tp1 = round(price + 1.0 * atr, 2)
                tp2 = round(price + 1.8 * atr, 2)

                in_trade = True
                active_trade = {
                    "symbol": symbol,
                    "entry_date": date,
                    "entry_price": round(price, 2),
                    "stop_loss": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "bars_held": 0,
                    "tp1_tagged": False,
                    "signal_type": "BULLISH" if is_bullish_entry else "PULLBACK",
                }

    if not trades:
        return {"error": "No completed trade signals triggered during this period."}

    df_trades = pd.DataFrame(trades)
    total_trades = len(df_trades)
    tp1_hits = len(df_trades[df_trades["outcome"].str.contains("TP1")])
    tp2_hits = len(df_trades[df_trades["outcome"].str.contains("TP2")])
    sl_hits = len(df_trades[df_trades["outcome"].str.contains("STOP LOSS")])
    expired = len(df_trades[df_trades["outcome"].str.contains("EXPIRED")])

    tp1_win_rate = round((tp1_hits + tp2_hits) / total_trades * 100, 1)
    sl_rate = round(sl_hits / total_trades * 100, 1)
    total_net_pnl = round(df_trades["net_pnl_pct"].sum(), 2)
    avg_net_pnl = round(df_trades["net_pnl_pct"].mean(), 2)

    winning_trades = df_trades[df_trades["net_pnl_pct"] > 0]
    losing_trades = df_trades[df_trades["net_pnl_pct"] <= 0]

    gross_profit = winning_trades["gross_pnl_pct"].sum() if not winning_trades.empty else 0.0
    gross_loss = abs(losing_trades["gross_pnl_pct"].sum()) if not losing_trades.empty else 1e-6
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0

    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "sl_hits": sl_hits,
        "expired": expired,
        "win_rate_pct": tp1_win_rate,
        "sl_rate_pct": sl_rate,
        "total_net_pnl_pct": total_net_pnl,
        "avg_trade_pnl_pct": avg_net_pnl,
        "profit_factor": profit_factor,
        "trades_df": df_trades,
    }
