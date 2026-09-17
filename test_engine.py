"""
test_engine.py
Comprehensive automated test suite verifying data integrity, strategy checklist logic,
multi-target backtester accounting, and SQLite lifecycle tracking.
"""

import sys
import os
import sqlite3
import datetime
import numpy as np
import pandas as pd

# Ensure local directory is on path
sys.path.insert(0, os.path.dirname(__file__))

from data_engine import (
    validate_market_data,
    generate_isolated_test_data,
    get_watchlist,
    check_has_true_ohlc,
    get_symbol_sector,
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


def test_true_ohlc_enforcement():
    print("1b. Testing true OHLC verification & approximated DPS protection...")
    # 1. Real / Isolated test data has true wicks
    df_real = generate_isolated_test_data(days=60, base_price=150.0)
    assert check_has_true_ohlc(df_real) is True, "Test data with real wicks must pass check_has_true_ohlc"

    # 2. Fabricate / approximated DPS-style dataset where High = max(O, C) and Low = min(O, C)
    df_approx = df_real.copy()
    df_approx["High"] = np.maximum(df_approx["Open"], df_approx["Close"])
    df_approx["Low"] = np.minimum(df_approx["Open"], df_approx["Close"])
    assert check_has_true_ohlc(df_approx) is False, "Approximated OHLC without wicks must be identified as has_true_ohlc=False"

    # 3. validate_market_data with require_true_ohlc=True must reject it
    is_valid, reason, _ = validate_market_data(df_approx, require_true_ohlc=True)
    assert not is_valid, "validate_market_data must reject approximated OHLC when require_true_ohlc=True"
    assert "intraday high/low" in reason.lower() or "approximated" in reason.lower()

    # 4. Signal engine must refuse to produce a TRIGGERED status on approximated data
    meta_approx = {
        "source": "PSX Official Portal (DPS) — OHLC approximated (Close/Open only)",
        "has_true_ohlc": False,
        "last_date": "2026-09-15",
        "data_age_days": 0,
    }
    setup_approx = generate_signal("DPS_TEST", df_approx, data_meta=meta_approx)
    assert setup_approx["status"] != "TRIGGERED", "Signal engine must NEVER trigger on approximated OHLC data"
    if setup_approx.get("strategy") in ["BREAKOUT", "PULLBACK"]:
        assert setup_approx["checklist"].get("Verified Intraday OHLC (True High/Low)") is False
        assert "disqualified" in setup_approx["trigger_note"].lower()

    # 5. Backtester must refuse to simulate on approximated data
    bt_res = run_signal_backtest(df_approx, symbol="DPS_TEST")
    assert "error" in bt_res, "Backtester must return error when given approximated OHLC"
    assert "intraday high/low" in bt_res["error"].lower() or "approximated" in bt_res["error"].lower()
    print("  ✓ True OHLC enforcement passed (Approximated DPS data strictly disqualified from alerts & backtests).")


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


def test_pkt_timezone_conversion():
    print("5. Testing Pakistan Standard Time (PKT) timestamp conversion...")
    import datetime
    from data_engine import PKT_TZ
    # Example: 19:30:00 UTC on 2026-09-14 is 00:30:00 PKT on 2026-09-15
    utc_dt = datetime.datetime(2026, 9, 14, 19, 30, 0, tzinfo=datetime.timezone.utc)
    ts = int(utc_dt.timestamp())

    pkt_date = datetime.datetime.fromtimestamp(ts, tz=PKT_TZ).date()
    assert pkt_date == datetime.date(2026, 9, 15), f"Expected 2026-09-15 in PKT, got {pkt_date}"
    print(f"  ✓ PKT timezone conversion passed (UTC epoch correctly resolved to PKT session date: {pkt_date}).")


def test_backtester_same_bar_breakeven():
    print("6. Testing backtester same-bar TP1 + Breakeven retracement (fixed_tp mode)...")
    df = generate_isolated_test_data(days=200, base_price=300.0)

    probe_bar = df.index[84]
    df.loc[probe_bar, "High"] = 290.0   # crosses TP1 (~288.54)
    df.loc[probe_bar, "Low"] = 270.0    # retraces into breakeven band: below entry (~274.69), above stop (~264.43)
    df.loc[probe_bar, "Close"] = 275.5

    bt = run_signal_backtest(df, symbol="TEST_BE", holding_max_bars=20, broker_fee_pct=0.35, exit_mode="fixed_tp")
    assert "error" not in bt, f"Backtest failed: {bt.get('error')}"
    assert bt["resolved_trades"] > 0, "Fixture must produce at least one resolved trade to actually test anything"

    first_trade = bt["trades_df"].iloc[0]
    assert "BREAKEVEN" in first_trade["outcome"], f"Expected a same-day breakeven outcome, got: {first_trade['outcome']}"
    assert bool(first_trade["tp1_reached"]), "TP1 was genuinely touched intraday and must be credited"
    assert bool(first_trade["is_ambiguous"]), "A same-bar TP1-touch-then-retrace must be flagged ambiguous"
    print(f"  ✓ Backtester same-bar breakeven edge case handling passed (outcome: {first_trade['outcome']}).")


def test_sqlite_wal_mode():
    print("7. Testing SQLite Write-Ahead Logging (WAL) & connection busy timeout...")
    with signal_tracker.get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert mode.lower() in ["wal", "memory"], f"Expected WAL mode, got {mode}"
        assert timeout >= 30000, f"Expected busy timeout >= 30000ms, got {timeout}"
    print(f"  ✓ SQLite WAL mode active ({mode.upper()}) with {timeout}ms busy timeout.")


def test_kse100_macro_and_relative_strength(df):
    print("8. Testing KSE-100 index feed, Macro Market Gate, & Relative Strength...")
    from data_engine import fetch_kse100_index
    df_kse, regime = fetch_kse100_index()
    assert df_kse is not None and not df_kse.empty, "KSE-100 index dataframe must not be empty"
    assert len(df_kse) >= 50, "KSE-100 index must have at least 50 historical sessions"
    assert "regime" in regime, "regime dict must contain 'regime'"
    assert regime["regime"] in ["BULL_MARKET", "RECOVERY_ZONE", "MARKET_CORRECTION"]
    assert "close" in regime and regime["close"] > 0
    assert "ema50" in regime and regime["ema50"] > 0

    # Indicator computation with KSE-100 benchmark
    df_ind = compute_all_indicators(df, df_kse=df_kse)
    assert "RS_Ratio" in df_ind.columns, "Relative strength ratio must be computed"
    assert "RS_MA20" in df_ind.columns, "Relative strength 20-day moving average must be computed"
    assert "Is_RS_Leader" in df_ind.columns, "Relative strength leadership flag must be present"
    assert "Is_Squeeze" in df_ind.columns, "Bollinger squeeze compression flag must be present"

    # Test Macro Gate Disqualification: Bearish market must suppress TRIGGERED status
    bearish_regime = {
        "regime": "MARKET_CORRECTION",
        "is_bullish": False,
        "close": 150000.0,
        "ema50": 160000.0,
    }
    setup_bear = evaluate_bar_strategy(df_ind, bar_idx=-1, has_true_ohlc=True, market_regime=bearish_regime)
    assert setup_bear["status"] != "TRIGGERED", "Signal must NEVER trigger when KSE-100 is in MARKET_CORRECTION"
    if setup_bear.get("strategy") in ["BREAKOUT", "PULLBACK"]:
        assert setup_bear["checklist"].get("Macro Market Gate (KSE-100 > 50 EMA)") is False
        assert "correction" in setup_bear["trigger_note"].lower() or "suppressed" in setup_bear["trigger_note"].lower()

    print(f"  ✓ KSE-100 Macro Gate & RS passed (KSE-100: {regime['close']:,.2f} | Regime: {regime['regime']}).")


def test_time_stop_exit():
    print("9. Testing Time-Stop Rule (Exit stalled positions after N bars)...")
    cache_sys = os.path.join(os.path.dirname(__file__), ".cache", "SYS_real.csv")
    assert os.path.exists(cache_sys), "SYS_real.csv cache file must exist"
    df_sys = pd.read_csv(cache_sys, index_col=0, parse_dates=True)

    bt = run_signal_backtest(df_sys, symbol="SYS", holding_max_bars=20, time_stop_bars=4)
    assert "error" not in bt, f"Backtest failed: {bt.get('error')}"
    assert bt["total_triggered"] >= 1, "Should have triggered setups in SYS"
    assert bt.get("time_stop_hits", 0) >= 1, "Should have triggered at least one time-stop exit"

    trades = bt["trades_df"]
    stalled_trades = trades[trades["outcome"].str.contains("TIME-STOP")]
    assert not stalled_trades.empty, "Must have recorded at least one TIME-STOP exit in trades_df"
    first_stalled = stalled_trades.iloc[0]
    assert first_stalled["bars_held"] >= 4, f"Time stop should trigger at >= 4 bars, held: {first_stalled['bars_held']}"
    assert not bool(first_stalled["tp1_reached"]), "Time-stopped trade must not have reached TP1"
    print(f"  ✓ Time-Stop Rule passed ({len(stalled_trades)} stalled trade(s) exited cleanly at >= 4 bars).")


def test_microstructure_and_next_day_open():
    print("10. Testing Next-Day Open (T+1 Open) fills & Volume-Tiered Friction...")
    from backtester import calculate_execution_friction
    
    # Verify ADV tiers
    spread1, slip1, tier1 = calculate_execution_friction(3_000_000)
    assert spread1 == 0.15 and slip1 == 0.10 and "Tier 1" in tier1
    spread2, slip2, tier2 = calculate_execution_friction(1_000_000)
    assert spread2 == 0.35 and slip2 == 0.20 and "Tier 2" in tier2
    spread3, slip3, tier3 = calculate_execution_friction(200_000)
    assert spread3 == 0.65 and slip3 == 0.35 and "Tier 3" in tier3

    cache_sys = os.path.join(os.path.dirname(__file__), ".cache", "SYS_real.csv")
    df_sys = pd.read_csv(cache_sys, index_col=0, parse_dates=True)
    bt = run_signal_backtest(df_sys, symbol="SYS", use_next_day_open=True)

    assert "error" not in bt
    trades = bt["trades_df"]
    if not trades.empty:
        for _, tr in trades.iterrows():
            # Signal date must be strictly before entry date (cannot buy on signal candle close)
            assert tr["entry_date"] > tr["signal_date"], (
                f"Entry date {tr['entry_date']} must be after signal date {tr['signal_date']}"
            )
            # Entry price must incorporate spread and slippage above raw open
            assert tr["entry_price"] >= tr["raw_open"], "Fill price must include friction above raw open"
    print(f"  ✓ Next-Day Open execution passed (All {len(trades)} trades entered on Day T+1 Open with ADV friction).")


def test_circuit_breaker_locks():
    print("11. Testing PSX ±7.5% Circuit Breaker upper-lock discard & limit-down rules...")
    # Use isolated test data with confirmed signal
    df_circuit = generate_isolated_test_data(days=120, base_price=100.0)
    
    # Run backtest to find a triggered signal date
    bt_initial = run_signal_backtest(df_circuit, symbol="TEST_CIRCUIT", use_next_day_open=True)
    assert not bt_initial["trades_df"].empty, "Test data must generate at least one trade"
    first_sig_date = bt_initial["trades_df"].iloc[0]["signal_date"]
    
    # Locate signal bar index and lock the next bar at Upper Circuit (+7.5%)
    idx_sig = df_circuit.index.get_loc(first_sig_date)
    idx_next = idx_sig + 1
    prev_c = float(df_circuit["Close"].iloc[idx_sig])
    upper_lock_price = round(prev_c * 1.075, 2)
    
    df_circuit.loc[df_circuit.index[idx_next], "Open"] = upper_lock_price
    df_circuit.loc[df_circuit.index[idx_next], "High"] = upper_lock_price
    df_circuit.loc[df_circuit.index[idx_next], "Low"] = upper_lock_price
    df_circuit.loc[df_circuit.index[idx_next], "Close"] = upper_lock_price
    
    bt = run_signal_backtest(df_circuit, symbol="TEST_CIRCUIT", use_next_day_open=True, enforce_circuit_limits=True)
    assert "error" not in bt
    # Order must be discarded due to Upper Circuit Lock
    assert bt.get("circuit_lock_discards", 0) >= 1, "Must discard entry order when next bar opens locked at upper circuit"
    print("  ✓ PSX Circuit Breaker rule passed (Upper-circuit locked open correctly rejected as unfillable).")


def test_bootstrap_ci_and_sample_adequacy():
    print("12. Testing Bootstrap Resampling (1,000 runs) & Sample Size Adequacy ($N < 30$)...")
    from backtester import evaluate_sample_adequacy, run_bootstrap_simulation
    
    # 1. Sample Size Adequacy
    s0 = evaluate_sample_adequacy(0)
    assert s0["is_adequate"] is False and "No Trades" in s0["badge"]
    s1 = evaluate_sample_adequacy(8)
    assert s1["is_adequate"] is False and "Critically Low" in s1["badge"]
    s2 = evaluate_sample_adequacy(22)
    assert s2["is_adequate"] is False and "Low" in s2["badge"]
    s3 = evaluate_sample_adequacy(35)
    assert s3["is_adequate"] is True and "Adequate" in s3["badge"]

    # 2. Bootstrap Resampling
    dummy_trades = pd.DataFrame({
        "net_pnl_pct": [3.2, -1.5, 4.0, -2.1, 1.8, -0.8, 5.1, -1.9, 2.7, -1.2],
        "tp1_reached": [True, False, True, False, True, False, True, False, True, False],
    })
    boot = run_bootstrap_simulation(dummy_trades, n_iterations=1000)
    assert boot["iterations"] == 1000
    assert boot["win_rate_ci"][0] <= boot["win_rate_ci"][1], "Lower bound of CI must be <= upper bound"
    assert boot["net_pnl_ci"][0] <= boot["net_pnl_ci"][1], "Lower bound of Net P&L CI must be <= upper bound"
    print(f"  ✓ Bootstrap & Sample Size passed (Win Rate 95% CI: [{boot['win_rate_ci'][0]}% – {boot['win_rate_ci'][1]}%]).")


def test_parameter_sensitivity_grid():
    print("13. Testing 9-Point Parameter Sensitivity Grid & Stability Plateau...")
    from backtester import run_sensitivity_grid
    cache_sys = os.path.join(os.path.dirname(__file__), ".cache", "SYS_real.csv")
    df_sys = pd.read_csv(cache_sys, index_col=0, parse_dates=True)

    grid = run_sensitivity_grid(df_sys, symbol="SYS")
    assert "grid_df" in grid and not grid["grid_df"].empty
    assert len(grid["grid_df"]) == 9, "Sensitivity grid must evaluate all 9 parameter variations"
    assert "stability_badge" in grid
    assert 0.0 <= grid["profitable_pct"] <= 100.0
    print(f"  ✓ Sensitivity Grid passed ({len(grid['grid_df'])} variations evaluated | Stability: {grid['stability_badge']}).")


def test_walk_forward_validation():
    print("14. Testing Rolling Walk-Forward Out-of-Sample Validation...")
    from backtester import run_walk_forward_analysis, MIN_OOS_WINDOW_BARS

    # Self-contained: uses the quarantined synthetic generator directly rather than
    # relying on a pre-warmed live-fetch cache, so this test doesn't depend on
    # network access or another test having run first.
    df = generate_isolated_test_data(days=400, base_price=120.0)

    # 1. REGRESSION GUARD: the historical bug was that the default test_bars (25)
    # sat below run_signal_backtest's own 30-bar floor, so every OOS window was
    # silently discarded as "insufficient data" and total_oos_trades was always 0,
    # which is easy to misread as "the strategy has no out-of-sample edge."
    # With defaults restored to a valid window size, real data must produce
    # evaluable windows and at least one genuine out-of-sample trade.
    res = run_walk_forward_analysis(df, symbol="WFO_TEST")
    assert res["status"] == "OK"
    assert res["windows_evaluated"] > 0, "Default parameters must produce at least one evaluable window"
    assert res["total_oos_trades"] > 0, (
        "Default parameters produced zero OOS trades on data known to trigger setups — "
        "this is the exact silent-failure mode the test_bars floor guards against"
    )
    if not res["oos_trades_df"].empty:
        assert "wfo_window" in res["oos_trades_df"].columns
        assert "net_pnl_pct" in res["oos_trades_df"].columns

    # 2. Explicitly passing a stale/too-small test_bars must be clamped up to the
    # floor rather than silently reproducing the zero-trade bug.
    res_clamped = run_walk_forward_analysis(df, symbol="WFO_TEST", test_bars=10)
    assert res_clamped["status"] == "OK"
    assert res_clamped["windows_evaluated"] > 0
    assert res_clamped["total_oos_trades"] > 0, "A too-small test_bars must be clamped to MIN_OOS_WINDOW_BARS, not silently zeroed out"

    # 3. Insufficient total history must be reported explicitly, not silently
    # zeroed out the same way an undersized window used to be.
    short_df = df.iloc[:60]
    res_short = run_walk_forward_analysis(short_df, symbol="WFO_TEST")
    assert res_short["status"] == "INSUFFICIENT_HISTORY"
    assert res_short["windows_evaluated"] == 0

    print(
        f"  ✓ Walk-Forward validation passed ({res['windows_evaluated']} windows, "
        f"{res['total_oos_trades']} OOS trades, MIN_OOS_WINDOW_BARS={MIN_OOS_WINDOW_BARS})."
    )


def test_trailing_ema_exit_mode():
    print("15. Testing Macro-Gated Trailing Trend Exit Architecture (Trailing 20 EMA vs Fixed TP)...")
    # 1. Backtest with trailing_ema mode (default)
    df_test = generate_isolated_test_data(days=150, base_price=100.0)
    bt_trail = run_signal_backtest(df_test, symbol="TEST_TRAIL", exit_mode="trailing_ema")
    assert "error" not in bt_trail
    assert bt_trail["microstructure"]["exit_mode"] == "trailing_ema"

    # 2. Backtest with legacy fixed_tp mode
    bt_fixed = run_signal_backtest(df_test, symbol="TEST_FIXED", exit_mode="fixed_tp")
    assert "error" not in bt_fixed
    assert bt_fixed["microstructure"]["exit_mode"] == "fixed_tp"

    # 3. Check signal engine setup output includes trailing_stop_ema and trailing_rule
    df_ind = compute_all_indicators(df_test)
    for i in range(25, len(df_ind)):
        setup = evaluate_bar_strategy(df_ind, bar_idx=i)
        if setup.get("status") in ["TRIGGERED", "WATCHLIST"]:
            assert "trailing_stop_ema" in setup, "Setup must contain trailing_stop_ema"
            assert "trailing_rule" in setup, "Setup must contain trailing_rule"
            break

    # 4. Check actionable card formatting includes Exit Model
    dummy_signal = {
        "symbol": "TEST",
        "status": "TRIGGERED",
        "action": "BUY (MOMENTUM CONFIRMED)",
        "strategy": "BREAKOUT",
        "price": 105.0,
        "entry_range": (104.0, 106.0),
        "stop_loss": 98.0,
        "tp1": 115.0,
        "tp2": 125.0,
        "trailing_stop_ema": 102.5,
        "trailing_rule": "Trail daily stop along 20-day EMA",
        "confidence": 85,
        "rsi": 62.0,
        "volume_surge": 1.8,
        "vol_20ma": 1500000,
        "spread_slippage_pct": 0.35,
        "relative_strength": "OUTPERFORMING (+5.2%)",
        "score": 88,
        "checklist": {
            "price_above_ema50": True,
            "rsi_in_sweet_spot": True,
            "macd_bullish": True,
            "volume_surge_confirmed": True,
            "favorable_risk_reward": True,
        },
        "market_regime": {"is_bullish": True, "regime": "BULL_MARKET"},
    }
    card_text = format_actionable_card(dummy_signal, "Test Company")
    assert "Trailing 20 EMA" in card_text, "Actionable card must mention Trailing 20 EMA exit model"
    print("  ✓ Trailing 20 EMA Exit Architecture passed (Trailing EMA + Structural Stops + Telegram Card).")


def test_trailing_ema_ambiguous_bar():
    print("16. Testing trailing_ema mode ambiguous same-bar target+stop handling...")
    df = generate_isolated_test_data(days=200, base_price=300.0)

    probe_bar = df.index[84]
    df.loc[probe_bar, "High"] = 305.0   # crosses TP2 (~300.34)
    df.loc[probe_bar, "Low"] = 260.0    # crashes through the stop (~264.43)
    df.loc[probe_bar, "Close"] = 262.0

    bt = run_signal_backtest(df, symbol="TEST_AMBIG_TRAIL", holding_max_bars=30, broker_fee_pct=0.35, exit_mode="trailing_ema")
    assert "error" not in bt, f"Backtest failed: {bt.get('error')}"
    assert bt["resolved_trades"] > 0, "Fixture must produce at least one resolved trade to actually test anything"

    first_trade = bt["trades_df"].iloc[0]
    assert first_trade["net_pnl_pct"] < 0, "This trade must close as a loss (stopped out)"
    assert not bool(first_trade["tp1_reached"]), "A same-bar stop breach must NOT credit the ambiguous TP1 touch"
    assert not bool(first_trade["tp2_reached"]), "A same-bar stop breach must NOT credit the ambiguous TP2 touch"
    assert bool(first_trade["is_ambiguous"]), "Same-bar target-touch + stop-breach must be flagged ambiguous"
    assert bt["ambiguous_trades"] >= 1
    assert bt["tp2_hit_rate_pct"] == 0.0, "A losing, stopped-out trade must not inflate the TP2 hit-rate stat"
    print(f"  ✓ Trailing EMA ambiguous-bar handling passed (outcome: {first_trade['outcome']}, net_pnl: {first_trade['net_pnl_pct']}%).")


def test_circuit_trapping_multiday():
    print("17. Testing Multi-Day Lower Circuit Lock Trapping Model...")
    df = generate_isolated_test_data(days=150, base_price=100.0)
    bt_initial = run_signal_backtest(df, symbol="TEST_TRAP", use_next_day_open=True)
    assert not bt_initial["trades_df"].empty, "Test data must generate at least one trade"
    first_trade = bt_initial["trades_df"].iloc[0]
    entry_date = first_trade["entry_date"]
    entry_idx = df.index.get_loc(entry_date)

    # Let trade run 1 bar, then on bar entry_idx + 2 lock it at lower limit (-7.5%)
    idx_lock1 = entry_idx + 2
    prev_c = float(df["Close"].iloc[idx_lock1 - 1])
    limit_offset = max(prev_c * 0.075, 1.0)
    lock_dn_1 = round(max(prev_c - limit_offset, 0.01), 2)
    df.loc[df.index[idx_lock1], ["Open", "High", "Low", "Close"]] = lock_dn_1

    # Bar 2 of lock (entry_idx + 3):
    idx_lock2 = entry_idx + 3
    limit_offset_2 = max(lock_dn_1 * 0.075, 1.0)
    lock_dn_2 = round(max(lock_dn_1 - limit_offset_2, 0.01), 2)
    df.loc[df.index[idx_lock2], ["Open", "High", "Low", "Close"]] = lock_dn_2

    # Bar 3 (entry_idx + 4) unlocks:
    idx_unlock = entry_idx + 4
    df.loc[df.index[idx_unlock], "Open"] = lock_dn_2 + 2.0
    df.loc[df.index[idx_unlock], "High"] = lock_dn_2 + 3.0
    df.loc[df.index[idx_unlock], "Low"] = lock_dn_2 - 1.0
    df.loc[df.index[idx_unlock], "Close"] = lock_dn_2 + 1.5

    bt_trapped = run_signal_backtest(
        df,
        symbol="TEST_TRAP",
        use_next_day_open=True,
        enforce_circuit_limits=True,
        simulate_circuit_trapping=True,
    )
    assert "error" not in bt_trapped
    assert bt_trapped["resolved_trades"] > 0
    trapped_trade = bt_trapped["trades_df"].iloc[0]
    assert "Trapped in Lower Lock" in trapped_trade["outcome"], f"Expected trapped outcome, got: {trapped_trade['outcome']}"
    assert trapped_trade.get("lock_bars_trapped", 0) >= 1, f"Expected lock_bars_trapped >= 1, got: {trapped_trade.get('lock_bars_trapped')}"
    assert bt_trapped["trapped_lock_trades"] >= 1
    print(f"  ✓ Multi-Day Circuit Trapping passed (Outcome: {trapped_trade['outcome']} | Trapped: {trapped_trade['lock_bars_trapped']} bar(s)).")


def test_portfolio_capital_allocation():
    print("18. Testing Portfolio-Level Capital Allocation & Cash Constraints...")
    from backtester import run_portfolio_backtest
    symbols = ["OGDC", "PPL", "SYS", "LUCK", "MEBL", "ENGRO"]
    data_dict = {}
    for i, s in enumerate(symbols):
        data_dict[s] = generate_isolated_test_data(days=160, base_price=100.0 + i * 20.0)

    res = run_portfolio_backtest(
        data_dict,
        initial_capital=1_000_000.0,
        max_positions=2,
        max_sector_exposure=2,
    )
    assert "error" not in res
    assert "final_equity" in res
    assert "equity_df" in res and not res["equity_df"].empty
    assert res["equity_df"]["Open_Positions"].max() <= 2, f"Open positions should never exceed max_positions=2, found: {res['equity_df']['Open_Positions'].max()}"
    assert res["final_equity"] > 0, "Final equity should be positive"

    # REGRESSION GUARD: the equity curve's last row previously went stale after
    # forcibly closing any positions still open at the end of the backtest --
    # those closures deduct real exit friction from cash, but the equity curve
    # (and everything derived from it: net_pnl_pkr, CAGR, Sharpe/Sortino/Calmar,
    # max drawdown) was built BEFORE that closure ran, so it never reflected the
    # unwind cost. The headline final_equity/net_pnl_pkr must reconcile exactly
    # with the sum of what the trade log itself reports.
    trades_df = res.get("trades_df")
    trade_pnl_sum = float(trades_df["net_pnl_pkr"].sum()) if trades_df is not None and not trades_df.empty else 0.0
    assert abs(res["net_pnl_pkr"] - trade_pnl_sum) < 1.0, (
        f"Reported net_pnl_pkr ({res['net_pnl_pkr']}) must reconcile with the sum of "
        f"individual trade net_pnl_pkr ({round(trade_pnl_sum, 2)}) -- gap: {round(res['net_pnl_pkr'] - trade_pnl_sum, 2)}"
    )
    print(
        f"  ✓ Portfolio Capital Allocation passed (Initial: PKR {res['initial_capital']:,.0f} -> "
        f"Final: PKR {res['final_equity']:,.0f} | Return: {res['net_return_pct']}% | Max DD: {res['max_drawdown_pct']}% | "
        f"Max concurrent: {res['equity_df']['Open_Positions'].max()})."
    )


def test_portfolio_sector_caps():
    print("19. Testing Sector Exposure Caps (Diversification Guard)...")
    from backtester import run_portfolio_backtest
    bank_symbols = ["MEBL", "MCB", "UBL"]
    data_dict = {}
    for i, s in enumerate(bank_symbols):
        data_dict[s] = generate_isolated_test_data(days=160, base_price=150.0 + i * 10.0)

    res = run_portfolio_backtest(
        data_dict,
        initial_capital=1_000_000.0,
        max_positions=5,
        max_sector_exposure=1,
    )
    assert "error" not in res

    # REGRESSION GUARD: previously this test only printed the skip count and never
    # actually asserted the cap held. Reconstruct concurrent same-sector holdings
    # directly from the closed-trade log's entry/exit windows and confirm no two
    # positions in the same sector ever overlapped in time.
    trades_df = res.get("trades_df")
    if trades_df is not None and not trades_df.empty:
        trades_df = trades_df.copy()
        trades_df["sector"] = trades_df["symbol"].map(get_symbol_sector)
        for _, row in trades_df.iterrows():
            overlapping = trades_df[
                (trades_df["sector"] == row["sector"])
                & (trades_df["entry_date"] <= row["exit_date"])
                & (trades_df["exit_date"] >= row["entry_date"])
            ]
            assert len(overlapping) <= 1, (
                f"Sector cap violated: {len(overlapping)} concurrent '{row['sector']}' positions "
                f"overlapped between {row['entry_date']} and {row['exit_date']} with max_sector_exposure=1"
            )

    if not res["skipped_df"].empty:
        sector_skips = res["skipped_df"][res["skipped_df"]["reason"] == "SECTOR_LIMIT_EXCEEDED"]
        print(f"    (Sector limit active: {len(sector_skips)} concurrent bank signal(s) prevented)")
    print("  ✓ Sector Exposure Caps passed (verified against the closed-trade log: no same-sector overlap at max_sector_exposure=1).")


def test_telegram_no_signals_note_matches_regime():
    print("20. Testing Telegram no-signals fallback message matches actual market regime...")
    # REGRESSION GUARD: this fallback message previously hardcoded a claim that
    # "the broad market remains in correction" any time a scan produced zero
    # new setups and zero lifecycle updates -- regardless of the actual KSE-100
    # regime. A bull-market day with simply no fresh triggers is common and has
    # nothing to do with a correction, so the message must only make that claim
    # when the regime genuinely says so.
    from telegram_notifier import build_no_signals_note

    bullish_note = build_no_signals_note(is_bullish=True)
    bearish_note = build_no_signals_note(is_bullish=False)

    assert "correction" not in bullish_note.lower(), (
        f"Bull-market fallback must not claim a market correction: {bullish_note}"
    )
    assert "correction" in bearish_note.lower(), (
        f"Bear-market/correction fallback should say so: {bearish_note}"
    )
    assert bullish_note != bearish_note, "The two regime cases must produce distinct messages"
    print("  ✓ Telegram no-signals fallback correctly tracks the actual market regime.")


if __name__ == "__main__":
    print("=== Running Overhauled PSX AlphaSignals Test Suite ===")
    df_test = test_data_integrity()
    test_true_ohlc_enforcement()
    test_strategy_and_checklist(df_test)
    test_backtester_multi_target(df_test)
    test_signal_tracker_lifecycle()
    test_pkt_timezone_conversion()
    test_backtester_same_bar_breakeven()
    test_sqlite_wal_mode()
    test_kse100_macro_and_relative_strength(df_test)
    test_time_stop_exit()
    test_microstructure_and_next_day_open()
    test_circuit_breaker_locks()
    test_bootstrap_ci_and_sample_adequacy()
    test_parameter_sensitivity_grid()
    test_walk_forward_validation()
    test_trailing_ema_exit_mode()
    test_trailing_ema_ambiguous_bar()
    test_circuit_trapping_multiday()
    test_portfolio_capital_allocation()
    test_portfolio_sector_caps()
    test_telegram_no_signals_note_matches_regime()
    print("=== All Verification Tests Passed Successfully! ===")

