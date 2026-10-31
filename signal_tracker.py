"""
signal_tracker.py
Persistent Signal Ledger (SQLite) & Lifecycle Tracker.
Tracks every setup from creation to resolution, maintains trade history,
and records explicit updates without rewriting history.
"""

import os
import sqlite3
import datetime
import pandas as pd

from swing_config import STATE_RANK

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
                signal_version, state, score, grade, entry_trigger, trigger_condition
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        cursor.execute(
            "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
            (sig_id, now_ts, "CREATED", f"Setup created ({new_state}): {setup.get('trigger_note')}"),
        )
        conn.commit()
        return True, f"New setup recorded for ${symbol} ({new_state})."


def audit_active_signals(market_data: dict, broker_fee_pct: float = 0.35) -> list[dict]:
    """
    Checks all open setups against the latest market session candle.
    Updates lifecycle states and returns a list of update messages to send.
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM signals WHERE status IN ('WATCHING', 'TRIGGERED')")
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

            # If setup was only WATCHING, check if invalidated by closing below stop
            if sig["status"] == "WATCHING":
                if close < sl:
                    cursor.execute(
                        "UPDATE signals SET status = 'INVALIDATED', resolved_date = ? WHERE id = ?",
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
                        SET status = 'STOPPED_OUT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?, notes = ?
                        WHERE id = ?
                        """,
                        (today_str, sl, round(gross * 100, 2), round(net * 100, 2), "Ambiguous bar (touched both TP1 and SL)", sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "STOP_HIT", "Stopped out on ambiguous bar."),
                    )
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
                        SET status = 'STOPPED_OUT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?
                        WHERE id = ?
                        """,
                        (today_str, sl, round(gross * 100, 2), round(net * 100, 2), sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "STOP_HIT", f"Stop loss hit at {sl:.2f}."),
                    )
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
                    gross = 0.5 * p1_ret + 0.5 * p2_ret
                    net = gross - (broker_fee_pct / 100.0)
                    cursor.execute(
                        """
                        UPDATE signals 
                        SET status = 'TP2_HIT', resolved_date = ?, exit_price = ?, gross_pnl_pct = ?, net_pnl_pct = ?
                        WHERE id = ?
                        """,
                        (today_str, tp2, round(gross * 100, 2), round(net * 100, 2), sig["id"]),
                    )
                    cursor.execute(
                        "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                        (sig["id"], now_ts, "TP2_HIT", f"Full target 2 reached at {tp2:.2f}."),
                    )
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
                        SET status = 'TP1_HIT', stop_loss = ?, notes = 'TP1 hit, SL moved to breakeven'
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
            active = len(df_subset[df_subset["status"].isin(["WATCHING", "TRIGGERED"])]) if total > 0 else 0
            closed = df_subset[df_subset["status"].isin(["TP1_HIT", "TP2_HIT", "STOPPED_OUT", "EXPIRED"])] if total > 0 else pd.DataFrame()
            if closed.empty:
                return {
                    "total_recorded": total,
                    "active_count": active,
                    "closed_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate_pct": 0.0,
                    "net_pnl_pct": 0.0,
                    "avg_trade_pnl": 0.0,
                    "message": f"{total} setups recorded ({active} active, 0 closed yet).",
                }
            wins = len(closed[closed["net_pnl_pct"] > 0])
            win_rate = round((wins / len(closed)) * 100, 1)
            total_pnl = round(float(closed["net_pnl_pct"].sum()), 2)
            avg_pnl = round(float(closed["net_pnl_pct"].mean()), 2)
            return {
                "total_recorded": total,
                "active_count": active,
                "closed_count": len(closed),
                "wins": wins,
                "losses": len(closed) - wins,
                "win_rate_pct": win_rate,
                "net_pnl_pct": total_pnl,
                "avg_trade_pnl": avg_pnl,
                "ledger_df": closed,
            }

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
