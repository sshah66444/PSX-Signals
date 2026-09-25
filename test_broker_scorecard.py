import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import broker_scorecard as b


class ScorecardTests(unittest.TestCase):
    def test_weekly_calls_and_overlapping_symbols(self):
        calls=json.loads((b.ROOT/'broker_calls.json').read_text())
        rows=b.score_all(calls,b.read_prices(b.ROOT/'broker_prices.csv'))
        self.assertEqual(len(rows),6)
        self.assertEqual(sum(r['status'].startswith('target') for r in rows),4)
        self.assertEqual(sum(r['status'].startswith('stopped') for r in rows),2)
        self.assertEqual(rows[1]['status'],'target_gap')
        self.assertAlmostEqual(rows[1]['exit'],202.25)
        self.assertIn('Repeated symbols',b.render(rows))

    def test_same_day_target_and_stop_uses_stop_first(self):
        call=dict(id='c',report_date='2026-09-21',symbol='X',stop=90,target1=110,target2=120)
        bar=dict(date=date(2026,9,21),open=100,high=111,low=89,close=101)
        result=b.score_call(call,{'X':[bar]})
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(result['exit'],90)

    def test_unresolved_call_is_not_counted_as_win(self):
        call=dict(id='c',report_date='2026-09-21',symbol='X',stop=90,target1=110,target2=120)
        bar=dict(date=date(2026,9,21),open=100,high=105,low=95,close=101)
        result=b.score_call(call,{'X':[bar]})
        self.assertEqual(result['status'],'open')
        self.assertNotIn('return_pct',result)

    def test_missing_entry_day_is_not_invented(self):
        call=dict(id='c',report_date='2026-09-21',symbol='X',stop=90,target1=110,target2=120)
        bar=dict(date=date(2026,9,22),open=100,high=105,low=95,close=101)
        self.assertEqual(b.score_call(call,{'X':[bar]})['status'],'missing_entry_bar')

    def test_record_session_is_idempotent_and_uses_official_bar(self):
        calls=[dict(symbol='X')]
        summary=dict(rows={'X':dict(Open=100,High=102,Low=98,Close=101)})
        with tempfile.TemporaryDirectory() as folder, \
             patch('daily_briefing.market_summary',return_value=summary):
            prices=Path(folder)/'prices.csv'
            self.assertEqual(b.record_completed_session(calls,prices,date(2026,9,24)),1)
            self.assertEqual(b.record_completed_session(calls,prices,date(2026,9,24)),0)
            self.assertEqual(len(b.read_prices(prices)['X']),1)


if __name__=='__main__': unittest.main()
