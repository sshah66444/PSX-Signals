"""Evening next-session plans. Independent of the legacy trading ledger."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import os
import sqlite3
import time
import requests
import numpy as np
import pandas as pd
from psx_provider import fetch_official
from data_engine import fetch_kse100_index
from signal_engine import generate_signal

ROOT = Path(__file__).resolve().parent
PKT = ZoneInfo('Asia/Karachi')


def load_config(path=None):
    c = json.loads(Path(path or ROOT/'briefing_config.json').read_text())
    for key in ('prepare_time', 'delivery_time', 'cutoff_time'):
        datetime.strptime(c[key], '%H:%M')
    if not c['prepare_time'] < c['delivery_time'] < c['cutoff_time']:
        raise ValueError('Require preparation < delivery < cutoff on the same evening')
    if not 0 < c['min_coverage'] <= 1 or not 1 <= c['top_n'] <= 3:
        raise ValueError('Coverage must be (0,1]; top_n must be 1–3')
    if not c['symbols'] or len(c['symbols']) != len(set(c['symbols'])):
        raise ValueError('Provide a nonempty, unique watchlist')
    import re
    if not all(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,19}', x) for x in c['symbols']):
        raise ValueError('Invalid watchlist symbol')
    if not 1 <= c['workers'] <= 4 or c['retry_minutes'] < 5:
        raise ValueError('Use 1–4 workers and retries at least 5 minutes apart')
    for value in c['holidays']: date.fromisoformat(value)
    return c


def session_day(d, config):
    if d.year != config['calendar_year']:
        raise ValueError('Trading calendar expired; update calendar_year and holidays before sending plans')
    return d.weekday() < 5 and d.isoformat() not in config['holidays']


def next_session(d, config):
    for _ in range(14):
        d += timedelta(days=1)
        if session_day(d, config): return d
    raise ValueError('No next session found in configured calendar')


def verified_bars(df, expected):
    if df is None or len(df) < 60:
        raise ValueError('Fewer than 60 daily bars')
    d = df.copy()
    d.index = pd.to_datetime(d.index).tz_localize(None)
    if d.index.has_duplicates or not d.index.is_monotonic_increasing:
        raise ValueError('Unordered/duplicate session dates')
    if d.index[-1].date() != expected:
        raise ValueError(f'Latest data {d.index[-1].date()}; need {expected}')
    columns = ['Open','High','Low','Close','Volume']
    if not set(columns).issubset(d.columns) or not np.isfinite(d[columns].to_numpy(dtype=float)).all():
        raise ValueError('Incomplete OHLCV data')
    if (d[columns[:-1]] <= 0).any().any() or (d.Volume < 0).any():
        raise ValueError('Invalid price or volume')
    if (d.High < d[['Open','Close','Low']].max(axis=1)).any() or (d.Low > d[['Open','Close']].min(axis=1)).any():
        raise ValueError('Invalid daily candle bounds')
    if ((d.Open / d.Close.shift(1) - 1).abs() > .35).any():
        raise ValueError('Large price discontinuity; corporate-action/data review required')
    return d


def get_stock(symbol, expected, config):
    df, source = fetch_official(symbol, config['period'], str(ROOT/'.cache'))
    return verified_bars(df, expected), source


def build_snapshot(config, now=None):
    now = now or datetime.now(PKT)
    expected = now.astimezone(PKT).date()
    if not session_day(expected, config) or now.astimezone(PKT).strftime('%H:%M') < config['prepare_time']:
        raise ValueError('Prepare only after configured evening time on a trading day')
    target = next_session(expected, config)
    benchmark, regime = fetch_kse100_index(force_refresh=True)
    benchmark_ok = (benchmark is not None and len(benchmark) >= 50
                    and benchmark.index[-1].date() == expected
                    and np.isfinite(benchmark.Close.to_numpy(dtype=float)).all()
                    and (benchmark.Close > 0).all())
    errors, candidates = {}, []
    valid = 0
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        futures = {pool.submit(get_stock, symbol, expected, config):symbol for symbol in config['symbols']}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                df, source = future.result()
                valid += 1
                if not benchmark_ok: continue
                setup = generate_signal(symbol, df, data_meta=dict(source=source,has_true_ohlc=True,
                    last_date=expected.isoformat(),data_age_days=0,is_settled=True,
                    expected_date=expected.isoformat()),df_kse=benchmark,market_regime=regime)
                if setup.get('status') not in ('TRIGGERED','WATCHING'): continue
                keys = ('entry_min','entry_max','stop_loss','tp1','tp2','rr_tp1','price')
                if not all(isinstance(setup.get(k),(float,int)) and math.isfinite(setup[k]) for k in keys): continue
                if not 0 < setup['stop_loss'] < setup['entry_min'] <= setup['entry_max'] < setup['tp1'] < setup['tp2']: continue
                # Do not promote structurally invalid WATCHING setups just to fill a shortlist.
                checks = setup.get('checklist',{})
                failures = [k for k,v in checks.items() if not v]
                if len(failures) > 2: continue
                if any(any(word in key.lower() for word in ('liquidity','macro market','true high','risk:reward')) for key in failures): continue
                keep = {k:setup[k] for k in keys}
                keep.update(symbol=symbol,strategy=setup['strategy'],status=setup['status'],
                            failures=failures,reason=setup.get('trigger_note','')[:240])
                candidates.append(keep)
            except (ValueError, OSError, requests.RequestException, KeyError, TypeError) as exc:
                errors[symbol] = str(exc)[:160]
    candidates.sort(key=lambda x:(x['status']=='TRIGGERED',-len(x['failures']),x['rr_tp1']),reverse=True)
    coverage = valid / len(config['symbols'])
    ready = bool(benchmark_ok and coverage >= config['min_coverage'])
    return dict(session=expected.isoformat(),for_session=target.isoformat(),generated_at=now.isoformat(),
                source='Official PSX daily OHLCV',total=len(config['symbols']),valid=valid,
                ready=ready,benchmark_ok=bool(benchmark_ok),regime=regime.get('regime','UNKNOWN') if benchmark_ok else 'UNVERIFIED',
                candidates=candidates[:config['top_n']] if ready else [],errors=errors)


def render(snapshot):
    s=snapshot
    lines=[f"PSX PLAN — {s['for_session']}",f"Based on completed session: {s['session']}",
           'Horizon: trades held a few sessions',
           f"Source: {s['source']} | Fresh stocks: {s['valid']}/{s['total']}",
           f"Market context: {s['regime']}"]
    if not s['ready']:
        lines += ['DATA INCOMPLETE — no new entry plan.',
                  'Latest-session prices or benchmark could not be verified. Older-session setups are withheld.']
    elif not s['candidates']:
        lines += ['NO QUALIFYING SETUPS — no new entry suggested by these rules.',
                  'The scan completed; no candidate passed the shortlist filters.']
    else:
        for i,x in enumerate(s['candidates'],1):
            label='Close confirmed; conditional entry next session' if x['status']=='TRIGGERED' else 'WATCH ONLY — confirmation still missing'
            entry=(x['entry_min']+x['entry_max'])/2
            rr=(x['tp1']-entry)/(entry-x['stop_loss'])
            block=[f"\n{i}. {x['symbol']} | {x['strategy']}",label,
                   f"Latest close: PKR {x['price']:.2f}",
                   f"Entry band: {x['entry_min']:.2f}–{x['entry_max']:.2f}",
                   f"Stop: {x['stop_loss']:.2f} | TP1: {x['tp1']:.2f} | TP2: {x['tp2']:.2f}",
                   f"TP1 reward/risk at entry midpoint: {rr:.2f}:1 before costs",
                   'Skip if opening price is outside the entry band; do not chase a gap.',
                   'Why: '+x['reason']]
            if x['failures']: block += ['Missing: '+ '; '.join(x['failures'])[:200],
                                        'Do not enter from this card; wait for a later completed-close confirmation.']
            if len('\n'.join(lines+block)) < 3200: lines += block
    if s.get('errors'): lines += ['Unavailable/stale: '+', '.join(sorted(s['errors']))[:230]]
    lines += ['\nLevels expire after the named session. These are conditional setups, not executed trades.']
    return '\n'.join(lines)


class Journal:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots(session TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(key TEXT PRIMARY KEY, status TEXT NOT NULL, message_id INTEGER, text TEXT NOT NULL);
        ''');self.db.commit()
    def snapshot(self,session):
        row=self.db.execute('SELECT payload FROM snapshots WHERE session=?',(session,)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,snapshot):
        with self.db:self.db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)',(snapshot['session'],json.dumps(snapshot)))
    def status(self,key):
        row=self.db.execute('SELECT status FROM deliveries WHERE key=?',(key,)).fetchone()
        return row[0] if row else None
    def deliver(self,key,text,sender):
        status=self.status(key)
        if status in ('sent','sending','uncertain'): return False
        with self.db:self.db.execute('INSERT OR REPLACE INTO deliveries(key,status,text) VALUES (?, ?, ?)',(key,'sending',text))
        try: mid=sender(text)
        except DeliveryRejected:
            with self.db:self.db.execute('UPDATE deliveries SET status=? WHERE key=?',('retry',key))
            raise
        except Exception:
            with self.db:self.db.execute('UPDATE deliveries SET status=? WHERE key=?',('uncertain',key))
            raise
        with self.db:self.db.execute('UPDATE deliveries SET status=?,message_id=? WHERE key=?',('sent',mid,key))
        return True


class DeliveryRejected(RuntimeError): pass


def telegram_sender(token,chat):
    def send(text):
        try:
            response=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                   json={'chat_id':chat,'text':text},timeout=20)
            if 400 <= response.status_code < 500:
                raise DeliveryRejected(f'Telegram rejected delivery (HTTP {response.status_code})')
            response.raise_for_status()
            data=response.json()
            if not data.get('ok'): raise DeliveryRejected('Telegram rejected delivery')
            return data['result']['message_id']
        except DeliveryRejected: raise
        except Exception:
            raise RuntimeError('Telegram delivery uncertain; inspect journal before retrying') from None
    return send


def tick(config,journal,sender,now=None):
    now=(now or datetime.now(PKT)).astimezone(PKT)
    day,clock=now.date(),now.strftime('%H:%M')
    if not session_day(day,config) or clock < config['prepare_time']: return
    session=day.isoformat(); key=session+':plan'
    if journal.status(key) in ('sent','sending','uncertain'): return
    snapshot=journal.snapshot(session)
    # Deliver prepared data before fetching anything: scan latency must not delay the deadline.
    if clock >= config['delivery_time']:
        if clock > config['cutoff_time']:
            journal.deliver(session+':missed',f'PSX {session}: no verified plan was delivered within the evening window. No late entry plan will be sent. Data or worker availability needs attention.',sender)
            return
        if snapshot and snapshot['ready']:
            journal.deliver(key,render(snapshot),sender);return
        journal.deliver(session+':pending',f"PSX plan for {next_session(day,config)} — {session} data is not fully verified yet. No entries suggested. Retrying until {config['cutoff_time']} Pakistan time.",sender)
    if snapshot:
        last=datetime.fromisoformat(snapshot['generated_at'])
        if (now-last).total_seconds() < config['retry_minutes']*60: return
    return True  # Main schedules background preparation without blocking delivery.


def safe_snapshot(config,now):
    try:
        return build_snapshot(config,now)
    except Exception as exc:
        return dict(session=now.date().isoformat(),for_session=next_session(now.date(),config).isoformat(),generated_at=now.isoformat(),ready=False,
                    source='Official PSX daily OHLCV',total=len(config['symbols']),valid=0,
                    benchmark_ok=False,regime='UNVERIFIED',candidates=[],errors={'scan':type(exc).__name__})


def load_credentials():
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('\"\''))
    token,chat=os.getenv('TELEGRAM_BOT_TOKEN'),os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat: raise ValueError('Configure TELEGRAM_BOT_TOKEN and one TELEGRAM_CHAT_ID')
    if ',' in chat or chat=='*': raise ValueError('Configure exactly one recipient for this personal briefing')
    return token,chat


def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--preview',action='store_true')
    mode.add_argument('--daemon',action='store_true')
    mode.add_argument('--tick',action='store_true')
    parser.add_argument('--config',type=Path,default=ROOT/'briefing_config.json')
    args=parser.parse_args();config=load_config(args.config)
    if not (args.daemon or args.tick):
        snapshot=build_snapshot(config)
        print(render(snapshot));return
    token,chat=load_credentials()
    journal=Journal(ROOT/'.state'/'briefing.sqlite3')
    # Reject accidental concurrent workers; macOS/Linux deployment.
    import fcntl
    lock=open(ROOT/'.state'/'worker.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    sender=telegram_sender(token,chat)
    # Prevent changing the destination while retaining another recipient's delivery state.
    recipient=hashlib.sha256(chat.encode()).hexdigest()
    journal.db.execute('CREATE TABLE IF NOT EXISTS owner(recipient TEXT)')
    old=journal.db.execute('SELECT recipient FROM owner').fetchone()
    if old and old[0]!=recipient: raise ValueError('Recipient changed; use a separate journal directory')
    if not old:
        with journal.db:journal.db.execute('INSERT INTO owner VALUES (?)',(recipient,))
    executor=ThreadPoolExecutor(max_workers=1)
    future=None
    while True:
        try:
            config=load_config(args.config)
            if future is not None and future.done():
                journal.save(future.result());future=None
            due=tick(config,journal,sender)
            if due and future is None:
                future=executor.submit(safe_snapshot,config,datetime.now(PKT))
            if args.tick:
                if future is not None: journal.save(future.result())
                tick(config,journal,sender)
                break
        except Exception as exc:
            print(f'Briefing worker requires attention: {type(exc).__name__}',flush=True)
            if args.tick:raise SystemExit(1)
        time.sleep(30)

if __name__=='__main__':main()
