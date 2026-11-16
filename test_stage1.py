"""
test_stage1.py
Stage-1 tests: the live exit profile (time stop, trailing runner, holding cap) and
the walk-forward / matched random-entry null model.

Uses an isolated temporary ledger and synthetic OHLC -- no network, no live data.
"""

import os
import sys
import tempfile
import unittest
import uuid

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

import signal_tracker
import walkforward
from swing_config import get_config

TODAY = pd.Timestamp.today().normalize()


def series(closes, highs=None, lows=None):
    """Business-day OHLC frame ending today."""
    closes = [float(c) for c in closes]
    highs = [float(h) for h in (highs if highs is not None else [c * 1.01 for c in closes])]
    lows = [float(l) for l in (lows if lows is not None else [c * 0.99 for c in closes])]
    index = pd.bdate_range(end=TODAY, periods=len(closes))
    return pd.DataFrame(
        {"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": 1_000_000.0},
        index=index,
    )


class Stage1ExitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = signal_tracker.DB_PATH
        signal_tracker.DB_PATH = os.path.join(self._tmp.name, "stage1.db")
        signal_tracker.init_db()

    def tearDown(self):
        signal_tracker.DB_PATH = self._orig_db
        self._tmp.cleanup()

    def insert(self, **overrides):
        row = dict(
            id=f"T1-{uuid.uuid4().hex[:8]}", symbol="TEST", strategy="PULLBACK",
            track="swing", status="TRIGGERED", created_date=TODAY.strftime("%Y-%m-%d"),
            triggered_date=TODAY.strftime("%Y-%m-%d"), entry_min=100.0, entry_max=100.0,
            stop_loss=90.0, tp1=115.0, tp2=145.0, resolved_date=None, exit_price=None,
            gross_pnl_pct=None, net_pnl_pct=None, notes="", signal_version="2.0",
            state="BUY_NOW", score=80, grade="A", entry_trigger=100.0,
            trigger_condition="", initial_stop_loss=90.0,
        )
        row.update(overrides)
        columns = ", ".join(row)
        with signal_tracker.get_connection() as conn:
            conn.execute(
                f"INSERT INTO signals ({columns}) VALUES ({', '.join('?' for _ in row)})",
                tuple(row.values()),
            )
            conn.commit()
        return row["id"]

    def fetch(self, row_id):
        with signal_tracker.get_connection() as conn:
            return dict(conn.execute("SELECT * FROM signals WHERE id = ?", (row_id,)).fetchone())

    def market(self, frame, symbol="TEST"):
        return {symbol: {"status": "OK", "df": frame}}

    def test_time_stop_closes_a_flat_trade(self):
        frame = series([100.0] * 21, highs=[101.0] * 21, lows=[99.0] * 21)
        row_id = self.insert(triggered_date=frame.index[-21].strftime("%Y-%m-%d"))

        updates = signal_tracker.audit_active_signals(self.market(frame))
        final = self.fetch(row_id)
        self.assertEqual(final["status"], "TIME_STOP")
        self.assertEqual(final["closed_reason"], "TIME_STOP")
        self.assertEqual([u["type"] for u in updates], ["TIME_STOP"])
        self.assertAlmostEqual(final["net_pnl_pct"], -0.35, places=2)

    def test_time_stop_spares_a_trade_that_ran(self):
        # 0.8R of favourable excursion is progress: the time stop must not fire.
        frame = series([100.0] * 21, highs=[108.0] * 21, lows=[99.0] * 21)
        row_id = self.insert(triggered_date=frame.index[-21].strftime("%Y-%m-%d"))

        signal_tracker.audit_active_signals(self.market(frame))
        self.assertEqual(self.fetch(row_id)["status"], "TRIGGERED")

    def test_trailing_stop_closes_a_runner_above_breakeven(self):
        closes = [100.0 + i for i in range(40)] + [118.0]
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.99 for c in closes]
        frame = series(closes, highs=highs, lows=lows)
        row_id = self.insert(
            status="TP1_HIT", stop_loss=100.0, initial_stop_loss=90.0, tp1=110.0, tp2=1000.0,
            triggered_date=frame.index[-21].strftime("%Y-%m-%d"),
        )

        updates = signal_tracker.audit_active_signals(self.market(frame))
        final = self.fetch(row_id)
        self.assertEqual(final["status"], "TRAIL_STOP")
        self.assertEqual(final["closed_reason"], "TRAIL_STOP")
        self.assertGreater(final["exit_price"], 100.0, "trail must sit above breakeven")
        self.assertGreater(final["net_pnl_pct"], 0.0)
        self.assertEqual([u["type"] for u in updates], ["TRAIL_STOP"])

    def test_swing_tp1_is_a_1_5r_scale_out(self):
        cfg = get_config("swing_trade")
        self.assertEqual(cfg["tp1_risk_multiplier"], 1.5)
        self.assertEqual(cfg["tp1_atr_multiplier"], 1.5,
                         "both TP1 legs must move: the engine takes max(risk, atr)")
        self.assertGreaterEqual(cfg["tp1_risk_multiplier"], cfg["min_rr_tp1"] - 1e-9)


