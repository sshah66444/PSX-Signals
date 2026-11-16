#!/usr/bin/env python3
"""
refresh_cache.py
Downloads one uniform history window for the whole PSX watchlist into a cache folder.

Why: the walk-forward folds were partly comparing *data coverage* rather than market
regimes — 12 symbols had 2 years cached, the other 62 only about six months, so fold 3
held 314 of 450 trades. This refreshes every symbol over the same period using the
project's own data engine, which refuses to fabricate prices: a symbol that fails
validation is reported and skipped, never filled in.

Usage
-----
    python refresh_cache.py                      # 2y into .cache_2y
    python refresh_cache.py --period 1y --dir .cache_1y --force
    python refresh_cache.py --symbols OGDC MEBL
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from data_engine import fetch_psx_stock, get_watchlist

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(BASE_DIR, ".cache_2y")
MIN_BARS = 120


def cache_path(symbol: str, directory: str, period: str) -> str:
    return os.path.join(directory, f"{symbol}_{period}.csv")


def write_history(symbol: str, frame, directory: str, period: str) -> str:
    """Writes Date,OHLCV in the same shape the existing caches use."""
    os.makedirs(directory, exist_ok=True)
    path = cache_path(symbol, directory, period)
    out = frame.copy()
    out.index.name = "Date"
    columns = [column for column in ("Open", "High", "Low", "Close", "Volume") if column in out]
    out[columns].to_csv(path)
    return path


def refresh(period: str = "2y", directory: str = DEFAULT_DIR, symbols=None, force: bool = False,
            sleep: float = 0.6) -> dict:
    """Fetches and writes each symbol's history; returns a summary dict."""
    os.makedirs(directory, exist_ok=True)
    wanted = [symbol.upper() for symbol in (symbols or list(get_watchlist()))]
    written, skipped, existing = [], [], []

    for index, symbol in enumerate(wanted, start=1):
        path = cache_path(symbol, directory, period)
        if os.path.exists(path) and not force:
            existing.append(symbol)
            continue
        try:
            result = fetch_psx_stock(symbol, period=period)
        except Exception as exc:  # one bad symbol must not stop the refresh
            skipped.append({"symbol": symbol, "reason": f"fetch failed: {exc}"})
            continue
        frame = result.get("df")
        if result.get("status") != "OK" or frame is None or len(frame) < MIN_BARS:
            skipped.append({
                "symbol": symbol,
                "reason": result.get("error") or f"only {0 if frame is None else len(frame)} bars",
            })
            continue
        write_history(symbol, frame, directory, period)
        written.append({
            "symbol": symbol,
            "rows": len(frame),
            "from": frame.index[0].strftime("%Y-%m-%d"),
            "to": frame.index[-1].strftime("%Y-%m-%d"),
        })
        print(f"  [{index}/{len(wanted)}] {symbol}: {len(frame)} bars "
              f"{written[-1]['from']} → {written[-1]['to']}")
        if sleep:
            time.sleep(sleep)

    return {
        "directory": directory,
        "period": period,
        "written": written,
        "existing": existing,
        "skipped": skipped,
        "coverage": sorted({row["from"] for row in written})[:1],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh a uniform PSX history cache.")
    parser.add_argument("--period", default="2y", help="Yahoo period (default: 2y)")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="Cache directory (default: .cache_2y)")
    parser.add_argument("--symbols", nargs="*", help="Limit to these symbols (default: watchlist)")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist")
    parser.add_argument("--sleep", type=float, default=0.6, help="Pause between requests (seconds)")
    args = parser.parse_args(argv)

    summary = refresh(period=args.period, directory=args.dir, symbols=args.symbols,
                      force=args.force, sleep=args.sleep)
    print("=" * 70)
    print(f"written: {len(summary['written'])} | already cached: {len(summary['existing'])} | "
          f"skipped: {len(summary['skipped'])}")
    for skipped in summary["skipped"]:
        print(f"  skipped {skipped['symbol']}: {skipped['reason']}")
    print(f"cache: {summary['directory']}")
    return 0 if summary["written"] or summary["existing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
