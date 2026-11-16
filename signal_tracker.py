"""
signal_tracker.py
Persistent Signal Ledger (SQLite) & Lifecycle Tracker.
Tracks every setup from creation to resolution, maintains trade history,
and records explicit updates without rewriting history.
"""

import os
import sqlite3
import datetime
import numpy as np
import pandas as pd

from swing_config import (
    STATE_RANK,
    MIN_TIME_STOP_MFE_R,
    PARTIAL_EXIT_FRACTION,
    TRAIL_RUNNER_AFTER_TP1,
    get_config,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "signals.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging (WAL) and busy timeout for safe concurrent multi-process access
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            track TEXT DEFAULT 'swing',
            status TEXT NOT NULL,
            created_date TEXT NOT NULL,
            triggered_date TEXT,
            entry_min REAL,
            entry_max REAL,
            stop_loss REAL,
            tp1 REAL,
            tp2 REAL,
            resolved_date TEXT,
            exit_price REAL,
            gross_pnl_pct REAL,
            net_pnl_pct REAL,
            notes TEXT
        )
        """)
        # Ensure 'track' column exists for existing tables
        cursor.execute("PRAGMA table_info(signals)")
        columns = [col[1] for col in cursor.fetchall()]
        if "track" not in columns:
            cursor.execute("ALTER TABLE signals ADD COLUMN track TEXT DEFAULT 'swing'")

        # Tiered-signal columns (signal v2). Added in place so existing ledgers
        # keep their history and old rows simply read back as version 1.0.
        tiered_columns = {
            "signal_version": "TEXT DEFAULT '1.0'",
            "state": "TEXT",
            "score": "INTEGER",
            "grade": "TEXT",
            "entry_trigger": "REAL",
            "trigger_condition": "TEXT",
        }
        for col_name, col_type in tiered_columns.items():
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")
                columns.append(col_name)

        # Stage-0 ledger integrity columns. Every trade is now measured against the
        # benchmark over the identical holding window, and the reason a row closed is
        # stored explicitly so the ledger can be audited without re-reading notes.
        integrity_columns = {
            "benchmark_pnl_pct": "REAL",   # KSE-100 (or proxy) return, entry -> exit
            "excess_pnl_pct": "REAL",      # net_pnl_pct - benchmark_pnl_pct
            "closed_reason": "TEXT",       # STOP / TP1_BE / TP2 / PARTIAL_MARK / EXPIRED / INVALIDATED
            "initial_stop_loss": "REAL",   # pre-TP1 stop, so R-multiples survive the BE move
        }
        for col_name, col_type in integrity_columns.items():
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")
                columns.append(col_name)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals (id)
        )
        """)
        conn.commit()


# Initialize database schema on load
init_db()


# --- Ledger integrity constants (Stage 0) ---------------------------------
# Statuses that still need auditing against new candles. TP1_HIT is open on
# purpose: the runner is still live after half the position is booked.
AUDITABLE_STATUSES = ("WATCHING", "TRIGGERED", "TP1_HIT")
# Statuses that represent a finished trade (net_pnl_pct is always set on these).
TERMINAL_STATUSES = (
    "STOPPED_OUT", "TP1_BE", "TP2_HIT", "PARTIAL_CLOSED",
    "TIME_STOP", "TRAIL_STOP", "HOLDING_CAP", "EXPIRED",
)
# Fraction booked at TP1; the remainder runs to TP2 / breakeven / mark-to-market.
# Defined in swing_config so the walk-forward harness and the live ledger share it.
# A WATCHING setup that never triggers is not a trade and must not sit in the
# ledger forever: expire it after this many trading days (Mon-Fri, PSX calendar).
STALE_WATCH_TRADING_DAYS = 15
# Persistence statuses whose names changed in Stage 0 but must stay readable.
_STATUS_ALIASES = {"TP1_HIT_CLOSED": "PARTIAL_CLOSED"}


