#!/usr/bin/env python3
"""
import_broker_data.py
Imports a broker EOD export (CSV or Excel) into the screener's local price cache.

Brokers such as JS Global export daily OHLCV with their own column names and date
formats. This normalises any reasonable layout into the schema the signal engine
expects (.cache/{SYMBOL}_real.csv -> Date index + Open/High/Low/Close/Volume),
merges it with existing history, and marks the symbol as broker-sourced so the
engine prefers it over Yahoo.

Examples
--------
  # Inspect an unfamiliar export first
  python import_broker_data.py --file jsg_eod.csv --list-columns

  # Single symbol
  python import_broker_data.py --file jsg_eod.csv --symbol OGDC

  # A whole folder of per-symbol files (OGDC.csv, MEBL.csv, ...)
  python import_broker_data.py --dir ~/Downloads/jsg_exports

  # Excel workbook with a specific sheet, preview only
  python import_broker_data.py --file jsg_eod.xlsx --sheet EOD --symbol OGDC --dry-run
"""

from __future__ import annotations

import os
import re
import sys
import json
import glob
import argparse
import datetime

import pandas as pd

CACHE_DIR = os.environ.get("PSX_CACHE_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".cache"
)
os.makedirs(CACHE_DIR, exist_ok=True)

# role -> accepted header spellings (normalised, so punctuation/case do not matter)
COLUMN_ALIASES = {
    "date": {
        "date", "trade date", "trading date", "date time", "datetime", "dt", "day",
        "session", "session date", "time",
    },
    "open": {"open", "open price", "opening", "opening price", "o", "open rate"},
    "high": {"high", "high price", "day high", "h", "highest", "high rate"},
    "low": {"low", "low price", "day low", "l", "lowest", "low rate"},
    "close": {
        "close", "close price", "closing", "closing price", "last", "last price",
        "ldcp", "c", "price", "close rate", "market price",
    },
    "volume": {
        "volume", "vol", "volume traded", "traded volume", "shares", "shares traded",
        "turnover", "qty", "quantity",
    },
    "symbol": {
        "symbol", "ticker", "scrip", "script", "code", "security", "instrument",
        "symbol code", "company",
    },
}

REQUIRED_ROLES = ("date", "close")


def _norm(text) -> str:
    """Normalises a header/name for alias matching: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).strip().lower()).strip()


def detect_columns(columns) -> dict:
    """
    Maps logical roles to the actual column names found in the file.

    Returns e.g. {"date": "Trade Date", "close": "Close Price", ...}. Unknown
    columns are ignored; roles that cannot be found are simply absent.
    """
    normalised = {col: _norm(col) for col in columns}
    mapping = {}
    used = set()

    # Exact alias match first so "open price" is not stolen by a fuzzy rule
    for role, aliases in COLUMN_ALIASES.items():
        for col, norm in normalised.items():
            if col in used:
                continue
            if norm in aliases:
                mapping[role] = col
                used.add(col)
                break

    # Then a containment pass for headers like "Close Price (PKR)" / "Volume (Shares)"
    for role, aliases in COLUMN_ALIASES.items():
        if role in mapping:
            continue
        for col, norm in normalised.items():
            if col in used:
                continue
            if any(alias in norm.split() for alias in aliases):
                mapping[role] = col
                used.add(col)
                break

    return mapping


def _to_number(value):
    """Converts '1,234.50' / 'Rs. 1,234' / '-' into a float (NaN when unusable)."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("Rs.", "").replace("\u20a8", "")
    if text in {"-", "--", ""}:
        return float("nan")
    try:
        return float(text)
    except (TypeError, ValueError):
        return float("nan")


def parse_dates(series: pd.Series) -> pd.Series:
    """
    Parses the date column tolerantly.

    Pakistani exports are day-first (dd/mm/yyyy), which pandas would otherwise
    read as month-first, so dayfirst=True is used for ambiguous separators.
    Numeric epoch values (seconds or milliseconds) are also accepted.
    """
    raw = series.copy()

    numeric = pd.to_numeric(raw, errors="coerce")
    if numeric.notna().mean() > 0.8:
        sample = float(numeric.dropna().iloc[0])
        unit = "ms" if sample > 1e11 else "s"
        return pd.to_datetime(numeric, unit=unit, errors="coerce").dt.normalize()

    try:
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True)

    if parsed.isna().mean() > 0.2:
        for fmt in ("%d-%b-%Y", "%d-%b-%y", "%b %d, %Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y"):
            alt = pd.to_datetime(raw, errors="coerce", format=fmt)
            if alt.notna().mean() > parsed.notna().mean():
                parsed = alt
    return parsed.dt.normalize()



