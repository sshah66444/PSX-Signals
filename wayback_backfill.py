#!/usr/bin/env python3
"""
wayback_backfill.py
Recovers historical PSX bars from the Internet Archive.

PSX retired its /timeseries/eod API, but the Wayback Machine captured it while
it was live. Each capture holds the symbol's full timeseries up to the capture
date (ENGRO: 1052 bars, 2020-10 -> 2025-01), which gives backfill for symbols
Yahoo Finance never carried.

Archived market-summary pages are also usable: each capture is one official
snapshot (true OHLC) for the whole market.

Notes
-----
* Archived /timeseries payloads are [timestamp, close, volume, open] with no
  intraday High/Low, so bars from them are labelled approximated (they warm up
  indicators but cannot trigger a live entry).
* Coverage is whatever happened to be crawled — expect gaps, not a clean series.

Usage
-----
  python wayback_backfill.py --symbols ENGRO,FFBL,SHEL,CYNERGICO,GLL,IPAK --dry-run
  python wayback_backfill.py --symbols ENGRO
  python wayback_backfill.py --symbols ENGRO --snapshots 40   # also replay archived market summaries
"""

from __future__ import annotations

import os
import sys
import json
import time
import gzip
import argparse
import datetime

import pandas as pd
import requests

from data_engine import CACHE_DIR, PKT_TZ

CDX_URL = "http://web.archive.org/cdx/search/cdx"
RAW_URL = "https://web.archive.org/web/{ts}id_/{url}"
HEADERS = {"User-Agent": "psx-signal-screener/1.0 (personal research)"}
POLITE_DELAY = 1.5  # seconds between archive.org requests