def _benchmark_series(df_bench):
    """Normalises a benchmark frame into a date-indexed close series (or None)."""
    if df_bench is None or len(df_bench) == 0 or "Close" not in df_bench:
        return None
    series = df_bench["Close"].astype(float).copy()
    index = pd.to_datetime(series.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    series.index = index.normalize()
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series if not series.empty else None


def compute_benchmark_return(df_bench, start_date, end_date, max_staleness_days: int = 5):
    """
    Benchmark return (%) over the same window a trade was held.

    Returns None instead of guessing when the benchmark series does not actually
    cover the exit date -- a stale index must never be used to claim or deny alpha.
    """
    series = _benchmark_series(df_bench)
    if series is None:
        return None
    try:
        start = pd.Timestamp(str(start_date)).normalize()
        end = pd.Timestamp(str(end_date)).normalize()
    except (ValueError, TypeError):
        return None
    if start > end:
        start, end = end, start
    if (end - series.index[-1]).days > max_staleness_days:
        return None
    start_slice = series.loc[:start]
    end_slice = series.loc[:end]
    if start_slice.empty or end_slice.empty:
        return None
    base = float(start_slice.iloc[-1])
    final = float(end_slice.iloc[-1])
    if base <= 0:
        return None
    return round(((final / base) - 1.0) * 100.0, 2)


def _attach_benchmark(cursor, sig, exit_date: str, net_pnl_pct, df_bench) -> None:
    """Stores benchmark + excess return for one resolved trade (no-op without a feed)."""
    if df_bench is None or net_pnl_pct is None:
        return
    entry_date = sig["triggered_date"] or sig["created_date"]
    bench_pct = compute_benchmark_return(df_bench, entry_date, exit_date)
    if bench_pct is None:
        return
    excess = round(float(net_pnl_pct) - bench_pct, 2)
    cursor.execute(
        "UPDATE signals SET benchmark_pnl_pct = ?, excess_pnl_pct = ? WHERE id = ?",
        (bench_pct, excess, sig["id"]),
    )


def record_or_update_setup(setup: dict, symbol: str) -> tuple[bool, str]:
    """
    Records a new setup or checks if an open setup already exists.
    Avoids re-inserting unchanged setups every day.
    Returns (is_new, message).
    """
    strategy = setup.get("strategy")
    status = setup.get("status")
    if not strategy or strategy == "NONE" or status not in ["WATCHING", "TRIGGERED"]:
        return False, "Not an actionable setup."

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        cursor = conn.cursor()
        # Look for existing active setup for this symbol
        cursor.execute(
            "SELECT * FROM signals WHERE symbol = ? AND status IN ('WATCHING', 'TRIGGERED')",
            (symbol,),
        )
        existing = cursor.fetchone()

        new_state = setup.get("state") or ("BUY_NOW" if status == "TRIGGERED" else "WATCH")

        if existing:
            # Tiered upgrades: WATCH -> BUY_ZONE -> BUY_NOW must each notify once,
            # otherwise a setup that moves into the buy zone is never announced.
            old_state = existing["state"] if "state" in existing.keys() else None
            if not old_state:
                old_state = "BUY_NOW" if existing["status"] == "TRIGGERED" else "WATCH"
            old_rank = STATE_RANK.get(old_state, 0)
            new_rank = STATE_RANK.get(new_state, 0)

            if new_rank > old_rank:
                new_status = "TRIGGERED" if new_state == "BUY_NOW" else "WATCHING"
                cursor.execute(
                    """
                    UPDATE signals
                    SET status = ?, state = ?, score = ?, grade = ?, entry_trigger = ?,
                        trigger_condition = ?, signal_version = ?,
                        triggered_date = COALESCE(triggered_date, ?), notes = ?
                    WHERE id = ?
                    """,
                    (
                        new_status,
                        new_state,
                        setup.get("score"),
                        setup.get("grade"),
                        setup.get("entry_trigger"),
                        setup.get("trigger_condition"),
                        setup.get("signal_version"),
                        today_str if new_state == "BUY_NOW" else None,
                        setup.get("trigger_note", ""),
                        existing["id"],
                    ),
                )
                cursor.execute(
                    "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                    (
                        existing["id"],
                        now_ts,
                        new_state,
                        f"{old_state} -> {new_state}: {setup.get('trigger_note')}",
                    ),
                )
                conn.commit()
                return True, f"Upgraded ${symbol} from {old_state} to {new_state}."

            # Refresh conviction on an active setup without re-alerting
            if new_state == old_state:
                cursor.execute(
                    "UPDATE signals SET score = ?, grade = ? WHERE id = ?",
                    (setup.get("score"), setup.get("grade"), existing["id"]),
                )
                conn.commit()
            return False, f"Setup for ${symbol} already active ({old_state}). Levels unchanged."

        # New setup detected
        sig_id = f"{symbol}-{datetime.datetime.now().strftime('%Y%m%d%H%M')}-{strategy[:3]}"
        triggered_date = today_str if status == "TRIGGERED" else None
        track = setup.get("track", "swing" if strategy in ["BREAKOUT", "PULLBACK"] else "tactical")

        cursor.execute(
            """
            INSERT INTO signals (
                id, symbol, strategy, track, status, created_date, triggered_date,
                entry_min, entry_max, stop_loss, tp1, tp2, notes,
                signal_version, state, score, grade, entry_trigger, trigger_condition,
                initial_stop_loss
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sig_id,
                symbol,
                strategy,
                track,
                status,
                today_str,
                triggered_date,
                setup.get("entry_min"),
                setup.get("entry_max"),
                setup.get("stop_loss"),
                setup.get("tp1"),
                setup.get("tp2"),
                setup.get("trigger_note"),
                setup.get("signal_version"),
                new_state,
                setup.get("score"),
                setup.get("grade"),
                setup.get("entry_trigger"),
                setup.get("trigger_condition"),
                setup.get("stop_loss"),
            ),
        )
        cursor.execute(
            "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
            (sig_id, now_ts, "CREATED", f"Setup created ({new_state}): {setup.get('trigger_note')}"),
        )
        conn.commit()
        return True, f"New setup recorded for ${symbol} ({new_state})."


def audit_active_signals(market_data: dict, broker_fee_pct: float = 0.35, df_bench=None,
                         mode: str = None) -> list[dict]:
    """
    Checks all open setups against the latest market session candle.
    Updates lifecycle states and returns a list of update messages to send.

    TP1_HIT rows stay in the audit set: after half the position is booked at TP1
    the runner still has to resolve at TP2, at breakeven, or as a stale partial --
    previously these fell out of the query and stayed "open" in the ledger forever.
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    exit_cfg = _exit_config(mode)

    with get_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in AUDITABLE_STATUSES)
        cursor.execute(
            f"SELECT * FROM signals WHERE status IN ({placeholders}) AND resolved_date IS NULL",
            AUDITABLE_STATUSES,
        )
        active_list = cursor.fetchall()

        for sig in active_list:
            sym = sig["symbol"]
            stock_info = market_data.get(sym)
            if not stock_info or stock_info.get("status") != "OK":
                continue

            df = stock_info.get("df")
            if df is None or df.empty:
                continue

            latest_bar = df.iloc[-1]
            high = float(latest_bar["High"])
            low = float(latest_bar["Low"])
            close = float(latest_bar["Close"])

            entry = sig["entry_max"]
            sl = sig["stop_loss"]
            tp1 = sig["tp1"]
            tp2 = sig["tp2"]

            # Stage 1: how long has this trade been open, and how much has it run in R?
            bars_held = _bars_since(df, sig["triggered_date"]) if sig["triggered_date"] else 0
            base_stop = sig["initial_stop_loss"] if sig["initial_stop_loss"] else sl
            risk = (entry - base_stop) if (entry and base_stop and entry > base_stop) else None

            # If setup was only WATCHING, check if invalidated by closing below stop
            if sig["status"] == "WATCHING":
                if close < sl:
                    cursor.execute(
                        "UPDATE signals SET status = 'INVALIDATED', resolved_date = ?, closed_reason = 'INVALIDATED' WHERE id = ?",
                        (today_str, sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "INVALIDATED", f"Setup invalidated: close ({close:.2f}) broke support ({sl:.2f})."),
                    )
                    updates.append({
                        "symbol": sym,
                        "type": "INVALIDATED",
                        "text": f"⚠️ <b>UPDATE on ${sym}</b>: Setup invalidated. Price ({close:.2f}) closed below planned support ({sl:.2f}).",
                    })
                    continue

            # A TP1 runner: half booked at TP1, stop sits at breakeven, TP2 still live.
            # Resolve it at breakeven or TP2 so the ledger stops treating it as open.
            if sig["status"] == "TP1_HIT":
                p1_ret = ((tp1 - entry) / entry) if (tp1 and entry) else 0.0
                be_stop = sl if sl else entry
                effective_stop = be_stop
                trail_period = exit_cfg.get("trailing_ema")
                # Stage 1: once the runner has been open for the keep-before-trail
                # window it rides the mode's EMA, so a trade that stalls gives back
                # far less than the old "wait for TP2" model.
                if TRAIL_RUNNER_AFTER_TP1 and trail_period and bars_held >= exit_cfg.get("min_hold_before_trail", 0):
                    trail_stop = _ema_value(df, trail_period)
                    if trail_stop and trail_stop > (effective_stop or 0):
                        effective_stop = round(trail_stop, 2)
                gross = None
                exit_price = None
                new_status = None
                reason = None
                note = None

                if effective_stop and low <= effective_stop:
                    # Ambiguous bar favours the conservative outcome, as elsewhere.
                    exit_gain = (effective_stop - entry) / entry
                    gross = PARTIAL_EXIT_FRACTION * p1_ret + (1 - PARTIAL_EXIT_FRACTION) * exit_gain
                    exit_price = effective_stop
                    if be_stop and effective_stop > be_stop:
                        new_status = "TRAIL_STOP"
                        reason = "TRAIL_STOP"
                        note = f"TP1 booked; runner trailed out at EMA-{trail_period}"
                    else:
                        new_status = "TP1_BE"
                        reason = "TP1_BE"
                        note = "TP1 booked on half; runner closed at breakeven stop"
                elif tp2 and high >= tp2:
                    gross = PARTIAL_EXIT_FRACTION * p1_ret + (1 - PARTIAL_EXIT_FRACTION) * ((tp2 - entry) / entry)
                    exit_price = tp2
                    new_status = "TP2_HIT"
                    reason = "TP2"
                    note = "TP1 and TP2 both booked (50/50 blend)"
                else:
                    continue

                net = gross - (broker_fee_pct / 100.0)
                cursor.execute(
                    """
                    UPDATE signals
                    SET status = ?, resolved_date = ?, exit_price = ?, gross_pnl_pct = ?,
                        net_pnl_pct = ?, closed_reason = ?, notes = ?
                    WHERE id = ?
                    """,
                    (new_status, today_str, exit_price, round(gross * 100, 2),
                     round(net * 100, 2), reason, note, sig["id"]),
                )
                cursor.execute(
                    "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                    (sig["id"], now_ts, new_status,
                     f"{note}: entry {entry:.2f} -> exit {exit_price:.2f} (net {net*100:.2f}%)."),
                )
                _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                icon = "🎯🎯" if new_status == "TP2_HIT" else "➖"
                updates.append({
                    "symbol": sym,
                    "type": new_status,
                    "text": f"{icon} <b>UPDATE on ${sym}</b>: TP1 runner closed at {exit_price:.2f} PKR (Net: {net*100:+.1f}%).",
                })
                continue

            # If setup is TRIGGERED, track trade resolution
            if sig["status"] == "TRIGGERED":
                # Check ambiguous bar
                is_ambiguous = high >= tp1 and low <= sl

                if is_ambiguous:
                    # Conservative accounting: mark stopped out under ambiguous conditions
                    gross = (sl - entry) / entry
                    net = gross - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals 
                        SET status = 'STOPPED_OUT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?, notes = ?
                        WHERE id = ?
                        """,
                        (today_str, sl, round(gross * 100, 2), round(net * 100, 2), "STOP_AMBIGUOUS", "Ambiguous bar (touched both TP1 and SL)", sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "STOP_HIT", "Stopped out on ambiguous bar."),
                    )
                    _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                    updates.append({
                        "symbol": sym,
                        "type": "STOP_HIT",
                        "text": f"🛑 <b>UPDATE on ${sym}</b>: Ambiguous bar touched both TP1 and SL. Marked as stopped out at {sl:.2f} PKR (Net: {net*100:.1f}%).",
                    })
                    continue

                # Check Stop Loss hit
                if low <= sl:
                    gross = (sl - entry) / entry
                    net = gross - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals 
                        SET status = 'STOPPED_OUT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?
                        WHERE id = ?
                        """,
                        (today_str, sl, round(gross * 100, 2), round(net * 100, 2), "STOP", sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "STOP_HIT", f"Stop loss hit at {sl:.2f}."),
                    )
                    _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                    updates.append({
                        "symbol": sym,
                        "type": "STOP_HIT",
                        "text": f"🛑 <b>UPDATE on ${sym}</b>: Stop Loss triggered at {sl:.2f} PKR. Trade closed (Net: {net*100:.1f}%).",
                    })
                    continue

                # Check TP2 hit
                if high >= tp2:
                    p1_ret = (tp1 - entry) / entry
                    p2_ret = (tp2 - entry) / entry
                    gross = PARTIAL_EXIT_FRACTION * p1_ret + (1 - PARTIAL_EXIT_FRACTION) * p2_ret
                    net = gross - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals 
                        SET status = 'TP2_HIT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?
                        WHERE id = ?
                        """,
                        (today_str, tp2, round(gross * 100, 2), round(net * 100, 2), "TP2", sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "TP2_HIT", f"Full target 2 reached at {tp2:.2f}."),
                    )
                    _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                    updates.append({
                        "symbol": sym,
                        "type": "TP2_HIT",
                        "text": f"🎯🎯 <b>UPDATE on ${sym}</b>: Target 2 REACHED at {tp2:.2f} PKR! Trade closed (Net: +{net*100:.1f}%).",
                    })
                    continue

                # Check TP1 hit
                if high >= tp1:
                    p1_ret = (tp1 - entry) / entry
                    net_tp1 = p1_ret - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals 
                        SET status = 'TP1_HIT', stop_loss = ?,
                            initial_stop_loss = COALESCE(initial_stop_loss, stop_loss),
                            notes = 'TP1 hit, SL moved to breakeven'
                        WHERE id = ?
                        """,
                        (entry, sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "TP1_HIT", f"TP1 reached at {tp1:.2f}. Stop moved to BE ({entry:.2f})."),
                    )
                    updates.append({
                        "symbol": sym,
                        "type": "TP1_HIT",
                        "text": f"🎯 <b>UPDATE on ${sym}</b>: Target 1 HIT at {tp1:.2f} PKR (+{net_tp1*100:.1f}%). 50% profit booked; SL moved to breakeven ({entry:.2f}).",
                    })
                    continue

                # --- Stage 1: time stop ------------------------------------------------
                # A trade that has not run MIN_TIME_STOP_MFE_R in its favour by the
                # configured time stop is dead money. The live record showed losers
                # held ~24 trading days while winners were never banked.
                if risk and bars_held >= exit_cfg.get("time_stop_bars", 10 ** 6):
                    mfe_r = _mfe_r(df, bars_held, entry, risk)
                    if mfe_r < MIN_TIME_STOP_MFE_R:
                        gross = (close - entry) / entry
                        net = gross - (broker_fee_pct / 100.0)
                        cursor.execute(
                            """
                            UPDATE signals
                            SET status = 'TIME_STOP', resolved_date = ?, exit_price = ?,
                                gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?, notes = ?
                            WHERE id = ?
                            """,
                            (today_str, round(close, 2), round(gross * 100, 2), round(net * 100, 2),
                             "TIME_STOP",
                             f"No progress ({mfe_r:.2f}R best excursion) after {bars_held} trading days",
                             sig["id"]),
                        )
                        cursor.execute(
                            "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                            (sig["id"], now_ts, "TIME_STOP",
                             f"Time stop after {bars_held} bars at {close:.2f} (MFE {mfe_r:.2f}R)."),
                        )
                        _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                        updates.append({
                            "symbol": sym,
                            "type": "TIME_STOP",
                            "text": (
                                f"⏱ <b>UPDATE on ${sym}</b>: Time stop — no progress "
                                f"({mfe_r:.2f}R in {bars_held} days). Closed at {close:.2f} PKR "
                                f"(Net: {net*100:+.1f}%)."
                            ),
                        })
                        continue

                # --- Stage 1: hard holding cap -----------------------------------------
                if exit_cfg.get("holding_max_bars") and bars_held >= exit_cfg["holding_max_bars"]:
                    gross = (close - entry) / entry
                    net = gross - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals
                        SET status = 'HOLDING_CAP', resolved_date = ?, exit_price = ?,
                            gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?, notes = ?
                        WHERE id = ?
                        """,
                        (today_str, round(close, 2), round(gross * 100, 2), round(net * 100, 2),
                         "HOLDING_CAP", f"Maximum holding period reached ({bars_held} trading days)",
                         sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "HOLDING_CAP",
                         f"Holding cap after {bars_held} bars at {close:.2f}."),
                    )
                    _attach_benchmark(cursor, sig, today_str, round(net * 100, 2), df_bench)
                    updates.append({
                        "symbol": sym,
                        "type": "HOLDING_CAP",
                        "text": (
                            f"⌛ <b>UPDATE on ${sym}</b>: Maximum holding period ({bars_held} days) "
                            f"reached. Closed at {close:.2f} PKR (Net: {net*100:+.1f}%)."
                        ),
                    })

        conn.commit()
    return updates


def get_active_signals(track: str = None) -> list[dict]:
    """Returns all currently active (WATCHING or TRIGGERED) setups, optionally filtered by track."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if track:
            cursor.execute(
                "SELECT * FROM signals WHERE status IN ('WATCHING', 'TRIGGERED') AND track = ? ORDER BY created_date DESC",
                (track,),
            )
        else:
            cursor.execute("SELECT * FROM signals WHERE status IN ('WATCHING', 'TRIGGERED') ORDER BY created_date DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_performance_summary(track: str = None) -> dict:
    """
    Computes audited performance from real signals actually recorded in signals.db.
    Supports filtering by track ('swing' or 'tactical') or returning overall + per-track breakdown.
    Completely separated from historical simulations.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if track:
            cursor.execute("SELECT * FROM signals WHERE track = ?", (track,))
        else:
            cursor.execute("SELECT * FROM signals")
        all_signals = cursor.fetchall()

        def compute_stats(df_subset):
            total = len(df_subset)
            if total == 0:
                return {
                    "total_recorded": 0,
                    "active_count": 0,
                    "entered_count": 0,
                    "closed_count": 0,
                    "open_runner_count": 0,
                    "expired_count": 0,
                    "invalidated_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate_pct": 0.0,
                    "net_pnl_pct": 0.0,
                    "avg_trade_pnl": 0.0,
                    "avg_excess_pnl": None,
                }

            status = df_subset["status"].fillna("").astype(str)
            # A setup only becomes a *trade* once it is triggered. Everything else
            # (watching, expired, never entered) must stay out of the trade statistics,
            # otherwise the win rate is computed over rows that have no P&L at all.
            entered = df_subset["triggered_date"].notna()
            resolved = df_subset["net_pnl_pct"].notna()
            active = status.isin(["WATCHING", "TRIGGERED"]) & ~resolved
            open_runners = (status == "TP1_HIT") & ~resolved
            expired = status == "EXPIRED"
            invalidated = status == "INVALIDATED"
            closed = df_subset[resolved]

            stats = {
                "total_recorded": total,
                "active_count": int(active.sum()),
                "entered_count": int(entered.sum()),
                "closed_count": len(closed),
                "open_runner_count": int(open_runners.sum()),
                "expired_count": int(expired.sum()),
                "invalidated_count": int(invalidated.sum()),
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "net_pnl_pct": 0.0,
                "avg_trade_pnl": 0.0,
                "avg_excess_pnl": None,
            }

            if closed.empty:
                stats["message"] = (
                    f"{total} setups recorded ({int(active.sum())} active, 0 settled trades, "
                    f"{int(expired.sum())} expired without entry)."
                )
                return stats

            pnl = closed["net_pnl_pct"].astype(float)
            wins = int((pnl > 0).sum())
            stats.update({
                "wins": wins,
                "losses": len(closed) - wins,
                "win_rate_pct": round((wins / len(closed)) * 100, 1),
                "net_pnl_pct": round(float(pnl.sum()), 2),
                "avg_trade_pnl": round(float(pnl.mean()), 2),
                "ledger_df": closed,
            })
            if "excess_pnl_pct" in closed.columns and closed["excess_pnl_pct"].notna().any():
                stats["avg_excess_pnl"] = round(float(closed["excess_pnl_pct"].dropna().mean()), 2)
            return stats

        if not all_signals:
            track_label = f" for {track} track" if track else ""
            return {
                "total_recorded": 0,
                "active_count": 0,
                "closed_count": 0,
                "win_rate_pct": 0.0,
                "net_pnl_pct": 0.0,
                "avg_trade_pnl": 0.0,
                "message": f"No signals have been recorded in the live ledger{track_label} yet.",
            }

        df = pd.DataFrame([dict(r) for r in all_signals])
        summary = compute_stats(df)

        if track is None:
            df_swing = df[df["track"] == "swing"] if "track" in df.columns else pd.DataFrame()
            df_tactical = df[df["track"] == "tactical"] if "track" in df.columns else pd.DataFrame()
            summary["swing"] = compute_stats(df_swing)
            summary["tactical"] = compute_stats(df_tactical)

        return summary


def trading_days_between(start_date, end_date) -> int:
    """Weekday count (Mon-Fri, the PSX session calendar) between two ISO dates."""
    try:
        start = pd.Timestamp(str(start_date)).normalize()
        end = pd.Timestamp(str(end_date)).normalize()
    except (ValueError, TypeError):
        return 0
    if end <= start:
        return 0
    return int(np.busday_count(start.date(), end.date()))


# --- Stage 1 exit helpers ---------------------------------------------------
def _exit_config(mode: str = None) -> dict:
    """Mode config for exit management, resilient to an unknown mode string."""
    try:
        return get_config(mode)
    except (ValueError, KeyError):
        return get_config()


def _bars_since(df, since_date) -> int:
    """Completed bars in `df` since the bar dated on/after `since_date` (0 = today)."""
    if df is None or len(df) == 0 or not since_date:
        return 0
    try:
        target = pd.Timestamp(str(since_date)).normalize()
    except (ValueError, TypeError):
        return 0
    index = pd.to_datetime(df.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    position = int((index.normalize() <= target).sum()) - 1
    if position < 0:
        return 0
    return max(0, len(df) - 1 - position)


def _ema_value(df, period: int, position: int = -1):
    """Latest EMA of Close at `position`, or None when there is too little data."""
    minimum = max(2, min(int(period), 20))   # an EMA needs a few bars, not its full span
    if df is None or len(df) < minimum or "Close" not in df:
        return None
    series = df["Close"].astype(float).ewm(span=int(period), adjust=False).mean()
    value = float(series.iloc[position])
    return value if value > 0 else None


def _mfe_r(df, bars_held: int, entry: float, risk: float) -> float:
    """Maximum favourable excursion since entry, in risk units (R)."""
    if df is None or bars_held <= 0 or not risk or risk <= 0:
        return 0.0
    window = df.iloc[-(bars_held + 1):]
    if window.empty or "High" not in window:
        return 0.0
    return round((float(window["High"].max()) - float(entry)) / float(risk), 3)


def _verified_close(sig, market_data):
    """Latest verified close for a symbol, or None. Never invents a price."""
    info = (market_data or {}).get(sig["symbol"])
    if not info or info.get("status") != "OK":
        return None
    df = info.get("df")
    if df is None or len(df) == 0 or "Close" not in df:
        return None
    try:
        close = float(df.iloc[-1]["Close"])
    except (TypeError, ValueError, IndexError):
        return None
    return close if close > 0 else None


def finalize_partials(market_data: dict = None, broker_fee_pct: float = 0.35,
                      max_age_trading_days: int = None, df_bench=None,
                      as_of: str = None) -> list[dict]:
    """
    Closes TP1 runners that can no longer resolve on their own.

    A TP1_HIT row means half the position was booked at TP1 and the other half is
    still riding. If the runner never reaches TP2 or the breakeven stop, the ledger
    keeps it open forever and the trade disappears from every performance number.
    This marks the remainder to the latest *verified* close and books the blend:

        net = 0.5 x TP1 return + 0.5 x (last close - entry) / entry - fees

    Rows without a verified candle are reported back untouched -- the project's
    no-synthetic-prices rule wins over having a tidier ledger.
    """
    as_of = as_of or datetime.date.today().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM signals WHERE status = 'TP1_HIT' AND resolved_date IS NULL")
        runners = cursor.fetchall()

        for sig in runners:
            entry = sig["entry_max"]
            tp1 = sig["tp1"]
            if not entry or not tp1:
                updates.append({
                    "symbol": sig["symbol"],
                    "type": "PARTIAL_SKIPPED",
                    "text": f"⚠️ Cannot finalise ${sig['symbol']}: ledger row has no entry/TP1 level.",
                })
                continue

            age = trading_days_between(sig["triggered_date"] or sig["created_date"], as_of)
            if max_age_trading_days is not None and age < max_age_trading_days:
                continue

            mark = _verified_close(sig, market_data)
            if mark is None:
                updates.append({
                    "symbol": sig["symbol"],
                    "type": "PARTIAL_SKIPPED",
                    "text": (
                        f"⚠️ Cannot finalise ${sig['symbol']} TP1 runner: no verified close available "
                        f"(held {age} trading days). Left open rather than guessing a price."
                    ),
                })
                continue

            p1_ret = (tp1 - entry) / entry
            mark_ret = (mark - entry) / entry
            gross = PARTIAL_EXIT_FRACTION * p1_ret + (1 - PARTIAL_EXIT_FRACTION) * mark_ret
            net = gross - (broker_fee_pct / 100.0)
            net_pct = round(net * 100, 2)
            cursor.execute(
                """
                UPDATE signals
                SET status = 'PARTIAL_CLOSED', resolved_date = ?, exit_price = ?,
                    gross_pnl_pct = ?, net_pnl_pct = ?, closed_reason = ?, notes = ?
                WHERE id = ?
                """,
                (as_of, round(mark, 2), round(gross * 100, 2), net_pct, "PARTIAL_MARK",
                 f"TP1 booked on {PARTIAL_EXIT_FRACTION:.0%}; runner marked to {mark:.2f} "
                 f"after {age} trading days", sig["id"]),
            )
            cursor.execute(
                "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                (sig["id"], now_ts, "PARTIAL_CLOSED",
                 f"Runner finalised at {mark:.2f} (entry {entry:.2f}, TP1 {tp1:.2f}, net {net_pct:+.2f}%)."),
            )
            _attach_benchmark(cursor, sig, as_of, net_pct, df_bench)
            updates.append({
                "symbol": sig["symbol"],
                "type": "PARTIAL_CLOSED",
                "text": (
                    f"📏 <b>LEDGER on ${sig['symbol']}</b>: TP1 runner finalised at {mark:.2f} PKR "
                    f"(Net: {net_pct:+.1f}%) after {age} trading days."
                ),
            })

        conn.commit()
    return updates


def resolve_stale(max_trading_days: int = STALE_WATCH_TRADING_DAYS,
                  as_of: str = None) -> list[dict]:
    """
    Expires WATCHING setups that never triggered.

    They are not trades: `net_pnl_pct` stays NULL on purpose so they can never
    enter a win rate or expectancy. Without this a setup issued weeks ago keeps
    showing up in /active with support and stop levels that no longer mean anything.
    """
    as_of = as_of or datetime.date.today().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expired_rows = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM signals WHERE status = 'WATCHING' AND triggered_date IS NULL AND resolved_date IS NULL"
        )
        for sig in cursor.fetchall():
            age = trading_days_between(sig["created_date"], as_of)
            if age <= max_trading_days:
                continue
            cursor.execute(
                """
                UPDATE signals
                SET status = 'EXPIRED', resolved_date = ?, closed_reason = 'EXPIRED', notes = ?
                WHERE id = ?
                """,
                (as_of, f"Never triggered within {age} trading days (limit {max_trading_days})", sig["id"]),
            )
            cursor.execute(
                "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                (sig["id"], now_ts, "EXPIRED", f"Setup expired after {age} trading days without a trigger."),
            )
            expired_rows.append({"symbol": sig["symbol"], "age_days": age, "strategy": sig["strategy"]})

        conn.commit()
    return expired_rows


def backfill_benchmark_returns(df_bench=None) -> dict:
    """
    Fills benchmark_pnl_pct / excess_pnl_pct for settled trades that lack them.

    Excess return is the only honest score in a rising market: +4% means little if
    the index did +7% over the same window. Pass a benchmark close frame, or let
    this fetch KSE-100.
    """
    if df_bench is None:
        try:
            from data_engine import fetch_kse100_index

            df_bench, _meta = fetch_kse100_index()
        except Exception as exc:  # a feed outage must never break the daily scan
            return {"updated": 0, "skipped": 0, "error": f"benchmark unavailable: {exc}"}
    if _benchmark_series(df_bench) is None:
        return {"updated": 0, "skipped": 0, "error": "benchmark frame empty or missing Close"}

    updated = 0
    skipped = 0
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM signals
            WHERE net_pnl_pct IS NOT NULL
              AND resolved_date IS NOT NULL
              AND (benchmark_pnl_pct IS NULL OR excess_pnl_pct IS NULL)
            """
        )
        for sig in cursor.fetchall():
            bench_pct = compute_benchmark_return(
                df_bench, sig["triggered_date"] or sig["created_date"], sig["resolved_date"]
            )
            if bench_pct is None:
                skipped += 1
                continue
            cursor.execute(
                "UPDATE signals SET benchmark_pnl_pct = ?, excess_pnl_pct = ? WHERE id = ?",
                (bench_pct, round(float(sig["net_pnl_pct"]) - bench_pct, 2), sig["id"]),
            )
            updated += 1
        conn.commit()

    return {"updated": updated, "skipped": skipped, "candidates": updated + skipped}
