"""Auditable outcomes for the exact next-day Telegram briefing rule.

Historical replay requires real OHLCV and official daily breadth CSVs. Forward
tracking uses the encrypted delivery journal and completed PSX market summary.
Neither mode assumes that a hypothetical entry was actually filled.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from daily_briefing import (PKT, ROOT, Journal, evaluate_candidate, evaluate_dip_watch,
                            load_config, market_summary, verified_bars)

HOLD_SESSIONS = 4
ROUND_TRIP_COST_PCT = 0.35
PRICE_FIELDS = ('Open', 'High', 'Low', 'Close', 'Volume')


def ensure_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS performance_bars (
        session TEXT NOT NULL, symbol TEXT NOT NULL, open REAL NOT NULL,
        high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        volume REAL NOT NULL, PRIMARY KEY(session, symbol))''')
    db.execute('''CREATE TABLE IF NOT EXISTS performance_market (
        session TEXT PRIMARY KEY, advancing INTEGER NOT NULL,
        declining INTEGER NOT NULL, index_close REAL NOT NULL)''')
    db.commit()


def record_completed_session(journal, summary, symbols):
    """Record verified daily prices once; never change an earlier observation."""
    db = journal.db
    ensure_tables(db)
    session = summary['date']
    with db:
        db.execute('INSERT OR IGNORE INTO performance_market VALUES (?,?,?,?)',
                   (session, summary['advancing'], summary['declining'], summary['index_close']))
        for symbol in symbols:
            bar = summary['rows'].get(symbol)
            if bar:
                db.execute('INSERT OR IGNORE INTO performance_bars VALUES (?,?,?,?,?,?,?)',
                           (session, symbol, *(float(bar[k]) for k in PRICE_FIELDS)))


def delivered_setups(journal):
    """Only score plans the Telegram API confirmed as sent."""
    rows = journal.db.execute('''SELECT d.key, s.payload FROM deliveries d
        JOIN snapshots s ON s.session=substr(d.key,1,10)
        WHERE d.status='sent' AND d.key LIKE '%:plan' ORDER BY d.key''')
    for row in rows:
        snapshot = json.loads(row['payload'])
        if snapshot.get('ready'):
            for setup in snapshot.get('candidates', []) + snapshot.get('dip_setups', []):
                yield snapshot, setup


def stored_bars(journal, symbol, after):
    rows = journal.db.execute('''SELECT session, open, high, low, close, volume
        FROM performance_bars WHERE symbol=? AND session>? ORDER BY session''',
        (symbol, after)).fetchall()
    return [dict(session=r['session'], Open=r['open'], High=r['high'],
                 Low=r['low'], Close=r['close'], Volume=r['volume']) for r in rows]


def simulate_outcome(setup, bars, hold_sessions=HOLD_SESSIONS,
                     round_trip_cost_pct=ROUND_TRIP_COST_PCT,
                     expected_entry_session=None, expected_sessions=None):
    """Conservative hypothetical fill: next open in band; stop wins same-bar tie."""
    if not bars:
        return dict(status='missing_entry_bar' if expected_sessions else 'awaiting_entry_bar')
    first = bars[0]
    if expected_entry_session and first['session'] != expected_entry_session:
        return dict(status='missing_entry_bar', expected_entry_session=expected_entry_session)
    opening = float(first['Open'])
    if not float(setup['entry_min']) <= opening <= float(setup['entry_max']):
        return dict(status='skipped_open_outside_band', opening=opening,
                    entry_session=first['session'])
    conditional = setup.get('strategy') == 'DIP_REVERSAL'
    if conditional and float(first['High']) < float(setup['trigger']):
        return dict(status='not_triggered', opening=opening,
                    entry_session=first['session'])
    entry = float(setup['trigger']) if conditional else opening
    stop, target = float(setup['stop_loss']), float(setup['tp1'])
    if not 0 < stop < entry < target:
        return dict(status='invalid_levels', entry=entry)
    sessions = expected_sessions or [bar['session'] for bar in bars[:hold_sessions]]
    by_session = {bar['session']: bar for bar in bars}
    for i, session in enumerate(sessions[:hold_sessions], 1):
        bar = by_session.get(session)
        if bar is None:
            return dict(status='missing_followup_bar', entry=entry,
                        entry_session=first['session'], missing_session=session)
        opening, high, low = (float(bar[k]) for k in ('Open', 'High', 'Low'))
        # On entry day the open is the fill. Later gaps fill at the opening price.
        if i > 1 and opening <= stop:
            status, exit_price = 'stopped_gap', opening
        elif i > 1 and opening >= target:
            status, exit_price = 'target_gap', opening
        elif low <= stop:
            status, exit_price = 'stopped', stop
        elif high >= target:
            status, exit_price = 'target1', target
        elif i == hold_sessions:
            status, exit_price = 'time_exit', float(bar['Close'])
        else:
            continue
        gross = 100 * (exit_price / entry - 1)
        return dict(status=status, entry=entry, entry_session=first['session'],
                    exit=exit_price, exit_session=bar['session'],
                    gross_pct=round(gross, 3),
                    net_pct=round(gross - round_trip_cost_pct, 3))
    return dict(status='open', entry=entry, entry_session=first['session'],
                sessions_seen=min(len(sessions), hold_sessions))


def forward_scorecard(journal):
    ensure_tables(journal.db)
    rows = []
    for snapshot, setup in delivered_setups(journal):
        sessions = [r[0] for r in journal.db.execute('''SELECT session FROM performance_market
            WHERE session>? ORDER BY session LIMIT ?''',
            (snapshot['session'], HOLD_SESSIONS)).fetchall()]
        outcome = simulate_outcome(setup, stored_bars(journal, setup['symbol'],
                                                      snapshot['session']),
                                   expected_entry_session=snapshot['for_session'],
                                   expected_sessions=sessions)
        rows.append(dict(source_session=snapshot['session'],
                         target_session=snapshot['for_session'],
                         symbol=setup['symbol'],strategy=setup.get('strategy','BREAKOUT'),
                         **outcome))
    return rows


def historical_replay(prices, breadth, config):
    """Replay the live candidate/watch rules with prior bars only.

    `prices` maps symbols to sorted DataFrames of real daily OHLCV;
    `breadth` maps session dates to official advance/decline counts. A source
    day without breadth is skipped rather than inventing a market regime.
    """
    days = sorted({d.date() for frame in prices.values() for d in frame.index})
    market_days = sorted(breadth)
    available = {symbol: prices[symbol] for symbol in config['symbols'] if symbol in prices}
    rows = []
    for day in days:
        market = breadth.get(day)
        if market is None or day not in market_days[:-1]:
            continue
        expected_entry = market_days[market_days.index(day)+1].isoformat()
        candidates, dips = [], []
        valid = 0
        for symbol, frame in available.items():
            history = frame.loc[frame.index.date <= day]
            if len(history) < 60 or history.index[-1].date() != day:
                continue
            valid += 1
            setup = evaluate_candidate(symbol, history, market)
            if setup:
                candidates.append(setup)
            elif dip := evaluate_dip_watch(symbol, history):
                dips.append(dip)
        if valid / len(config['symbols']) < config['min_coverage']:
            continue
        candidates.sort(key=lambda x: x['rr_tp1'], reverse=True)
        dips.sort(key=lambda x: (x['price']-x['support'])/x['price'])
        for setup in candidates[:config['top_n']] + dips[:config['top_n']]:
            future = available[setup['symbol']].loc[
                available[setup['symbol']].index.date > day].head(HOLD_SESSIONS)
            bars = [dict(session=index.date().isoformat(), **{
                key: float(row[key]) for key in PRICE_FIELDS})
                    for index, row in future.iterrows()]
            rows.append(dict(source_session=day.isoformat(),
                             symbol=setup['symbol'],strategy=setup.get('strategy','BREAKOUT'),
                             **simulate_outcome(setup, bars,
                                                expected_entry_session=expected_entry,
                                                expected_sessions=[d.isoformat() for d in
                                                    market_days if d>day][:HOLD_SESSIONS])))
    return rows


def read_replay_csv(prices_path, breadth_path):
    raw = pd.read_csv(prices_path)
    required = {'date', 'symbol', *PRICE_FIELDS}
    if not required.issubset(raw.columns):
        raise ValueError(f'Price CSV needs {sorted(required)}')
    raw['date'] = pd.to_datetime(raw['date'], errors='raise')
    prices = {}
    for symbol, group in raw.groupby('symbol'):
        frame = group.set_index('date').sort_index()[list(PRICE_FIELDS)]
        if frame.index.has_duplicates:
            raise ValueError(f'Duplicate prices for {symbol}')
        frame = frame.astype(float)
        if len(frame) >= 60:
            verified_bars(frame, frame.index[-1].date())
        prices[symbol] = frame
    breadth = {}
    with Path(breadth_path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row['date'])
            if day in breadth:
                raise ValueError(f'Duplicate breadth for {day}')
            breadth[day] = dict(advancing=int(row['advancing']),
                                declining=int(row['declining']))
            if min(breadth[day].values()) < 0:
                raise ValueError(f'Negative breadth for {day}')
    return prices, breadth


def report(rows, label, delivered_plans=None):
    closed = [r for r in rows if 'net_pct' in r]
    skipped = sum(r['status'].startswith('skipped') for r in rows)
    untriggered = sum(r['status']=='not_triggered' for r in rows)
    wins = sum(r['net_pct'] > 0 for r in closed)
    net = sum(r['net_pct'] for r in closed)
    lines = [label]
    if delivered_plans is not None:
        lines.append(f'Delivered plans: {delivered_plans}; plans with no scored setup: '
                     f'{delivered_plans-len({r["source_session"] for r in rows})}.')
    lines.append(f'Setups evaluated: {len(rows)}; hypothetical fills: {len(closed)}; '
                 f'opens outside zone skipped: {skipped}; dip triggers missed: {untriggered}; '
                 f'unresolved: {len(rows)-len(closed)-skipped-untriggered}.')
    for strategy in ('BREAKOUT','DIP_REVERSAL'):
        group=[r for r in rows if r.get('strategy','BREAKOUT')==strategy]
        finished=[r for r in group if 'net_pct' in r]
        if group:
            mean=f'{sum(r["net_pct"] for r in finished)/len(finished):+.2f}%' if finished else 'pending'
            lines.append(f'{strategy}: {len(group)} setups, {len(finished)} closed fills, mean net {mean}.')
    if closed:
        lines.append(f'Net wins: {wins}/{len(closed)}; mean per filled setup: '
                     f'{net/len(closed):+.2f}% after {ROUND_TRIP_COST_PCT:.2f}% assumed costs.')
    else:
        lines.append('No closed hypothetical fills yet. Edge is not established.')
    lines.append('Hypothetical fills use daily bars; same-day stop/target ties count as stopped. '
                 'Actual execution, slippage and taxes may differ.')
    if rows:
        lines.append('Recent setups:')
        for row in rows[-10:]:
            result = (f'{row["net_pct"]:+.2f}% net' if 'net_pct' in row
                      else row['status'])
            lines.append(f'{row["source_session"]} {row["symbol"]} {row.get("strategy","BREAKOUT")}: {result} '
                         f'({row["status"]})')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--record', action='store_true')
    mode.add_argument('--forward-report', action='store_true')
    mode.add_argument('--replay', action='store_true')
    parser.add_argument('--journal', type=Path, default=ROOT/'.state'/'briefing.sqlite3')
    parser.add_argument('--prices', type=Path)
    parser.add_argument('--breadth', type=Path)
    args = parser.parse_args()
    if args.replay:
        if not args.prices or not args.breadth:
            parser.error('--replay requires --prices and --breadth')
        prices, breadth = read_replay_csv(args.prices, args.breadth)
        print(report(historical_replay(prices, breadth, load_config()),
                     'Historical briefing-rule replay'))
        return
    journal = Journal(args.journal)
    if args.record:
        config = load_config()
        summary = market_summary(datetime.now(PKT).date())
        record_completed_session(journal, summary, config['symbols'])
        print(f"Recorded official session {summary['date']} for forward scorecard")
    else:
        delivered = journal.db.execute('''SELECT COUNT(*) FROM deliveries
            WHERE status='sent' AND key LIKE '%:plan' ''').fetchone()[0]
        print(report(forward_scorecard(journal), 'Delivered briefing scorecard',
                     delivered_plans=delivered))


if __name__ == '__main__':
    main()
