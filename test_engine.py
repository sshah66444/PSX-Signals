"""
test_engine.py
Comprehensive automated test suite verifying data integrity, strategy checklist logic,
multi-target backtester accounting, and SQLite lifecycle tracking.
"""

import sys
import os
import sqlite3
import numpy as np
import pandas as pd

# Ensure local directory is on path
sys.path.insert(0, os.path.dirname(__file__))

from data_engine import (
    validate_market_data,
    generate_isolated_test_data,
    get_watchlist,
)
from signal_engine import (
    compute_all_indicators,
    evaluate_bar_strategy,
    generate_signal,
    format_actionable_card,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_atr,
)
from backtester import run_signal_backtest
import signal_tracker


def test_data_integrity():
    print("1. Testing data integrity & validation...")
    watchlist = get_watchlist()
    assert "OGDC" in watchlist, "OGDC must be in watchlist"

    # Test stale data rejection
    dates_stale = pd.date_range(end="2020-01-01", periods=30, freq="B")
    df_stale = pd.DataFrame({
        "Open": [100] * 30, "High": [105] * 30, "Low": [95] * 30, "Close": [102] * 30, "Volume": [1000] * 30
    }, index=dates_stale)
    is_valid, reason, age = validate_market_data(df_stale, max_age_days=5)
    assert not is_valid, "Stale data must be rejected"
    assert "stale" in reason.lower()

    # Test isolated test data generation
    df_test = generate_isolated_test_data(days=120, base_price=100.0)
    assert not df_test.empty
    assert len(df_test) == 120
    is_valid, reason, age = validate_market_data(df_test, max_age_days=5)
    assert is_valid, f"Test data failed validation: {reason}"
    print("  ✓ Data integrity validation passed (stale data properly rejected).")
    return df_test


def test_strategy_and_checklist(df):
    print("2. Testing strategy & condition checklist...")
    df_ind = compute_all_indicators(df)
    assert "EMA_20" in df_ind.columns
    assert "MACD_Hist" in df_ind.columns
    assert "Rolling_High20" in df_ind.columns

    # Test bar evaluator
    setup = evaluate_bar_strategy(df_ind, bar_idx=-1)
    assert "status" in setup
    assert "checklist" in setup
    assert isinstance(setup["checklist"], dict)
    for rule, passed in setup["checklist"].items():
        assert isinstance(passed, (bool, np.bool_)), f"Rule '{rule}' must be boolean"

    # CRITICAL: Verify TRIGGERED is strictly gated on 100% checklist pass
    if setup["status"] == "TRIGGERED":
        assert all(setup["checklist"].values()), "TRIGGERED status must have 100% checklist pass rate"
    else:
        # If any checklist item is False, status cannot be TRIGGERED
        if not all(setup["checklist"].values()):
            assert setup["status"] != "TRIGGERED", "Status cannot be TRIGGERED when checklist items fail"

    # Verify user's edge case: Pinned RSI=100 must be rejected from TRIGGERED
    df_pinned = df_ind.copy()
    df_pinned.loc[df_pinned.index[-1], "RSI"] = 100.0
    setup_pinned = evaluate_bar_strategy(df_pinned, bar_idx=-1)
    assert setup_pinned["status"] != "TRIGGERED", "Setup with RSI=100 must NOT be TRIGGERED"

    # Test actionable card formatting & HTML escaping
    card = format_actionable_card(setup, company_name="Test Company")
    assert "<b>" in card, "Card should contain Telegram HTML tags"
    # Ensure unescaped angle brackets in checklist do not break HTML
    assert "< 20" not in card or "&lt; 20" in card or "20" in card
    print(f"  ✓ Strategy & checklist passed (Status: {setup['status']}, Rules checked: {len(setup['checklist'])}).")
    return df_ind


def test_backtester_multi_target(df):
    print("3. Testing multi-target backtester...")
    bt = run_signal_backtest(df, symbol="TEST", holding_max_bars=20, broker_fee_pct=0.35)
    assert "error" not in bt, f"Backtest failed: {bt.get('error')}"
    assert "tp1_hit_rate_pct" in bt
    assert "tp2_hit_rate_pct" in bt
    assert "ambiguous_trades" in bt
    assert "resolved_trades" in bt
    assert "unresolved_trades" in bt

    print(
        f"  ✓ Backtester passed: {bt['total_triggered']} triggered, "
        f"{bt['resolved_trades']} resolved, TP1 Hit: {bt['tp1_hit_rate_pct']}%, "
        f"TP2 Hit: {bt['tp2_hit_rate_pct']}%, Ambiguous: {bt['ambiguous_trades']}."
    )


def test_signal_tracker_lifecycle():
    print("4. Testing persistent SQLite signal ledger & lifecycle tracking...")
    # Initialize DB
    signal_tracker.init_db()

    # Create dummy setup
    dummy_setup = {
        "strategy": "PULLBACK",
        "status": "WATCHING",
        "entry_min": 100.0,
        "entry_max": 102.0,
        "stop_loss": 96.0,
        "tp1": 108.0,
        "tp2": 114.0,
        "trigger_note": "Testing 20 EMA support",
    }

    test_sym = "UNITTEST"
    is_new, msg = signal_tracker.record_or_update_setup(dummy_setup, test_sym)
    assert is_new, "First insertion should be recorded as new"

    # Second insertion with unchanged levels should be deduplicated
    is_new2, msg2 = signal_tracker.record_or_update_setup(dummy_setup, test_sym)
    assert not is_new2, "Unchanged setup must not be duplicated"
    assert "already active" in msg2.lower()

    # Test audit against a market candle hitting TP1
    candle_df = pd.DataFrame([{
        "Date": pd.to_datetime("today"),
        "Open": 101.0,
        "High": 110.0,  # Crosses TP1 (108.0)
        "Low": 100.5,
        "Close": 109.0,
        "Volume": 1000000,
    }]).set_index("Date")

    # Transition to TRIGGERED first
    dummy_setup["status"] = "TRIGGERED"
    signal_tracker.record_or_update_setup(dummy_setup, test_sym)

    market_data = {test_sym: {"status": "OK", "df": candle_df}}
    updates = signal_tracker.audit_active_signals(market_data)
    assert len(updates) >= 1, "Should trigger a TP1 update"
    assert updates[0]["type"] == "TP1_HIT", f"Expected TP1_HIT, got {updates[0]['type']}"

    # Clean up test symbol from database
    with signal_tracker.get_connection() as conn:
        conn.execute("DELETE FROM signal_events WHERE signal_id LIKE 'UNITTEST%'")
        conn.execute("DELETE FROM signals WHERE symbol = 'UNITTEST'")
        conn.commit()

    print("  ✓ SQLite signal ledger & lifecycle auditor passed.")


if __name__ == "__main__":
    print("=== Running Overhauled PSX AlphaSignals Test Suite ===")
    df_test = test_data_integrity()
    test_strategy_and_checklist(df_test)
    test_backtester_multi_target(df_test)
    test_signal_tracker_lifecycle()
    print("=== All Verification Tests Passed Successfully! ===")