def load_table(path: str, sheet: str = None, header_row: int = 0) -> pd.DataFrame:
    """Loads a CSV/text/Excel export into a DataFrame, preserving raw headers."""
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            import openpyxl  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "Excel support needs openpyxl: pip install openpyxl  "
                "(or save the file as CSV and pass that instead)"
            ) from exc
        return pd.read_excel(path, sheet_name=sheet if sheet else 0, header=header_row)

    for sep in (",", ";", "\t", "|"):
        try:
            df = pd.read_csv(path, sep=sep, header=header_row, engine="python")
        except Exception:
            continue
        if df.shape[1] >= 3:
            return df
    return pd.read_csv(path, sep=",", header=header_row)


def _has_wicks(df: pd.DataFrame) -> bool:
    """True when bars carry genuine intraday excursions beyond open/close."""
    if df.empty:
        return False
    max_oc = df[["Open", "Close"]].max(axis=1)
    min_oc = df[["Open", "Close"]].min(axis=1)
    return bool((df["High"] > max_oc + 1e-6).any() or (df["Low"] < min_oc - 1e-6).any())


def normalise_frame(df: pd.DataFrame, symbol: str, colmap: dict = None) -> tuple:
    """
    Converts an arbitrary export into the engine's cache schema.

    Returns (frame, info) where frame has a DatetimeIndex named "Date" and the
    columns Open/High/Low/Close/Volume, and info records provenance flags
    (true_ohlc, rows dropped, date range).
    """
    colmap = colmap or detect_columns(df.columns)
    missing = [r for r in REQUIRED_ROLES if r not in colmap]
    if missing:
        raise ValueError(
            "Could not identify required column(s): "
            + ", ".join(missing)
            + f". Found columns: {list(df.columns)}. "
            "Run with --list-columns to inspect the file."
        )

    work = pd.DataFrame()
    work["Date"] = parse_dates(df[colmap["date"]])
    work["Close"] = df[colmap["close"]].map(_to_number)
    for role in ("open", "high", "low"):
        work[role.capitalize()] = (
            df[colmap[role]].map(_to_number) if role in colmap else float("nan")
        )
    work["Volume"] = df[colmap["volume"]].map(_to_number) if "volume" in colmap else 0.0

    rows_in = len(work)
    work = work.dropna(subset=["Date", "Close"])
    work = work[work["Close"] > 0]

    # Close-only exports derive flat bars; the engine treats such data as
    # approximated, so it can never trigger a live entry.
    synthetic_ohlc = bool(work[["Open", "High", "Low"]].isna().any(axis=1).mean() > 0.5)

    work["Open"] = work["Open"].fillna(work["Close"])
    work["High"] = work["High"].fillna(work[["Open", "Close"]].max(axis=1))
    work["Low"] = work["Low"].fillna(work[["Open", "Close"]].min(axis=1))

    # Guard against inverted ranges that would corrupt ATR and stop levels
    work["High"] = work[["Open", "Close", "High"]].max(axis=1)
    work["Low"] = work[["Open", "Close", "Low"]].min(axis=1)

    work = work.set_index("Date").sort_index()
    work = work[~work.index.duplicated(keep="last")]
    work["Volume"] = work["Volume"].fillna(0).astype("int64")
    work = work[["Open", "High", "Low", "Close", "Volume"]]

    info = {
        "symbol": symbol,
        "rows_in": rows_in,
        "rows_used": len(work),
        "first_date": work.index[0].strftime("%Y-%m-%d") if len(work) else None,
        "last_date": work.index[-1].strftime("%Y-%m-%d") if len(work) else None,
        "synthetic_ohlc": synthetic_ohlc,
        "has_true_ohlc": (not synthetic_ohlc) and _has_wicks(work),
    }
    return work, info