def _cdx(url: str, limit: int = None) -> list:
    """Returns CDX rows (dicts) for archived captures of `url`."""
    params = {
        "url": url,
        "output": "json",
        "filter": "statuscode:200",
    }
    if limit:
        params["limit"] = limit
    resp = requests.get(CDX_URL, params=params, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return []
    header, *rest = rows
    return [dict(zip(header, r)) for r in rest]


def _fetch_archived(timestamp: str, url: str, timeout: int = 90) -> bytes:
    """
    Downloads the raw archived body (the 'id_' modifier bypasses the Wayback
    toolbar) and transparently gunzips it.
    """
    resp = requests.get(
        RAW_URL.format(ts=timestamp, url=url),
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.content
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body


def _bars_from_dps_json(payload: bytes) -> pd.DataFrame:
    """
    Parses an archived /timeseries/eod payload: [[ts, close, volume, open], ...].

    High/Low are reconstructed as max/min(open, close) — the endpoint never
    carried intraday wicks — so these bars are flagged approximated downstream.
    """
    data = json.loads(payload)
    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        raise ValueError("archived payload has no 'data' rows")

    records = []
    for item in rows:
        try:
            ts, close, volume, open_p = item[0], float(item[1]), float(item[2]), float(item[3])
        except (TypeError, ValueError, IndexError):
            continue
        if close <= 0 or open_p <= 0:
            continue
        day = datetime.datetime.fromtimestamp(ts, tz=PKT_TZ).date()
        records.append({
            "Date": pd.Timestamp(day),
            "Open": round(open_p, 2),
            "High": round(max(open_p, close), 2),
            "Low": round(min(open_p, close), 2),
            "Close": round(close, 2),
            "Volume": int(volume) if volume > 0 else 0,
        })

    if not records:
        raise ValueError("no usable rows in archived payload")
    frame = pd.DataFrame(records).drop_duplicates("Date").set_index("Date").sort_index()
    return frame[["Open", "High", "Low", "Close", "Volume"]]


def backfill_symbol(symbol: str, dry_run: bool = False) -> dict:
    """Pulls the newest archived timeseries capture for one symbol into the cache."""
    symbol = symbol.strip().upper()
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    captures = [c for c in _cdx(url) if "json" in (c.get("mimetype") or "")]
    if not captures:
        return {"symbol": symbol, "status": "not_archived"}

    capture = captures[-1]
    time.sleep(POLITE_DELAY)
    try:
        frame = _bars_from_dps_json(_fetch_archived(capture["timestamp"], capture["original"]))
    except Exception as exc:
        return {"symbol": symbol, "status": "failed", "error": str(exc)}

    cache_path = os.path.join(CACHE_DIR, f"{symbol}_real.csv")
    added = replaced = 0
    if os.path.exists(cache_path):
        existing = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        existing.index.name = "Date"
        overlap = existing.index.intersection(frame.index)
        added = len(frame.index.difference(existing.index))
        replaced = len(overlap)
        merged = pd.concat([existing, frame])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        added = len(frame)
        merged = frame

    if not dry_run:
        merged.to_csv(cache_path)

    return {
        "symbol": symbol,
        "status": "ok",
        "capture": capture["timestamp"],
        "bars": len(frame),
        "first": frame.index[0].strftime("%Y-%m-%d"),
        "last": frame.index[-1].strftime("%Y-%m-%d"),
        "added": added,
        "replaced": replaced,
        "rows_total": len(merged),
    }


def backfill_snapshots(limit: int = 40, dry_run: bool = False) -> dict:
    """
    Replays archived market-summary pages: each capture is one official session
    snapshot (true OHLC) for every symbol listed on that date.
    """
    from psx_snapshot import parse_snapshot

    url = "https://www.psx.com.pk/market-summary/"
    captures = _cdx(url)
    captures = [c for c in captures if "html" in (c.get("mimetype") or "")]
    captures = captures[-limit:] if limit else captures

    sessions, rows_written, failures = 0, 0, 0
    for capture in captures:
        ts = capture["timestamp"]
        session_date = datetime.datetime.strptime(ts[:8], "%Y%m%d").date()
        time.sleep(POLITE_DELAY)
        try:
            page = _fetch_archived(ts, capture["original"]).decode("utf-8", "ignore")
            frame = parse_snapshot(page)
        except Exception:
            failures += 1
            continue
        if frame is None or frame.empty:
            failures += 1
            continue

        for row in frame.itertuples(index=False):
            cache_path = os.path.join(CACHE_DIR, f"{row.symbol}_real.csv")
            bar = pd.DataFrame(
                {
                    "Open": [float(row.open)],
                    "High": [float(row.high)],
                    "Low": [float(row.low)],
                    "Close": [float(row.close)],
                    "Volume": [int(row.volume)],
                },
                index=pd.DatetimeIndex([pd.Timestamp(session_date)], name="Date"),
            )
            try:
                if os.path.exists(cache_path):
                    existing = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                    existing.index.name = "Date"
                    if pd.Timestamp(session_date) in existing.index:
                        continue
                    merged = pd.concat([existing, bar]).sort_index()
                else:
                    merged = bar
                if not dry_run:
                    merged.to_csv(cache_path)
                rows_written += 1
            except Exception:
                continue
        sessions += 1

    return {"sessions": sessions, "rows_written": rows_written, "failures": failures}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backfill PSX history from the Internet Archive")
    parser.add_argument("--symbols", help="Comma-separated tickers for JSON backfill")
    parser.add_argument("--snapshots", type=int, default=0,
                        help="Also replay N archived market-summary snapshots (slow)")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args(argv)

    if not args.symbols and not args.snapshots:
        parser.error("provide --symbols and/or --snapshots")

    exit_code = 0
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        print(f"Archived timeseries backfill for {len(symbols)} symbol(s)"
              f"{' [dry run]' if args.dry_run else ''}")
        for symbol in symbols:
            try:
                result = backfill_symbol(symbol, dry_run=args.dry_run)
            except Exception as exc:
                print(f"  {symbol:9s} FAILED: {exc}")
                exit_code = 1
                continue

            if result["status"] == "not_archived":
                print(f"  {symbol:9s} no archived capture")
            elif result["status"] == "failed":
                print(f"  {symbol:9s} FAILED: {result.get('error')}")
                exit_code = 1
            else:
                print(
                    f"  {symbol:9s} {result['bars']:5d} bars {result['first']} -> {result['last']} "
                    f"(+{result['added']} new, {result['replaced']} replaced, capture {result['capture'][:8]})"
                )

    if args.snapshots:
        print(f"Replaying up to {args.snapshots} archived market-summary snapshots")
        summary = backfill_snapshots(limit=args.snapshots, dry_run=args.dry_run)
        print(
            f"  sessions={summary['sessions']} rows={summary['rows_written']} "
            f"failed={summary['failures']}"
        )

    if not args.dry_run:
        print("Backfill written to .cache — run 'python telegram_notifier.py --check-feeds' to verify.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
