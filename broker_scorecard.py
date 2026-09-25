"""Reproducible price-level scorecard for manually entered broker calls.

This is a hypothetical next-session-open simulation, not evidence of execution.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parent


def read_prices(path):
    by_symbol = {}
    with Path(path).open(newline='') as stream:
        for raw in csv.DictReader(stream):
            bar = dict(date=date.fromisoformat(raw['date']),
                       open=float(raw['open']), high=float(raw['high']),
                       low=float(raw['low']), close=float(raw['close']))
            if not 0 < bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
                raise ValueError(f"Invalid bar: {raw['symbol']} {raw['date']}")
            by_symbol.setdefault(raw['symbol'], []).append(bar)
    for symbol, bars in by_symbol.items():
        bars.sort(key=lambda b: b['date'])
        if len({b['date'] for b in bars}) != len(bars):
            raise ValueError(f'Duplicate price date for {symbol}')
    return by_symbol


def score_call(call, prices):
    report_date = date.fromisoformat(call['report_date'])
    stop, target = float(call['stop']), float(call['target1'])
    if not 0 < stop < target or float(call['target2']) <= target:
        raise ValueError(f"Invalid levels for {call['id']}")
    bars = [b for b in prices.get(call['symbol'], []) if b['date'] >= report_date]
    if not bars or bars[0]['date'] != report_date:
        return dict(call=call, status='missing_entry_bar')
    entry = bars[0]['open']
    if not stop < entry < target:
        return dict(call=call, status='no_trade_at_open', entry=entry)
    for bar in bars:
        # A gap executes at the open. If both intraday levels are touched,
        # assume the stop came first because OHLC does not reveal their order.
        if bar['open'] <= stop:
            status, exit_price = 'stopped_gap', bar['open']
        elif bar['open'] >= target:
            status, exit_price = 'target_gap', bar['open']
        elif bar['low'] <= stop:
            status, exit_price = 'stopped', stop
        elif bar['high'] >= target:
            status, exit_price = 'target1', target
        else:
            continue
        return dict(call=call, status=status, entry=entry,
                    exit=exit_price, exit_date=bar['date'].isoformat(),
                    return_pct=100*(exit_price/entry-1))
    return dict(call=call, status='open', entry=entry,
                last_date=bars[-1]['date'].isoformat(), last_close=bars[-1]['close'])


def score_all(calls, prices):
    ids = [c['id'] for c in calls]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate broker call ID')
    return [score_call(call, prices) for call in calls]


def render(rows):
    closed = [r for r in rows if 'return_pct' in r]
    targets = sum(r['status'].startswith('target') for r in closed)
    stopped = sum(r['status'].startswith('stopped') for r in closed)
    repeated = sorted(s for s, count in Counter(r['call']['symbol'] for r in rows).items() if count > 1)
    lines = ['Broker-call scorecard — hypothetical next-session-open entries',
             'Exit at first target or stop; gap at open; stop first if both touch intraday.',
             'No fees, slippage, dividends or position sizing. A dip order may never have filled.',
             '']
    for r in rows:
        c = r['call']
        if 'return_pct' in r:
            result = f"{r['status']} {r['exit_date']} at {r['exit']:.2f}; {r['return_pct']:+.2f}%"
        else:
            result = r['status']
        entry = f"{r['entry']:.2f}" if 'entry' in r else 'unavailable'
        lines.append(f"{c['report_date']} {c['symbol']}: open {entry}; {result}")
    lines.extend(['', f'{targets} first-target exits; {stopped} stop exits; '
                  f'{len(rows)-len(closed)} unresolved or not entered.'])
    if closed:
        lines.append(f"Equal-weight mean per call before costs: {sum(r['return_pct'] for r in closed)/len(closed):+.2f}%")
    if repeated:
        lines.append('Repeated symbols counted as separate, overlapping calls: '+', '.join(repeated))
    return '\n'.join(lines)


def record_completed_session(calls, path, expected):
    """Append official completed-session bars for symbols with tracked calls."""
    from daily_briefing import market_summary
    summary = market_summary(expected)
    fields = ['date','symbol','open','high','low','close','source']
    path = Path(path)
    existing = []
    if path.exists():
        with path.open(newline='') as stream:
            existing = list(csv.DictReader(stream))
    seen = {(row['date'],row['symbol']) for row in existing}
    added = 0
    for symbol in sorted({c['symbol'] for c in calls}):
        quote = summary['rows'].get(symbol)
        if not quote or (expected.isoformat(),symbol) in seen:
            continue
        existing.append(dict(date=expected.isoformat(),symbol=symbol,
                             open=quote['Open'],high=quote['High'],low=quote['Low'],
                             close=quote['Close'],source='psx.com.pk/market-summary/'))
        added += 1
    if added:
        with NamedTemporaryFile('w',newline='',dir=path.parent,delete=False) as stream:
            temp = Path(stream.name)
            writer = csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
            writer.writeheader();writer.writerows(existing)
        temp.replace(path)
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calls', type=Path, default=ROOT/'broker_calls.json')
    parser.add_argument('--prices', type=Path, default=ROOT/'broker_prices.csv')
    parser.add_argument('--record-session', action='store_true',
                        help='Append today\'s completed official PSX bars for tracked symbols')
    args = parser.parse_args()
    calls = json.loads(args.calls.read_text())
    if args.record_session:
        from daily_briefing import PKT
        from datetime import datetime
        print(f"Recorded {record_completed_session(calls,args.prices,datetime.now(PKT).date())} new bars")
    else:
        print(render(score_all(calls, read_prices(args.prices))))


if __name__ == '__main__':
    main()
