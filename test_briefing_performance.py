import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import briefing_performance as p
from daily_briefing import Journal


SETUP = dict(symbol='OGDC', entry_min=100, entry_max=102,
             stop_loss=97, tp1=108, tp2=114, rr_tp1=1.5)


class PerformanceTests(unittest.TestCase):
    def test_next_open_band_and_conservative_intraday_tie(self):
        bar = dict(session='2026-10-08', Open=101, High=109, Low=96,
                   Close=104, Volume=100000)
        outcome = p.simulate_outcome(SETUP, [bar], expected_entry_session='2026-10-08')
        self.assertEqual(outcome['status'], 'stopped')
        self.assertEqual(outcome['net_pct'], round(100*(97/101-1)-0.35, 3))
        self.assertEqual(p.simulate_outcome(SETUP, [dict(bar, Open=103)])['status'],
                         'skipped_open_outside_band')
        self.assertEqual(p.simulate_outcome(SETUP, [bar],
                         expected_entry_session='2026-10-09')['status'],
                         'missing_entry_bar')

    def test_forward_scorecard_only_counts_sent_plan_and_does_not_rewrite_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(Path(folder)/'journal.sqlite3')
            snapshot = dict(session='2026-10-07', for_session='2026-10-08',
                            ready=True, candidates=[SETUP])
            journal.save(snapshot)
            journal.db.execute("INSERT INTO deliveries VALUES ('2026-10-07:plan','retry',NULL,'x')")
            journal.db.commit()
            summary = dict(date='2026-10-08', advancing=200, declining=100,
                           index_close=168000, rows={'OGDC':dict(Open=101, High=109,
                               Low=100, Close=107, Volume=100000)})
            p.record_completed_session(journal, summary, ['OGDC'])
            self.assertEqual(p.forward_scorecard(journal), [])
            journal.db.execute("UPDATE deliveries SET status='sent' WHERE key='2026-10-07:plan'")
            journal.db.commit()
            first = p.forward_scorecard(journal)
            self.assertEqual(first[0]['status'], 'target1')
            text = p.report(first, 'Forward', delivered_plans=2)
            self.assertIn('plans with no breakout: 1', text)
            self.assertIn('2026-10-07 OGDC:', text)
            changed = dict(summary, rows={'OGDC':dict(Open=101, High=109,
                Low=90, Close=95, Volume=100000)})
            p.record_completed_session(journal, changed, ['OGDC'])
            self.assertEqual(p.forward_scorecard(journal), first)
            journal.db.close()

    def test_historical_replay_uses_live_candidate_function_and_next_market_day(self):
        dates = pd.bdate_range('2026-07-01', periods=62)
        frame = pd.DataFrame(dict(Open=100., High=102., Low=98.,
                                  Close=101., Volume=100000), index=dates)
        source = dates[-2].date()
        target = dates[-1].date()
        breadth = {source:dict(advancing=200,declining=100),
                   target:dict(advancing=180,declining=120)}
        with patch.object(p, 'evaluate_candidate', return_value=SETUP) as live_rule:
            rows = p.historical_replay({'OGDC':frame}, breadth,
                                       dict(symbols=['OGDC'], min_coverage=1., top_n=3))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_session'], source.isoformat())
        self.assertEqual(rows[0]['entry_session'], target.isoformat())
        self.assertTrue(live_rule.called)


if __name__ == '__main__':
    unittest.main()
