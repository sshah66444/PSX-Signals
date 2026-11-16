"""
test_ledger_integrity.py
Stage-0 ledger integrity tests: schema migration, TP1 runner resolution, partial
finalisation, stale expiry, benchmark/excess attribution, and the split between
"signals issued" and "trades settled".

Every test runs against an isolated temporary database -- never the live signals.db.
"""

import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

import attribution
import signal_tracker

TODAY = datetime.date.today().strftime("%Y-%m-%d")


def days_ago(weekdays: int) -> str:
    """ISO date `weekdays` business days before today."""
    return (pd.Timestamp(TODAY) - pd.offsets.BDay(weekdays)).strftime("%Y-%m-%d")


def candle(high, low, close=None):
    return pd.DataFrame([{
        "Date": pd.to_datetime(TODAY),
        "Open": low,
        "High": high,
        "Low": low,
        "Close": close if close is not None else (high + low) / 2.0,
        "Volume": 1_000_000,
    }]).set_index("Date")


class LedgerIntegrityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = signal_tracker.DB_PATH
        signal_tracker.DB_PATH = os.path.join(self._tmp.name, "signals_test.db")
        signal_tracker.init_db()

    def tearDown(self):
        signal_tracker.DB_PATH = self._orig_db
        self._tmp.cleanup()

    def insert(self, **overrides):
        row = dict(
            id=f"TEST-{uuid.uuid4().hex[:8]}",
            symbol="TEST",
            strategy="PULLBACK",
            track="swing",
            status="WATCHING",
            created_date=TODAY,
            triggered_date=None,
            entry_min=100.0,
            entry_max=102.0,
            stop_loss=96.0,
            tp1=108.0,
            tp2=114.0,
            resolved_date=None,
            exit_price=None,
            gross_pnl_pct=None,
            net_pnl_pct=None,
            notes="",
            signal_version="2.0",
            state="WATCH",
            score=70,
            grade="B",
            entry_trigger=102.0,
            trigger_condition="",
            initial_stop_loss=96.0,
        )
        row.update(overrides)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        with signal_tracker.get_connection() as conn:
            conn.execute(f"INSERT INTO signals ({columns}) VALUES ({placeholders})", tuple(row.values()))
            conn.commit()
        return row["id"]

    def fetch(self, row_id):
        with signal_tracker.get_connection() as conn:
            row = conn.execute("SELECT * FROM signals WHERE id = ?", (row_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def market(high, low, close=None, symbol="TEST"):
        return {symbol: {"status": "OK", "df": candle(high, low, close)}}

    def test_schema_migration_adds_integrity_columns(self):
        with signal_tracker.get_connection() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
        for expected in ("benchmark_pnl_pct", "excess_pnl_pct", "closed_reason", "initial_stop_loss"):
            self.assertIn(expected, columns, f"missing Stage-0 column {expected}")

    def test_migration_upgrades_legacy_table_without_losing_rows(self):
        legacy = os.path.join(self._tmp.name, "legacy.db")
        conn = sqlite3.connect(legacy)
        conn.execute(
            """CREATE TABLE signals (
                   id TEXT PRIMARY KEY, symbol TEXT, strategy TEXT, status TEXT,
                   created_date TEXT, entry_min REAL, entry_max REAL, stop_loss REAL,
                   tp1 REAL, tp2 REAL, resolved_date TEXT, exit_price REAL,
                   gross_pnl_pct REAL, net_pnl_pct REAL, notes TEXT)"""
        )
        conn.execute(
            "INSERT INTO signals (id, symbol, strategy, status, created_date) "
            "VALUES ('OLD1','OGDC','PULLBACK','WATCHING','2026-09-16')"
        )
        conn.commit()
        conn.close()

        signal_tracker.DB_PATH = legacy
        signal_tracker.init_db()
        with signal_tracker.get_connection() as upgraded:
            columns = {row[1] for row in upgraded.execute("PRAGMA table_info(signals)")}
            row = upgraded.execute("SELECT * FROM signals WHERE id = 'OLD1'").fetchone()
        self.assertIn("benchmark_pnl_pct", columns)
        self.assertIn("initial_stop_loss", columns)
        self.assertEqual(row["symbol"], "OGDC", "legacy rows must survive the migration")

    def test_tp1_runner_resolves_at_breakeven(self):
        row_id = self.insert(
            status="TRIGGERED", triggered_date=days_ago(4), state="BUY_NOW",
            stop_loss=90.0, initial_stop_loss=90.0, entry_max=100.0, tp1=110.0, tp2=120.0,
        )
        # TP1 touched, stop not hit: half booked, stop moves to breakeven.
        signal_tracker.audit_active_signals(self.market(high=111.0, low=99.0))
        after_tp1 = self.fetch(row_id)
        self.assertEqual(after_tp1["status"], "TP1_HIT")
        self.assertAlmostEqual(after_tp1["stop_loss"], 100.0, places=2)
        self.assertAlmostEqual(after_tp1["initial_stop_loss"], 90.0, places=2,
                               msg="original stop must survive the breakeven move")
        self.assertIsNone(after_tp1["resolved_date"], "TP1 alone must not close the trade")

        # Runner then retraces into the breakeven stop.
        signal_tracker.audit_active_signals(self.market(high=103.0, low=99.5))
        final = self.fetch(row_id)
        self.assertEqual(final["status"], "TP1_BE")
        self.assertEqual(final["closed_reason"], "TP1_BE")
        self.assertIsNotNone(final["resolved_date"])
        # 0.5 x +10% (TP1) + 0.5 x 0% (breakeven) - 0.35% fees = +4.65%
        self.assertAlmostEqual(final["net_pnl_pct"], 4.65, places=2)

    def test_tp1_runner_resolves_at_tp2(self):
        row_id = self.insert(
            status="TP1_HIT", triggered_date=days_ago(5), stop_loss=100.0,
            initial_stop_loss=90.0, entry_max=100.0, tp1=110.0, tp2=120.0,
        )
        signal_tracker.audit_active_signals(self.market(high=121.0, low=101.0))
        final = self.fetch(row_id)
        self.assertEqual(final["status"], "TP2_HIT")
        self.assertEqual(final["closed_reason"], "TP2")
        # 0.5 x +10% + 0.5 x +20% - 0.35% fees = +14.65%
        self.assertAlmostEqual(final["net_pnl_pct"], 14.65, places=2)

    def test_finalize_partials_marks_runner_and_refuses_without_price(self):
        row_id = self.insert(
            status="TP1_HIT", triggered_date=days_ago(20), created_date=days_ago(25),
            stop_loss=100.0, initial_stop_loss=90.0, entry_max=100.0, tp1=110.0, tp2=120.0,
        )
        # No verified candle for the symbol: the row must stay open, not be guessed.
        skipped = signal_tracker.finalize_partials(market_data={})
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["type"], "PARTIAL_SKIPPED")
        self.assertIsNone(self.fetch(row_id)["net_pnl_pct"])

        # With a verified close: 0.5 x +10% + 0.5 x +5% - 0.35% = +7.15%
        updates = signal_tracker.finalize_partials(
            market_data=self.market(high=106.0, low=104.0, close=105.0)
        )
        self.assertEqual(updates[0]["type"], "PARTIAL_CLOSED")
        final = self.fetch(row_id)
        self.assertEqual(final["status"], "PARTIAL_CLOSED")
        self.assertEqual(final["closed_reason"], "PARTIAL_MARK")
        self.assertAlmostEqual(final["net_pnl_pct"], 7.15, places=2)
        self.assertAlmostEqual(final["exit_price"], 105.0, places=2)

    def test_finalize_partials_respects_max_age(self):
        self.insert(status="TP1_HIT", triggered_date=days_ago(2), stop_loss=100.0,
                    entry_max=100.0, tp1=110.0, tp2=120.0)
        updates = signal_tracker.finalize_partials(
            market_data=self.market(high=106.0, low=104.0, close=105.0), max_age_trading_days=10
        )
        self.assertEqual(updates, [], "a young runner must be left to resolve on its own")

    def test_resolve_stale_expires_only_old_untouched_setups(self):
        old_id = self.insert(status="WATCHING", created_date=days_ago(20), state="BUY_ZONE")
        fresh_id = self.insert(status="WATCHING", created_date=TODAY, state="WATCH")
        entered_id = self.insert(status="TRIGGERED", created_date=days_ago(20),
                                 triggered_date=days_ago(18), state="BUY_NOW")

        expired = signal_tracker.resolve_stale()
        self.assertEqual(len(expired), 1, f"only the stale WATCHING row may expire, got {expired}")

        old_row = self.fetch(old_id)
        self.assertEqual(old_row["status"], "EXPIRED")
        self.assertEqual(old_row["closed_reason"], "EXPIRED")
        self.assertIsNone(old_row["net_pnl_pct"], "expired setups were never trades: no P&L")
        self.assertEqual(self.fetch(fresh_id)["status"], "WATCHING")
        self.assertEqual(self.fetch(entered_id)["status"], "TRIGGERED")

    def test_benchmark_return_and_excess_backfill(self):
        bench = pd.DataFrame(
            {"Close": [100.0, 105.0, 110.0]},
            index=pd.to_datetime(["2026-01-05", "2026-01-12", "2026-01-19"]),
        )
        self.assertAlmostEqual(
            signal_tracker.compute_benchmark_return(bench, "2026-01-05", "2026-01-19"), 10.0, places=2
        )
        # A benchmark that stops long before the exit date must not be used.
        self.assertIsNone(signal_tracker.compute_benchmark_return(bench, "2026-01-05", "2026-03-02"))

        row_id = self.insert(
            status="STOPPED_OUT", triggered_date="2026-01-05", resolved_date="2026-01-19",
            net_pnl_pct=-3.08, gross_pnl_pct=-2.73, exit_price=96.0,
        )
        result = signal_tracker.backfill_benchmark_returns(bench)
        self.assertEqual(result["updated"], 1)
        final = self.fetch(row_id)
        self.assertAlmostEqual(final["benchmark_pnl_pct"], 10.0, places=2)
        self.assertAlmostEqual(final["excess_pnl_pct"], -13.08, places=2,
                               msg="excess = net P&L minus the benchmark over the same window")

    def test_performance_summary_excludes_signals_that_never_traded(self):
        for _ in range(3):
            self.insert(status="WATCHING")                       # active, no P&L
        for _ in range(2):
            self.insert(status="EXPIRED", resolved_date=TODAY)   # never entered, no P&L
        self.insert(status="STOPPED_OUT", triggered_date=days_ago(10),
                    resolved_date=TODAY, net_pnl_pct=-3.08)
        self.insert(status="TP2_HIT", triggered_date=days_ago(20),
                    resolved_date=TODAY, net_pnl_pct=5.95)

        summary = signal_tracker.get_performance_summary()
        self.assertEqual(summary["closed_count"], 2, "only settled trades count")
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["win_rate_pct"], 50.0)
        self.assertEqual(summary["expired_count"], 2)
        self.assertEqual(summary["entered_count"], 2)
        self.assertAlmostEqual(summary["net_pnl_pct"], 2.87, places=2)

    def test_attribution_separates_signals_from_settled_trades(self):
        for _ in range(3):
            self.insert(status="WATCHING")
        for _ in range(2):
            self.insert(status="EXPIRED", resolved_date=TODAY)
        self.insert(status="STOPPED_OUT", triggered_date=days_ago(10),
                    resolved_date=TODAY, net_pnl_pct=-3.08)
        self.insert(status="TP2_HIT", triggered_date=days_ago(20),
                    resolved_date=TODAY, net_pnl_pct=5.95)

        stats = attribution.build_attribution(attribution.load_ledger())
        self.assertEqual(stats["counts"]["signals"], 7)
        self.assertEqual(stats["counts"]["settled"], 2)
        self.assertEqual(stats["counts"]["expired"], 2)
        self.assertEqual(stats["overall"]["n"], 2)
        self.assertAlmostEqual(stats["overall"]["expectancy_pct"], 1.435, places=1)
        self.assertFalse(stats["sample_ok"], "2 trades is not a sample")
        self.assertTrue(any("settled trade" in w for w in stats["warnings"]))
        self.assertTrue(all(b["insufficient"] for b in stats["buckets"]["strategy"]))

        text = attribution.format_attribution_text(stats)
        self.assertIn("Ledger Attribution", text)
        self.assertIn("n&lt;30", text, "HTML output must escape the less-than sign")

    def test_attribution_handles_empty_ledger(self):
        stats = attribution.build_attribution([])
        self.assertEqual(stats["counts"]["signals"], 0)
        self.assertTrue(stats["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