class Stage1SimulatorTest(unittest.TestCase):
    def test_tp1_then_tp2_blends_the_partial(self):
        frame = series([100.0, 115.0, 140.0], highs=[101.0, 116.0, 141.0], lows=[99.0, 99.0, 114.0])
        result = walkforward.simulate_exit(frame, 0, 100.0, 90.0, 115.0, 130.0, get_config("swing_trade"))
        self.assertEqual(result["reason"], "TP2")
        # 0.5 x +15% + 0.5 x +30% - 0.35% fees
        self.assertAlmostEqual(result["net_pct"], 22.15, places=2)

    def test_time_stop_reason_and_cost(self):
        frame = series([100.0] * 25, highs=[101.0] * 25, lows=[99.0] * 25)
        result = walkforward.simulate_exit(frame, 0, 100.0, 90.0, 115.0, 130.0,
                                           get_config("swing_trade"))
        self.assertEqual(result["reason"], "TIME_STOP")
        self.assertAlmostEqual(result["net_pct"], -0.35, places=2)

    def test_runner_exits_on_trailing_ema_not_tp2(self):
        closes = [100.0 + i for i in range(40)] + [118.0]
        frame = series(closes, highs=[c * 1.005 for c in closes], lows=[c * 0.99 for c in closes])
        ema = frame["Close"].ewm(span=50, adjust=False).mean()
        result = walkforward.simulate_exit(frame, 0, 100.0, 90.0, 110.0, 1000.0,
                                           get_config("swing_trade"), ema=ema)
        self.assertEqual(result["reason"], "TRAIL_STOP")
        self.assertGreater(result["exit_price"], 100.0, "the trail must protect profit")

    def test_invalid_geometry_is_rejected(self):
        frame = series([100.0] * 10)
        self.assertIsNone(
            walkforward.simulate_exit(frame, 0, 100.0, 100.0, 110.0, 120.0, get_config("swing_trade"))
        )


class WalkForwardTest(unittest.TestCase):
    def test_fold_bounds_are_contiguous_and_cover_the_range(self):
        bounds = walkforward.fold_bounds("2025-01-01", "2025-12-31", 3)
        self.assertEqual(len(bounds), 3)
        self.assertEqual(bounds[0][0], pd.Timestamp("2025-01-01"))
        self.assertEqual(bounds[-1][1], pd.Timestamp("2025-12-31"))
        for (_, high), (low, _) in zip(bounds, bounds[1:]):
            self.assertEqual(high, low)
        self.assertEqual(walkforward.fold_of("2025-01-01", bounds), 1)
        self.assertEqual(walkforward.fold_of("2025-12-31", bounds), 3)

    def test_null_model_is_seeded_and_keeps_the_risk_geometry(self):
        closes = [100.0 + (i % 7) for i in range(120)]
        frame = series(closes, highs=[c * 1.02 for c in closes], lows=[c * 0.98 for c in closes])
        trades = [{
            "symbol": "TEST", "entry_date": frame.index[80], "entry_price": 100.0,
            "stop_loss": 90.0, "tp1": 115.0, "tp2": 145.0,
        } for _ in range(5)]
        bounds = walkforward.fold_bounds(frame.index[60], frame.index[-3], 1)

        first = walkforward.matched_random_trades(trades, {"TEST": frame}, bounds=bounds,
                                                  rng=__import__("numpy").random.default_rng(11))
        second = walkforward.matched_random_trades(trades, {"TEST": frame}, bounds=bounds,
                                                   rng=__import__("numpy").random.default_rng(11))
        self.assertIsNotNone(first)
        self.assertEqual(len(first), 5)
        self.assertEqual([t["net_pct"] for t in first], [t["net_pct"] for t in second],
                         "a seeded null model must be reproducible")
        for trade in first:
            self.assertGreater(trade["entry_price"] - trade["stop_loss"], 0)

    def test_walk_forward_produces_an_internally_consistent_report(self):
        import numpy as np

        rng = np.random.default_rng(3)
        closes = list(100 * np.cumprod(1 + rng.normal(0.0008, 0.02, 240)))
        frame = series(closes, highs=[c * 1.015 for c in closes], lows=[c * 0.985 for c in closes])

        report = walkforward.walk_forward(frames={"TEST": frame}, mode="swing_trade",
                                          folds=2, iterations=10, seed=5)
        self.assertNotIn("error", report)
        self.assertEqual(report["overall"]["n"], len(report["trades"]))
        self.assertEqual(len(report["by_fold"]), 2)
        self.assertTrue(report["verdict"])
        self.assertIn("VERDICT", walkforward.format_report_text(report))

    def test_empty_frames_reports_an_error(self):
        report = walkforward.walk_forward(frames={}, mode="swing_trade", folds=2, iterations=5)
        self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)

