"""Offline tests for timing, provenance, preview isolation, and delivery."""
import unittest
from unittest.mock import patch
from datetime import datetime,date
import tempfile
from pathlib import Path
import pandas as pd
import daily_briefing as b

class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.c=b.load_config();self.tmp=tempfile.TemporaryDirectory()
        self.j=b.Journal(Path(self.tmp.name)/'journal.db')
        self.messages=[]
        self.sender=lambda text:self.messages.append(text) or 99
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def now(self,t):return datetime.fromisoformat('2026-09-22T'+t).replace(tzinfo=b.PKT)
    def snapshot(self,ready=True):
        return dict(session='2026-09-22',for_session='2026-09-23',generated_at=self.now('19:30').isoformat(),
                    source='Official PSX',valid=30 if ready else 0,total=30,ready=ready,
                    benchmark_ok=ready,regime='BULL_MARKET',candidates=[],errors={})
    def test_eight_pm_report_and_no_duplicate(self):
        self.j.save(self.snapshot())
        b.tick(self.c,self.j,self.sender,self.now('19:59'));self.assertFalse(self.messages)
        b.tick(self.c,self.j,self.sender,self.now('20:00'));self.assertEqual(len(self.messages),1)
        b.tick(self.c,self.j,self.sender,self.now('20:15'));self.assertEqual(len(self.messages),1)
        self.assertIn('2026-09-23',self.messages[0]);self.assertIn('2026-09-22',self.messages[0])
    def test_unavailable_notice_then_recovery_report(self):
        b.tick(self.c,self.j,self.sender,self.now('20:00'))
        b.tick(self.c,self.j,self.sender,self.now('20:05'))
        self.assertEqual(len(self.messages),1)
        self.j.save(self.snapshot())
        b.tick(self.c,self.j,self.sender,self.now('20:15'))
        self.assertEqual(len(self.messages),2)
    def test_late_job_never_sends_plan(self):
        self.j.save(self.snapshot())
        b.tick(self.c,self.j,self.sender,self.now('22:00'))
        self.assertNotIn('PSX PLAN',self.messages[0])
        self.assertIn('No late entry plan',self.messages[0])
    def test_old_snapshot_not_used(self):
        s=self.snapshot();s['session']='2026-09-21';self.j.save(s)
        b.tick(self.c,self.j,self.sender,self.now('20:00'))
        self.assertNotIn('PSX PLAN',self.messages[0])
    def test_holiday_and_weekend_dates(self):
        self.assertEqual(b.next_session(date(2026,11,6),self.c),date(2026,11,10))
        self.assertEqual(b.next_session(date(2026,9,25),self.c),date(2026,9,28))
        self.assertFalse(b.session_day(date(2026,12,25),self.c))
    def test_calendar_expiry_fails_closed(self):
        with self.assertRaises(ValueError):b.next_session(date(2026,12,31),self.c)
    def test_preparation_does_not_deliver(self):
        self.assertTrue(b.tick(self.c,self.j,self.sender,self.now('19:00')))
        self.assertFalse(self.messages)
    def test_retry_throttle(self):
        s=self.snapshot(False);s['generated_at']=self.now('19:40').isoformat();self.j.save(s)
        self.assertIsNone(b.tick(self.c,self.j,self.sender,self.now('19:45')))
        self.assertTrue(b.tick(self.c,self.j,self.sender,self.now('19:55')))
    def test_ambiguous_delivery_not_blindly_retried(self):
        def fail(text):raise RuntimeError('network uncertainty')
        with self.assertRaises(RuntimeError):self.j.deliver('key','plan',fail)
        self.assertFalse(self.j.deliver('key','plan',self.sender));self.assertFalse(self.messages)
    def test_explicit_rejection_can_retry(self):
        def fail(text):raise b.DeliveryRejected('rejected')
        with self.assertRaises(b.DeliveryRejected):self.j.deliver('key','plan',fail)
        self.assertTrue(self.j.deliver('key','plan',self.sender))
    def test_state_survives_restart(self):
        self.j.deliver('key','plan',self.sender)
        reopened=b.Journal(self.j.path)
        self.assertFalse(reopened.deliver('key','plan',self.sender));reopened.db.close()
        self.assertEqual(len(self.messages),1)
    def fixture(self):
        index=pd.bdate_range(end='2026-09-22',periods=70)
        return pd.DataFrame(dict(Open=100.,High=102.,Low=98.,Close=101.,Volume=1000000),index=index)
    def test_stale_and_future_prices_rejected(self):
        d=self.fixture()
        b.verified_bars(d,date(2026,9,22))
        for expected in (date(2026,9,21),date(2026,9,23)):
            with self.assertRaises(ValueError):b.verified_bars(d,expected)
    def test_bad_candles_rejected(self):
        d=self.fixture();d.iloc[-1,d.columns.get_loc('High')]=90
        with self.assertRaises(ValueError):b.verified_bars(d,date(2026,9,22))
    def summary(self):
        return dict(date='2026-09-22',rows={'OGDC':{}},index_close=170000,
                    advancing=200,declining=100,asof='2026-09-22T19:15:00')
    def test_missing_completed_summary_withholds_candidates(self):
        c=dict(self.c,symbols=['OGDC'])
        with patch.object(b,'market_summary',side_effect=ValueError('summary unavailable')):
            with self.assertRaisesRegex(ValueError,'summary unavailable'):
                b.build_snapshot(c,self.now('19:30'))
    def test_shortlist_and_plain_dates(self):
        c=dict(self.c,symbols=['OGDC'])
        fixture=self.fixture()
        setup=dict(symbol='OGDC',strategy='BREAKOUT',status='TRIGGERED',entry_min=101.,entry_max=102.,
                   stop_loss=98.,tp1=108.,tp2=112.,rr_tp1=1.5,price=101.,failures=[],reason='Trend and volume passed')
        with patch.object(b,'market_summary',return_value=self.summary()),patch.object(b,'get_stock',return_value=(fixture,'PSX + Yahoo')),patch.object(b,'evaluate_candidate',return_value=setup):
            snap=b.build_snapshot(c,self.now('19:30'))
        self.assertTrue(snap['ready']);self.assertEqual(len(snap['candidates']),1)
        text=b.render(snap);self.assertIn('Do not chase'.lower(),text.lower());self.assertLess(len(text),3500)
    def test_preview_build_does_not_import_trade_ledger(self):
        import sys
        self.assertNotIn('signal_tracker',sys.modules)
    def test_zero_candidates_still_produces_daily_report(self):
        self.assertIn('NO QUALIFYING BREAKOUTS',b.render(self.snapshot()))
    def test_dip_watch_is_not_rendered_as_entry(self):
        s=self.snapshot()
        s['dip_watches']=[dict(symbol='PPL',price=230,support=225,confirmation=233,invalidation=222)]
        text=b.render(s)
        self.assertIn('observation only',text)
        self.assertIn('not a buy signal',text)
        self.assertNotIn('Entry band:',text)
    def test_dip_watch_requires_confirmation_inputs(self):
        d=self.fixture()
        self.assertIsNone(b.evaluate_dip_watch('OGDC',d))
    def test_dip_watch_rule_and_bearish_rejection(self):
        d=self.fixture()
        d.loc[d.index[-2],'Close']=100
        d.loc[d.index[-1],['Open','High','Low','Close','Volume']]=[100,102,99,101,200000]
        d.loc[d.index[-20],'High']=110
        d['ATR']=3.;d['EMA_20']=99.;d['EMA_50']=98.
        d['Vol_MA20']=150000.;d['RSI']=48.;d['MACD_Hist']=.2
        d.loc[d.index[-2],['RSI','MACD_Hist']]=[45.,.1]
        with patch.object(b,'compute_all_indicators',return_value=d):
            watch=b.evaluate_dip_watch('OGDC',d)
            self.assertEqual(watch['symbol'],'OGDC')
            self.assertLess(watch['invalidation'],watch['support'])
            d.loc[d.index[-1],'RSI']=40
            self.assertIsNone(b.evaluate_dip_watch('OGDC',d))
    def test_low_coverage_withholds_dip_watch(self):
        c=dict(self.c,symbols=['OGDC','PPL'],min_coverage=1.)
        with patch.object(b,'market_summary',return_value=self.summary()), \
             patch.object(b,'get_stock',side_effect=[(self.fixture(),'PSX + Yahoo'),ValueError('missing')]), \
             patch.object(b,'evaluate_candidate',return_value=None), \
             patch.object(b,'evaluate_dip_watch',return_value=dict(symbol='OGDC',price=101,support=99)):
            snap=b.build_snapshot(c,self.now('19:30'))
        self.assertFalse(snap['ready'])
        self.assertEqual(snap['dip_watches'],[])
    def test_prior_session_missing_is_rejected(self):
        c=dict(self.c,symbols=['OGDC'])
        prior=self.fixture().iloc[:-2]
        with patch.object(b.yf,'Ticker') as ticker:
            ticker.return_value.history.return_value=prior
            with self.assertRaisesRegex(ValueError,'Prior-session history missing'):
                b.get_stock('OGDC',date(2026,9,22),c,
                    dict(rows={'OGDC':dict(LDCP=101,Open=101,High=103,Low=100,Close=102,Volume=100000)}))
    def test_cross_source_close_mismatch_is_rejected(self):
        c=dict(self.c,symbols=['OGDC'])
        with patch.object(b.yf,'Ticker') as ticker:
            ticker.return_value.history.return_value=self.fixture().iloc[:-1]
            with self.assertRaisesRegex(ValueError,'mismatch'):
                b.get_stock('OGDC',date(2026,9,22),c,
                    dict(rows={'OGDC':dict(LDCP=130,Open=130,High=131,Low=129,Close=130,Volume=100000)}))

if __name__=='__main__':unittest.main()
