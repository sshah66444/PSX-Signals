"""
test_engine.py
Automated test suite verifying data engine, indicator math,
signal generation, charting, and backtesting.
"""

import sys
import os

# Ensure local directory is on path
sys.path.insert(0, os.path.dirname(__file__))

from data_engine import fetch_psx_stock, get_watchlist
from signal_engine import (
    compute_all_indicators,
    generate_signal,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_atr,
)
from chart_engine import create_signal_chart
from backtester import run_signal_backtest


def test_data_engine():
    print("Testing data engine...")
    watchlist = get_watchlist()
    assert "IPAK" in watchlist, "IPAK should be in watchlist"
    assert "OGDC" in watchlist, "OGDC should be in watchlist"

    # Test synthetic fallback / offline data fetching
    df, source = fetch_psx_stock("IPAK", period="6mo", use_live=False)
    assert not df.empty, "DataFrame should not be empty"
    assert len(df) >= 50, f"Expected at least 50 bars, got {len(df)}"
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        assert col in df.columns, f"Missing column {col}"
    print(f"  ✓ Data engine passed ({len(df)} bars fetched, source: {source})")
    return df


def test_signal_engine(df):
    print("Testing signal engine...")
    # Indicator calculations
    ema20 = calculate_ema(df["Close"], 20)
    assert len(ema20) == len(df), "EMA length mismatch"

    rsi = calculate_rsi(df["Close"], 14)
    assert 0 <= rsi.dropna().iloc[-1] <= 100, "RSI out of bounds"

    macd, sig, hist = calculate_macd(df["Close"])
    assert len(macd) == len(df), "MACD length mismatch"

    atr = calculate_atr(df, 14)
    assert (atr > 0).all(), "ATR should be positive"

    # Full indicator enrichment
    df_ind = compute_all_indicators(df)
    assert "EMA_20" in df_ind.columns
    assert "MACD_Hist" in df_ind.columns
    assert "Support_20" in df_ind.columns

    # Signal generation
    signal = generate_signal("IPAK", df)
    assert signal, "Signal dict should not be empty"
    assert "price" in signal
    assert "stop_loss" in signal
    assert "tp1" in signal
    assert "tp2" in signal
    assert signal["stop_loss"] < signal["price"], "Stop loss must be below current price"
    assert signal["tp1"] > signal["price"], "TP1 must be above current price"
    assert signal["tp2"] > signal["tp1"], "TP2 must be above TP1"
    assert "card_roman_urdu" in signal, "Roman Urdu card must be generated"
    assert "card_english" in signal, "English card must be generated"
    assert "IPAK" in signal["card_roman_urdu"]

    print(f"  ✓ Signal engine passed: Price={signal['price']}, SL={signal['stop_loss']}, TP1={signal['tp1']}, Signal={signal['signal_type']}")
    return signal


def test_chart_engine(df, signal):
    print("Testing chart engine...")
    fig = create_signal_chart(signal["df_indicators"], signal, "IPAK")
    assert fig is not None, "Figure must be created"
    assert len(fig.data) >= 4, f"Expected at least 4 chart traces, got {len(fig.data)}"
    print(f"  ✓ Chart engine passed ({len(fig.data)} plot traces configured)")


def test_backtester(df):
    print("Testing backtester...")
    bt = run_signal_backtest(df, symbol="IPAK", holding_max_bars=15, broker_fee_pct=0.35)
    assert "error" not in bt, f"Backtest failed with error: {bt.get('error')}"
    assert bt["total_trades"] >= 1, "Expected at least 1 backtest trade"
    assert 0 <= bt["win_rate_pct"] <= 100, "Win rate out of bounds"
    assert 0 <= bt["sl_rate_pct"] <= 100, "SL rate out of bounds"
    print(f"  ✓ Backtester passed: {bt['total_trades']} trades, Win Rate={bt['win_rate_pct']}%, SL Rate={bt['sl_rate_pct']}%, Net PnL={bt['total_net_pnl_pct']}%")


if __name__ == "__main__":
    print("=== Running PSX AlphaSignals Test Suite ===")
    df = test_data_engine()
    signal = test_signal_engine(df)
    test_chart_engine(df, signal)
    test_backtester(df)
    print("=== All Tests Passed Successfully! ===")
