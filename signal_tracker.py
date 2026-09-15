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

        if existing:
            # Check if status transitioned from WATCHING to TRIGGERED
            old_status = existing["status"]
            if old_status == "WATCHING" and status == "TRIGGERED":
                cursor.execute(
                    """
                    UPDATE signals 
                    SET status = 'TRIGGERED', triggered_date = ?, notes = ?
                    WHERE id = ?
                    """,
                    (today_str, setup.get("trigger_note", ""), existing["id"]),
                )
                cursor.execute(
                    "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
                    (existing["id"], now_ts, "TRIGGERED", f"Triggered: {setup.get('trigger_note')}"),
                )
                conn.commit()
                return True, f"Updated ${symbol} from WATCHING to TRIGGERED."
            return False, f"Setup for ${symbol} already active ({old_status}). Levels unchanged."

        # New setup detected
        sig_id = f"{symbol}-{datetime.datetime.now().strftime('%Y%m%d%H%M')}-{strategy[:3]}"
        triggered_date = today_str if status == "TRIGGERED" else None

        cursor.execute(
            """
            INSERT INTO signals (
                id, symbol, strategy, status, created_date, triggered_date,
                entry_min, entry_max, stop_loss, tp1, tp2, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sig_id,
                symbol,
                strategy,
                status,
                today_str,
                triggered_date,
                setup.get("entry_min"),
                setup.get("entry_max"),
                setup.get("stop_loss"),
                setup.get("tp1"),
                setup.get("tp2"),
                setup.get("trigger_note"),
            ),
        )
        cursor.execute(
            "INSERT INTO signal_events (signal_id, timestamp, event_type, details) VALUES (?, ?, ?, ?)",
            (sig_id, now_ts, "CREATED", f"Setup created ({status}): {setup.get('trigger_note')}"),
        )
        conn.commit()
        return True, f"New setup recorded for ${symbol} ({status})."


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


def get_active_signals() -> list[dict]:
    """Returns all currently active (WATCHING or TRIGGERED) setups."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM signals WHERE status IN ('WATCHING', 'TRIGGERED') ORDER BY created_date DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_performance_summary() -> dict:
    """
    Computes audited performance from real signals actually recorded in signals.db.
    Completely separated from historical simulations.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM signals")
        all_signals = cursor.fetchall()

        if not all_signals:
            return {
                "total_recorded": 0,
                "active_count": 0,
                "closed_count": 0,
                "win_rate_pct": 0.0,
                "net_pnl_pct": 0.0,
                "avg_trade_pnl": 0.0,
                "message": "No signals have been recorded in the live ledger yet.",
            }

        df = pd.DataFrame([dict(r) for r in all_signals])
        total = len(df)
        active = len(df[df["status"].isin(["WATCHING", "TRIGGERED"])])
        closed = df[df["status"].isin(["TP1_HIT", "TP2_HIT", "STOPPED_OUT", "EXPIRED"])]

        if closed.empty:
            return {
                "total_recorded": total,
                "active_count": active,
                "closed_count": 0,
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
