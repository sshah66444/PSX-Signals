"""Official PSX monthly historical OHLCV, cached separately from demo/Yahoo data."""
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import time
from zoneinfo import ZoneInfo
import pandas as pd
import requests

class HistoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headers, self.rows, self.row = [], [], []
        self.cell, self.kind, self.active = None, None, False
    def handle_starttag(self, tag, attrs):
        if tag == 'table' and dict(attrs).get('id') == 'historicalTable': self.active = True
        if not self.active: return
        if tag == 'tr': self.row = []
        if tag in ('th', 'td'): self.cell, self.kind = [], tag
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if not self.active: return
        if tag in ('td', 'th') and self.cell is not None:
            value = ''.join(self.cell).strip()
            if tag == 'th': self.headers.append(value.upper())
            else: self.row.append(value)
            self.cell = None
        if tag == 'tr' and self.row: self.rows.append(self.row)
        if tag == 'table': self.active = False


def parse_history(html):
    parser = HistoryParser(); parser.feed(html)
    columns = ['DATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']
    if parser.headers != columns:
        raise ValueError('PSX history table missing or column format changed')
    df = pd.DataFrame(parser.rows, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['Date'] = pd.to_datetime(df['Date'], format='%b %d, %Y')
    for col in columns[1:]:
        name = col.title()
        df[name] = pd.to_numeric(df[name].str.replace(',', '', regex=False))
    return df.set_index('Date').sort_index()


def fetch_official(symbol, period, cache_dir):
    months = {'1mo':1, '3mo':3, '6mo':6, '1y':12, '2y':24}[period]
    now = datetime.now(ZoneInfo('Asia/Karachi'))
    base = now.year * 12 + now.month - 1
    frames = []
    directory = Path(cache_dir) / 'official_psx'
    directory.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        for offset in range(months + 1):
            year, month0 = divmod(base - offset, 12)
            month = month0 + 1
            path = directory / f'{symbol}_{year}_{month:02d}.csv'
            # Current month refreshes every 15 minutes; historical corrections weekly.
            ttl = 900 if offset == 0 else 604800
            cached = None
            if path.exists():
                try:
                    cached = pd.read_csv(path, index_col='Date', parse_dates=True)
                except (ValueError, OSError):
                    cached = None
            if cached is not None and time.time() - path.stat().st_mtime < ttl:
                frames.append(cached); continue
            try:
                response = session.post('https://dps.psx.com.pk/historical',
                                        data={'symbol':symbol, 'year':year, 'month':month}, timeout=20)
                response.raise_for_status()
                frame = parse_history(response.text)
                if not frame.empty:
                    temp = path.with_suffix('.tmp')
                    frame.to_csv(temp)
                    temp.replace(path)
                frames.append(frame)
            except (requests.RequestException, ValueError):
                if cached is None:
                    raise ValueError('Official PSX history unavailable; no complete cached history') from None
                frames.append(cached)
    result = pd.concat(frames).sort_index()
    start = pd.Timestamp(now.date()) - pd.DateOffset(months=months)
    return result[result.index >= start], 'Official PSX historical OHLCV (15-minute local cache; unadjusted)'
