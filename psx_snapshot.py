#!/usr/bin/env python3
"""
psx_snapshot.py
Daily official EOD snapshot from the PSX public market-summary page.

PSX retired its data-portal JSON API, but `www.psx.com.pk/market-summary/` still
publishes the exchange's own closing snapshot: one table per sector with
SCRIP / LDCP / OPEN / HIGH / LOW / CURRENT / CHANGE / VOLUME for every listed
symbol (~568 on a normal session).

Scraping that page once per trading day therefore gives official OHLCV for the
whole market — including the symbols Yahoo Finance has dropped — and lets the
screener build its own permanent history from today onward.

Usage
-----
  python psx_snapshot.py --dry-run     # parse + report, write nothing
  python psx_snapshot.py               # update the cache (only when session closed)
  python psx_snapshot.py --force       # import even if the session is still open
  python psx_snapshot.py --limit 5     # preview a few symbols
"""

from __future__ import annotations

import os
import re
import sys
import json
import html as html_mod
import datetime
import argparse

import pandas as pd
import requests

from data_engine import CACHE_DIR, get_expected_session_date

SNAPSHOT_URL = "https://www.psx.com.pk/market-summary/"
SESSION_URL = "https://www.psx.com.pk/market-summary/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

SNAPSHOT_COLUMNS = ["symbol", "name", "ldcp", "open", "high", "low", "close", "change", "volume"]


def fetch_html(url: str = SNAPSHOT_URL, timeout: int = 30) -> str:
    """Downloads the market-summary page."""
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_session_status(page_html: str) -> str:
    """
    Reads the exchange status banner ("Closed" / "Open" / "Pre-open").

    Only a closed session represents settled end-of-day prices.
    """
    match = re.search(r"Status:\s*(?:<[^>]+>\s*)*([A-Za-z][A-Za-z\s-]{2,20})", page_html)
    if not match:
        return "Unknown"
    return match.group(1).strip().split()[0].title()


def _cells(row_html: str) -> list:
    raw = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    return [html_mod.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in raw]


def _num(text: str) -> float:
    if text is None:
        return float("nan")
    cleaned = str(text).replace(",", "").replace("%", "").replace("--", "").strip()
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return float("nan")


def parse_snapshot(page_html: str) -> pd.DataFrame:
    """
    Parses every sector table into a tidy frame of official EOD rows.

    The ticker lives in the row's `data-srip` attribute and the visible cell is
    the company name, so columns are mapped positionally after the name cell.
    """
    records = []
    for table in re.findall(r"<table.*?</table>", page_html, re.S):
        sector = None
        header_seen = False
        for row in re.findall(r"<tr[^>]*>.*?</tr>", table, re.S):
            cells = _cells(row)
            if not cells:
                continue

            # Sector heading rows have a single cell (e.g. "APPAREL")
            if len(cells) == 1 and not header_seen:
                sector = cells[0].strip().title()
                continue
            if any(c.upper() == "SCRIP" for c in cells):
                header_seen = True
                continue
            if not header_seen:
                continue

            m = re.search(r'data-srip="([A-Z0-9.\-]{2,12})"', row)
            if not m:
                continue
            symbol = m.group(1).upper().strip()

            name = ""
            name_match = re.search(r'data-srip="[^"]*"[^>]*>(.*?)</td>', row, re.S)
            if name_match:
                name = html_mod.unescape(re.sub(r"<[^>]+>", " ", name_match.group(1))).strip()

            # Values follow the name cell: LDCP, OPEN, HIGH, LOW, CURRENT, CHANGE, VOLUME
            numeric = [_num(c) for c in cells if _num(c) == _num(c)]  # numeric cells only
            if len(numeric) < 6:
                continue

            ldcp, open_p, high, low, close, change = numeric[:6]
            volume = numeric[6] if len(numeric) > 6 else 0.0
            if close <= 0:
                continue

            # Repair inverted or missing intraday bounds instead of storing bad bars
            open_p = open_p if open_p > 0 else close
            high = max(high if high > 0 else close, open_p, close)
            low = min(low if low > 0 else close, open_p, close)

            records.append({
                "symbol": symbol,
                "name": name,
                "ldcp": ldcp,
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "change": change,
                "volume": int(volume) if volume > 0 else 0,
                "sector": sector or "",
            })

    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["symbol"], keep="last")
    return frame.sort_values("symbol").reset_index(drop=True)


