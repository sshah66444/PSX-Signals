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
from bs4 import BeautifulSoup
import yfinance as yf
from signal_engine import compute_all_indicators

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


def previous_session(d,config):
    for _ in range(14):
        d-=timedelta(days=1)
        if session_day(d,config):return d
    raise ValueError('No previous trading session in configured calendar')


def market_summary(expected):
    """Read one completed official PSX market snapshot for the named source date."""
    response=requests.get('https://www.psx.com.pk/market-summary/',timeout=20)
    response.raise_for_status()
    soup=BeautifulSoup(response.text,'html.parser')
    stamp=soup.select_one('.inner-content-table h4')
    if not stamp:raise ValueError('PSX market summary timestamp missing')
    published=datetime.strptime(stamp.get_text(' ',strip=True)[:19],'%Y-%m-%d %H:%M:%S')
    if published.date()!=expected or published.time().hour<17:
        raise ValueError(f'Official market summary is not a completed {expected} session')
    exchange=soup.select_one('.inner-content-table .ms-tbl-new')
    if not exchange or 'Status: Closed' not in exchange.get_text(' ',strip=True):
        raise ValueError('PSX market session is not marked closed')
    rows={}
    for cell in soup.select('td.dataportal[data-srip]'):
        symbol=cell.get('data-srip','')
        cells=cell.parent.find_all('td',recursive=False)
        if len(cells)<8:continue
        try:
            vals=[float(cells[i].get_text(' ',strip=True).replace(',','')) for i in (1,2,3,4,5,7)]
            ldcp,o,h,l,c,v=vals
            if 0<l<=min(o,c)<=max(o,c)<=h and v>=0:
                rows[symbol]=dict(LDCP=ldcp,Open=o,High=h,Low=l,Close=c,Volume=v)
        except ValueError:continue
    index_card=None
    for card in soup.select('.indices-single'):
        if card.find('h3') and card.find('h3').get_text(strip=True)=='KSE100':
            index_card=card;break
    if not index_card or not index_card.find('h4'):
        raise ValueError('KSE-100 current close missing')
    index_close=float(index_card.find('h4').get_text(strip=True).replace(',',''))
    stats=exchange.get_text(' ',strip=True)
    import re
    matches=re.search(r'Advanced:\s*([\d,]+).*?Declined:\s*([\d,]+)',soup.get_text(' ',strip=True))
    if not matches or not rows:raise ValueError('PSX market breadth/stock rows missing')
    advancing,declining=(int(x.replace(',','')) for x in matches.groups())
    return dict(date=expected.isoformat(),rows=rows,index_close=index_close,
                advancing=advancing,declining=declining,asof=published.isoformat())


def get_stock(symbol,expected,config,summary):
    today=summary['rows'].get(symbol)
    if not today:raise ValueError('Symbol missing from completed PSX market summary')
    df=yf.Ticker(f'{symbol}.KA').history(period=config['period'],interval='1d',auto_adjust=False)
    if df is None or df.empty:raise ValueError('Yahoo historical OHLCV unavailable')
    d=df[['Open','High','Low','Close','Volume']].copy()
    d.index=pd.to_datetime(d.index).tz_localize(None)
    d=d[d.index.date<expected]
    prev=previous_session(expected,config)
    if len(d)<59 or d.index[-1].date()!=prev:
        raise ValueError(f'Prior-session history missing (need {prev})')
    # Check the cross-source overlap before appending today's official bar.
    if abs(float(d.Close.iloc[-1])/today['LDCP']-1)>.005:
        raise ValueError('Yahoo/PSX prior-close mismatch; corporate action or feed conflict')
    fresh=pd.DataFrame([{k:today[k] for k in ('Open','High','Low','Close','Volume')}],
                       index=pd.to_datetime([expected]))
    combined=pd.concat([d,fresh]).sort_index()
    return verified_bars(combined,expected), 'PSX completed market summary + Yahoo prior history'


def evaluate_candidate(symbol,df,summary):
    """Transparent short-horizon rule with no unverified index-history claims."""
    d=compute_all_indicators(df)
    b=d.iloc[-1];p=d.iloc[-2]
    close,atr=float(b.Close),float(b.ATR)
    resistance=float(d.High.iloc[-21:-1].max())
    trend=bool(close>b.EMA_20>b.EMA_50 and b.EMA_50>d.EMA_50.iloc[-6])
    volume_ok=bool(b.Volume>=1.2*b.Vol_MA20 and b.Vol_MA20>=80000)
    momentum=bool(b.MACD_Hist>p.MACD_Hist and 42<=b.RSI<=68)
    breakout=bool(close>resistance and close<=resistance+1.5*atr)
    healthy_market=summary['advancing']>=summary['declining']
    entry_low=round(close,2)
    entry_high=round(close+0.25*atr,2)
    stop=round(min(float(d.Low.iloc[-21:-1].min()),close-1.3*atr),2)
    risk=entry_high-stop
    checks=dict(trend=trend,liquidity=volume_ok,momentum=momentum,
                breakout=breakout,market_breadth=healthy_market,
                positive_risk=stop>0 and risk>0)
    if not all(checks.values()):return None
    tp1=round(entry_high+1.5*risk,2)
    tp2=round(entry_high+2.5*risk,2)
    return dict(symbol=symbol,strategy='BREAKOUT',status='TRIGGERED',price=close,
                entry_min=entry_low,entry_max=entry_high,stop_loss=stop,tp1=tp1,tp2=tp2,
                rr_tp1=round((tp1-entry_high)/risk,2),failures=[],
                reason='Fresh 20-session breakout, rising trend, 1.2× volume and improving MACD.')