def infer_symbol(path: str, df: pd.DataFrame = None, colmap: dict = None) -> str:
    """
    Works out the ticker: a symbol column if present, otherwise the filename stem
    (OGDC.csv, OGDC_2026.csv, 2026-10-31_OGDC.csv).
    """
    if df is not None:
        colmap = colmap or detect_columns(df.columns)
        if "symbol" in colmap:
            values = df[colmap["symbol"]].astype(str).str.upper().str.strip()
            symbols = {v for v in values if re.fullmatch(r"[A-Z0-9]{2,10}", v)}
            if len(symbols) == 1:
                return symbols.pop()

    stem = os.path.splitext(os.path.basename(path))[0].upper()
    tokens = [t for t in re.split(r"[^A-Z0-9]+", stem) if t]
    candidates = [t for t in tokens if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", t)]
    if not candidates:
        raise ValueError(f"Cannot infer a symbol from filename '{os.path.basename(path)}'")
    # Prefer the longest token so '2026' or 'EOD' is not mistaken for a ticker
    candidates.sort(key=lambda t: (len(t), t), reverse=True)
    return candidates[0]


def merge_with_cache(df: pd.DataFrame, symbol: str, dry_run: bool = False) -> dict:
    """
    Merges the imported frame into .cache/{SYMBOL}_real.csv, newest value winning
    on overlapping dates. Never truncates existing history.
    """
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_real.csv")
    added = replaced = 0
    merged = df

    if os.path.exists(cache_path):
        try:
            existing = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            existing.index.name = "Date"
            overlap = existing.index.intersection(df.index)
            replaced = len(overlap)
            added = len(df.index.difference(existing.index))
            merged = pd.concat([existing, df])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        except Exception:
            merged = df
    else:
        added = len(df)

    if not dry_run:
        merged.to_csv(cache_path)
        marker = {
            "source": "broker_import",
            "symbol": symbol,
            "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "rows": int(len(merged)),
            "last_date": merged.index[-1].strftime("%Y-%m-%d") if len(merged) else None,
        }
        with open(os.path.join(CACHE_DIR, f"{symbol}_broker.json"), "w") as fh:
            json.dump(marker, fh, indent=2)

    return {
        "path": cache_path,
        "rows_total": len(merged),
        "rows_added": added,
        "rows_replaced": replaced,
        "last_date": merged.index[-1].strftime("%Y-%m-%d") if len(merged) else None,
    }


def import_file(path: str, symbol: str = None, sheet: str = None, header_row: int = 0,
                dry_run: bool = False, verbose: bool = True) -> dict:
    """Imports one export file end-to-end and returns a summary dict."""
    df = load_table(path, sheet=sheet, header_row=header_row)
    colmap = detect_columns(df.columns)
    sym = (symbol or infer_symbol(path, df, colmap)).upper()
    frame, info = normalise_frame(df, sym, colmap)
    if frame.empty:
        raise ValueError(f"No usable rows found in {path}")
    result = merge_with_cache(frame, sym, dry_run=dry_run)
    result.update(info)
    result["file"] = os.path.basename(path)

    if verbose:
        flag = "DRY RUN" if dry_run else "imported"
        print(
            f"  {sym:9s} {flag:7s} rows={result['rows_used']:5d} "
            f"added={result['rows_added']:4d} replaced={result['rows_replaced']:3d} "
            f"{result['first_date']} -> {result['last_date']} "
            f"trueOHLC={'yes' if info['has_true_ohlc'] else 'NO'}"
        )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import broker EOD exports into the screener cache")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Path to one export (.csv/.txt/.xlsx)")
    src.add_argument("--dir", help="Folder of exports (one symbol per file)")
    parser.add_argument("--symbol", help="Ticker override (e.g. OGDC)")
    parser.add_argument("--sheet", help="Excel sheet name (default: first sheet)")
    parser.add_argument("--header-row", type=int, default=0, help="Row index containing headers")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing")
    parser.add_argument("--list-columns", action="store_true", help="Show detected columns and exit")
    args = parser.parse_args(argv)

    if args.list_columns:
        if not args.file:
            print("--list-columns requires --file")
            return 2
        df = load_table(args.file, sheet=args.sheet, header_row=args.header_row)
        colmap = detect_columns(df.columns)
        print(f"File: {args.file}  rows={len(df)}  columns={len(df.columns)}")
        print("Headers:", list(df.columns))
        print("Detected mapping:")
        for role in ("date", "open", "high", "low", "close", "volume", "symbol"):
            print(f"  {role:7s} -> {colmap.get(role, '(not found)')}")
        print("\nFirst rows:")
        print(df.head(3).to_string())
        return 0

    if args.file:
        files = [args.file]
    else:
        files = sorted(
            p for ext in ("csv", "txt", "xlsx", "xlsm", "xls")
            for p in glob.glob(os.path.join(args.dir, f"*.{ext}"))
        )
    if not files:
        print("No export files found.")
        return 2

    print(f"Importing {len(files)} file(s) into {CACHE_DIR}")
    failures = 0
    for path in files:
        try:
            import_file(path, symbol=args.symbol if len(files) == 1 else None,
                        sheet=args.sheet, header_row=args.header_row, dry_run=args.dry_run)
        except Exception as exc:
            failures += 1
            print(f"  {os.path.basename(path):40s} FAILED: {exc}")

    print(f"Done. {len(files) - failures} imported, {failures} failed.")
    if not args.dry_run and len(files) > failures:
        print("The engine will now prefer this broker data for those symbols.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