def update_cache(frame: pd.DataFrame, session_date: datetime.date = None,
                 dry_run: bool = False) -> dict:
    """
    Upserts each symbol's snapshot row into .cache/<SYM>_real.csv.

    Writes a `<SYM>_psx.json` provenance marker so fetch_psx_stock() prefers this
    official exchange data over scraped sources for the rest of the day.
    """
    if frame is None or frame.empty:
        return {"updated": 0, "skipped": 0, "date": None, "symbols": []}

    session_date = session_date or get_expected_session_date()
    stamp = pd.Timestamp(session_date).normalize()
    updated, skipped, symbols = 0, 0, []

    for row in frame.itertuples(index=False):
        cache_path = os.path.join(CACHE_DIR, f"{row.symbol}_real.csv")
        try:
            if os.path.exists(cache_path):
                existing = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                existing.index.name = "Date"
            else:
                existing = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
                existing.index.name = "Date"

            bar = pd.DataFrame(
                {
                    "Open": [float(row.open)],
                    "High": [float(row.high)],
                    "Low": [float(row.low)],
                    "Close": [float(row.close)],
                    "Volume": [int(row.volume)],
                },
                index=pd.DatetimeIndex([stamp], name="Date"),
            )

            had_date = stamp in existing.index if len(existing) else False
            merged = pd.concat([existing, bar]) if len(existing) else bar
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()

            if not dry_run:
                merged.to_csv(cache_path)
                marker = {
                    "source": "psx_market_summary",
                    "symbol": row.symbol,
                    "session": str(session_date),
                    "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "rows": int(len(merged)),
                    "last_date": merged.index[-1].strftime("%Y-%m-%d"),
                    "sector": getattr(row, "sector", "") or "",
                }
                with open(os.path.join(CACHE_DIR, f"{row.symbol}_psx.json"), "w") as fh:
                    json.dump(marker, fh, indent=2)

            updated += 1
            skipped += int(had_date)
            symbols.append(row.symbol)
        except Exception:
            continue

    return {
        "updated": updated,
        "skipped": skipped,
        "date": str(session_date),
        "symbols": symbols,
    }


def refresh(dry_run: bool = False, force: bool = False, limit: int = None) -> dict:
    """
    Fetches the official snapshot and (unless the session is still open) stores it.

    Returns a summary dict; raises only for network/parse failures so callers can
    degrade gracefully.
    """
    page = fetch_html()
    status = parse_session_status(page)
    frame = parse_snapshot(page)
    if limit:
        frame = frame.head(limit)

    if frame.empty:
        raise RuntimeError("PSX snapshot parsed 0 symbols (page layout may have changed)")

    if status.lower() != "closed" and not force:
        return {
            "updated": 0,
            "skipped": 0,
            "date": str(get_expected_session_date()),
            "symbols": [],
            "session": status,
            "deferred": True,
            "parsed": len(frame),
        }

    result = update_cache(frame, dry_run=dry_run)
    result["session"] = status
    result["parsed"] = len(frame)
    result["deferred"] = False
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import the official PSX EOD snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing")
    parser.add_argument("--force", action="store_true", help="Import even if the session is open")
    parser.add_argument("--limit", type=int, help="Only process the first N symbols (preview)")
    args = parser.parse_args(argv)

    try:
        result = refresh(dry_run=args.dry_run, force=args.force, limit=args.limit)
    except Exception as exc:
        print(f"Snapshot failed: {exc}")
        return 1

    if result.get("deferred"):
        print(
            f"PSX session is '{result['session']}' — snapshot deferred "
            f"({result['parsed']} symbols parsed, nothing written)."
        )
        return 0

    flag = "DRY RUN" if args.dry_run else "imported"
    print(
        f"PSX snapshot {flag}: {result['updated']} symbols "
        f"({result['skipped']} already had this session) for {result['date']} "
        f"[session: {result['session']}]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