def evaluate_dip_watch(symbol,df):
    """Observation only: a recovering share near recent support, not an entry call."""
    d=compute_all_indicators(df)
    b,p=d.iloc[-1],d.iloc[-2]
    close,atr=float(b.Close),float(b.ATR)
    if not np.isfinite([close,atr,b.RSI,b.Vol_MA20,b.MACD_Hist,p.MACD_Hist]).all() or atr<=0:
        return None
    recent_low=float(d.Low.iloc[-11:-1].min())
    resistance=float(d.High.iloc[-21:-1].max())
    support=max(x for x in (recent_low,float(b.EMA_20),float(b.EMA_50)) if x<close) if any(
        x<close for x in (recent_low,float(b.EMA_20),float(b.EMA_50))
    ) else recent_low
    checks=(b.Vol_MA20>=80000, 35<=b.RSI<=60, b.RSI>p.RSI,
            b.MACD_Hist>p.MACD_Hist, close>=p.Close,
            0<close-support<=1.5*atr, close<resistance)
    if not all(checks):return None
    return dict(symbol=symbol,price=round(close,2),support=round(support,2),
                confirmation=round(float(b.High),2),invalidation=round(max(.01,support-.5*atr),2),
                reason='Near recent support; RSI and MACD are improving. Wait for a later close above confirmation.')


def build_snapshot(config,now=None):
    now=now or datetime.now(PKT)
    expected=now.astimezone(PKT).date()
    if not session_day(expected,config) or now.astimezone(PKT).strftime('%H:%M')<config['prepare_time']:
        raise ValueError('Prepare only after configured evening time on a trading day')
    target=next_session(expected,config)
    summary=market_summary(expected)
    errors,candidates,watches={},[],[]
    valid=0
    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
        futures={pool.submit(get_stock,symbol,expected,config,summary):symbol for symbol in config['symbols']}
        for future in as_completed(futures):
            symbol=futures[future]
            try:
                df,source=future.result()
                valid+=1
                candidate=evaluate_candidate(symbol,df,summary)
                if candidate:candidates.append(candidate)
                elif watch:=evaluate_dip_watch(symbol,df):watches.append(watch)
            except (ValueError,OSError,requests.RequestException,KeyError,TypeError,IndexError) as exc:
                errors[symbol]=str(exc)[:160]
    candidates.sort(key=lambda x:x['rr_tp1'],reverse=True)
    watches.sort(key=lambda x:(x['price']-x['support'])/x['price'])
    coverage=valid/len(config['symbols'])
    ready=coverage>=config['min_coverage']
    regime=(f"KSE-100 {summary['index_close']:,.2f}; "
            f"advancing {summary['advancing']}, declining {summary['declining']}")
    return dict(session=expected.isoformat(),for_session=target.isoformat(),generated_at=now.isoformat(),
                source='Official PSX completed market summary + Yahoo prior history',
                total=len(config['symbols']),valid=valid,ready=ready,benchmark_ok=True,
                regime=regime,candidates=candidates[:config['top_n']] if ready else [],
                dip_watches=watches[:config['top_n']] if ready else [],errors=errors)


def render(snapshot):
    s=snapshot
    lines=[f"PSX PLAN — {s['for_session']}",f"Based on completed session: {s['session']}",
           'Horizon: trades held a few sessions',
           f"Source: {s['source']} | Fresh stocks: {s['valid']}/{s['total']}",
           f"Market context: {s['regime']}"]
    if not s['ready']:
        lines += ['DATA INCOMPLETE — no new entry plan.',
                  'The completed-session summary or enough stock histories could not be verified. Older-session setups are withheld.']
    elif not s['candidates']:
        lines += ['NO QUALIFYING BREAKOUTS — no new entry suggested by the breakout rules.']
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
    if s['ready'] and s.get('dip_watches'):
        lines += ['\nDIP WATCHLIST — observation only; no entry suggested today.']
        for x in s['dip_watches']:
            block=[f"{x['symbol']}: close {x['price']:.2f}; support ~{x['support']:.2f}; ",
                   f"watch for a later completed close above {x['confirmation']:.2f}; ",
                   f"invalidate below {x['invalidation']:.2f}."]
            if len('\n'.join(lines+block))<3200:lines.append(''.join(block))
        lines += ['A watch is not a buy signal. Recheck price, volume and market context after confirmation.']
    elif s['ready'] and not s['candidates']:
        lines += ['No dip watches met the observation filters either.']
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
                    source='Official PSX market summary + Yahoo prior history',total=len(config['symbols']),valid=0,
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
