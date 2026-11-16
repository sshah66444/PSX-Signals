"""
walkforward.py
Out-of-sample validation for the PSX signal engine, with a matched random-entry
null model.

Why this exists
---------------
The live ledger showed 11 settled trades with -2.26%/trade expectancy, and the one
archetype producing all the flow (MEAN_REVERSION) has no demonstrated edge. Before
any parameter is touched, the honest questions are:

  1. Does the strategy beat *chance* entries with the same stop/target geometry?
  2. Does whatever it has hold up across separate regimes (folds), not just pooled?

Method
------
* History comes from the local caches (`.cache_2y/*.csv`, then `.cache/*_real.csv`).
  Nothing is fetched, nothing is synthesised: a symbol without verified OHLC is
  skipped and reported.
* Signals are generated bar-by-bar by `signal_engine.evaluate_bar_strategy` -- the
  exact function the screener and backtester use -- with a *per-date* market regime
  derived from the benchmark (a single "today's regime" would leak the future).
* Entries fill on the next bar's open; exits use the live Stage-1 profile: 50% at
  TP1, breakeven stop, trailing-EMA runner, time stop on no progress, holding cap.
* The null model repeats the same simulation for random entry dates on the same
  symbol, in the same fold, with the *same price distances* to stop/TP1/TP2. That
  isolates timing skill from risk geometry -- the only difference is when you enter.
* Folds are contiguous calendar windows. Parameters are not re-fitted per fold
  (nothing here is fitted), so each fold is an independent out-of-sample sample of
  a fixed rule set across a different regime. Stability across folds is the point.

Usage
-----
    python walkforward.py                       # 3 folds, 200 null iterations
    python walkforward.py --folds 4 --iterations 500 --mode swing_trade
"""

import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from data_engine import check_has_true_ohlc
from signal_engine import compute_all_indicators, evaluate_bar_strategy
from swing_config import MIN_TIME_STOP_MFE_R, PARTIAL_EXIT_FRACTION, get_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIRS = (os.path.join(BASE_DIR, ".cache_2y"), os.path.join(BASE_DIR, ".cache"))
FEE_PCT = 0.35          # round-trip commission + CDC/SECP charges, as in the backtester
WARMUP_BARS = 60        # indicators need history before a signal is meaningful
ENTRY_STATES = ("BUY_NOW", "BUY_ZONE")


def _clean_frame(df: pd.DataFrame):
    """Normalise a cached CSV: date index without tz, OHLCV columns, sorted."""
    if df is None or df.empty:
        return None
    frame = df.copy()
    if "Date" in frame.columns:
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
        frame = frame.dropna(subset=["Date"]).set_index("Date")
    index = pd.to_datetime(frame.index, errors="coerce", utc=True)
    frame.index = index.tz_convert(None) if getattr(index, "tz", None) is not None else index
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    if len(keep) < 5:
        return None
    frame = frame[keep].astype(float)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame if len(frame) >= WARMUP_BARS + 30 else None


def load_history(symbol: str, cache_dirs=None):
    """Loads a symbol's cached OHLC history (2-year cache preferred)."""
    for directory in (cache_dirs or CACHE_DIRS):
        for pattern in (f"{symbol}_2y.csv", f"{symbol}_real.csv", f"{symbol}*.csv"):
            for path in sorted(glob.glob(os.path.join(directory, pattern))):
                try:
                    frame = _clean_frame(pd.read_csv(path))
                except (OSError, ValueError, pd.errors.ParserError):
                    continue
                if frame is not None:
                    return frame
    return None


def list_cached_symbols(cache_dirs=None) -> list:
    """Symbols with cached history files."""
    found = set()
    for directory in (cache_dirs or CACHE_DIRS):
        if not os.path.isdir(directory):
            continue
        for path in glob.glob(os.path.join(directory, "*.csv")):
            name = os.path.basename(path).split("_")[0].split(".")[0].upper()
            if name and name.isalpha():
                found.add(name)
    return sorted(found)


def build_benchmark(frames: dict):
    """Equal-weight index built from the loaded symbols (PSX proxy)."""
    closes = []
    for frame in frames.values():
        series = frame["Close"].copy()
        series = series[series > 0]
        if len(series) < WARMUP_BARS:
            continue
        closes.append(series / float(series.iloc[0]))
    if not closes:
        return None
    joined = pd.concat(closes, axis=1).sort_index().ffill().dropna(how="all")
    index = joined.mean(axis=1)
    return pd.DataFrame(
        {"Close": index, "Open": index, "High": index, "Low": index, "Volume": 0.0},
        index=index.index,
    )


def _regime_at(close, ema20, ema50, ema200) -> dict:
    """Graded macro regime for one bar (mirrors the engine's gate vocabulary)."""
    if any(value is None or pd.isna(value) for value in (close, ema20, ema50)):
        return {"is_bullish": True, "regime": "UNKNOWN", "trend_state": None}
    if close > ema50 and ema20 > ema50:
        state = "bull"
    elif close > ema20:
        state = "early"
    elif ema200 is not None and not pd.isna(ema200) and close < ema200:
        state = "bear"
    else:
        state = "correction"
    return {"is_bullish": state in ("bull", "early"), "regime": state.upper(), "trend_state": state}


def regime_series(bench):
    """Per-date regime frame, so historical signals never see a future market state."""
    if bench is None or len(bench) < 60:
        return None
    close = bench["Close"].astype(float)
    frame = pd.DataFrame({"Close": close}, index=bench.index)
    for span in (20, 50, 200):
        frame[f"EMA_{span}"] = close.ewm(span=span, adjust=False).mean()
    return frame


def _regime_for_date(regimes, date) -> dict:
    if regimes is None or regimes.empty:
        return {}
    try:
        row = regimes.loc[:date].iloc[-1]
    except (IndexError, KeyError):
        return {}
    return _regime_at(
        float(row["Close"]), float(row["EMA_20"]), float(row["EMA_50"]),
        float(row["EMA_200"]) if not pd.isna(row["EMA_200"]) else None,
    )


def _finish(df, idx, exit_price, entry_price, entry_idx, p1_ret, reason, fee_pct):
    """Blends the TP1 partial with the runner and returns a trade result."""
    bars = int(idx - entry_idx)
    tail = (float(exit_price) - entry_price) / entry_price
    if p1_ret is None:
        gross = tail
    else:
        gross = PARTIAL_EXIT_FRACTION * p1_ret + (1 - PARTIAL_EXIT_FRACTION) * tail
    return {
        "exit_idx": int(idx),
        "exit_date": df.index[idx],
        "exit_price": round(float(exit_price), 2),
        "bars": bars,
        "gross_pct": round(gross * 100.0, 2),
        "net_pct": round(gross * 100.0 - fee_pct, 2),
        "reason": reason,
    }


def simulate_exit(df, entry_idx, entry_price, stop_price, tp1, tp2, cfg, ema=None,
                  fee_pct=FEE_PCT):
    """
    Simulates the live Stage-1 exit profile for one long position.

    stop -> TP1 (50% booked, stop to breakeven) -> trailing-EMA runner -> TP2, with
    a time stop for trades that never ran MIN_TIME_STOP_MFE_R and a hard holding cap.
    Conservative on ambiguity: if a bar touches both the stop and a target, the stop
    is assumed to fill first -- the same assumption the ledger and backtester make.
    """
    if df is None or entry_idx >= len(df) - 1 or not entry_price:
        return None
    risk = float(entry_price) - float(stop_price)
    if risk <= 0:
        return None

    stop = float(stop_price)
    time_stop = int(cfg.get("time_stop_bars", 15))
    hold_cap = int(cfg.get("holding_max_bars", 90))
    min_hold = int(cfg.get("min_hold_before_trail", 10))
    p1_ret = None
    tp1_idx = None
    mfe_r = 0.0
    last_idx = len(df) - 1
    final_idx = min(last_idx, entry_idx + hold_cap)

    for idx in range(entry_idx + 1, final_idx + 1):
        bar = df.iloc[idx]
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        bars = idx - entry_idx
        mfe_r = max(mfe_r, (high - float(entry_price)) / risk)

        # 1. Stop (or the ratcheted trail) always wins on an ambiguous bar.
        if low <= stop:
            reason = "TRAIL_STOP" if stop > float(entry_price) else "STOP"
            return _finish(df, idx, stop, float(entry_price), entry_idx, p1_ret, reason, fee_pct)

        # 2. TP1 books half and moves the stop to breakeven.
        if p1_ret is None and tp1 and high >= float(tp1):
            p1_ret = (float(tp1) - float(entry_price)) / float(entry_price)
            tp1_idx = idx
            stop = max(stop, float(entry_price))

        # 3. TP2 closes the runner.
        if p1_ret is not None and tp2 and high >= float(tp2):
            return _finish(df, idx, float(tp2), float(entry_price), entry_idx, p1_ret, "TP2", fee_pct)

        # 4. Runner trails the mode's EMA once the keep-before-trail window is done.
        if p1_ret is not None and ema is not None and (idx - tp1_idx) >= min_hold:
            trail = float(ema.iloc[idx])
            if trail > stop:
                stop = trail

        # 5. Time stop: no progress means the position is dead money.
        if p1_ret is None and bars >= time_stop and mfe_r < MIN_TIME_STOP_MFE_R:
            return _finish(df, idx, close, float(entry_price), entry_idx, p1_ret, "TIME_STOP", fee_pct)

        # 6. Hard holding cap.
        if bars >= hold_cap:
            return _finish(df, idx, close, float(entry_price), entry_idx, p1_ret, "HOLDING_CAP", fee_pct)

    # Still open at the end of the data: mark it, don't pretend it was a win.
    return _finish(df, final_idx, float(df.iloc[final_idx]["Close"]), float(entry_price),
                   entry_idx, p1_ret, "OPEN_MARK", fee_pct)


def load_frames(symbols=None, cache_dirs=None) -> dict:
    """Loads every requested symbol's history from cache (no network)."""
    wanted = [s.upper() for s in (symbols or list_cached_symbols(cache_dirs))]
    frames = {}
    for symbol in wanted:
        frame = load_history(symbol, cache_dirs)
        if frame is not None:
            frames[symbol] = frame
    return frames


def fold_bounds(start, end, folds: int) -> list:
    """Contiguous calendar windows covering [start, end]."""
    folds = max(1, int(folds))
    edges = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(),
                          periods=folds + 1)
    return [(edges[i], edges[i + 1]) for i in range(folds)]


def fold_of(date, bounds) -> int:
    for number, (lo, hi) in enumerate(bounds, start=1):
        if lo <= pd.Timestamp(date) <= hi:
            return number
    return len(bounds)


def gap_event_proxy(df, idx: int, lookback: int = 5, gap_pct: float = 0.05,
                    vol_mult: float = 2.5) -> bool:
    """
    PROXY for "something happened here": a >=5% session or >=2.5x volume in the last
    `lookback` bars. Cached history has no announcement archive, so this stands in for
    corporate news when measuring the gate. It is a market-reaction proxy, not the
    announcement agent -- label it that way in any report.
    """
    if df is None or idx < 26:
        return False
    closes = df["Close"].to_numpy(dtype=float)
    volumes = df["Volume"].to_numpy(dtype=float) if "Volume" in df else None
    start = max(1, idx - lookback + 1)
    for position in range(start, idx + 1):
        previous = closes[position - 1]
        if previous > 0 and abs(closes[position] / previous - 1.0) >= gap_pct:
            return True
    if volumes is not None:
        baseline = float(np.median(volumes[max(0, idx - 20):idx])) if idx > 0 else 0.0
        if baseline > 0 and np.any(volumes[start: idx + 1] >= vol_mult * baseline):
            return True
    return False


def signal_trades(frames: dict, mode: str = "swing_trade", bench=None, warmup: int = WARMUP_BARS,
                  event_filter=None):
    """
    Runs the live engine bar-by-bar and simulates the Stage-1 exits.

    One position per symbol at a time (the ledger's per-symbol dedupe, mirrored),
    entry on the next open, and unfillable >7.5% gap-ups skipped. When `event_filter`
    is given it is called as (symbol, date, frame, index) and a truthy result blocks
    the entry -- that is how the event gate is measured against the null model.
    """
    cfg = get_config(mode)
    regimes = regime_series(bench)
    ema_period = int(cfg["trailing_ema"])
    trades, skipped = [], []

    for symbol, df in frames.items():
        if not check_has_true_ohlc(df):
            skipped.append({"symbol": symbol, "reason": "approximated OHLC (no true High/Low)"})
            continue
        try:
            ind = compute_all_indicators(df, df_kse=bench, mode=mode)
        except Exception as exc:  # a broken series must not kill the study
            skipped.append({"symbol": symbol, "reason": f"indicators failed: {exc}"})
            continue
        ind = ind.dropna(subset=["Close"])
        ema = ind["Close"].ewm(span=ema_period, adjust=False).mean()
        blocked_until = -1

        for idx in range(warmup, len(ind) - 1):
            if idx <= blocked_until:
                continue
            setup = evaluate_bar_strategy(
                ind, bar_idx=idx,
                market_regime=_regime_for_date(regimes, ind.index[idx]),
                mode=mode,
            )
            if setup.get("state") not in ENTRY_STATES:
                continue
            if event_filter is not None and event_filter(symbol, ind.index[idx], ind, idx):
                continue
            entry_idx = idx + 1
            entry_price = float(ind.iloc[entry_idx]["Open"])
            prev_close = float(ind.iloc[idx]["Close"])
            if entry_price <= 0 or prev_close <= 0:
                continue
            if entry_price / prev_close - 1.0 >= 0.075:      # limit-up open: no fill
                continue
            if not all(setup.get(key) for key in ("stop_loss", "tp1", "tp2")):
                continue
            result = simulate_exit(ind, entry_idx, entry_price, setup["stop_loss"],
                                   setup["tp1"], setup["tp2"], cfg, ema=ema)
            if result is None:
                continue
            blocked_until = result["exit_idx"]
            trades.append({
                "symbol": symbol,
                "strategy": setup.get("strategy"),
                "state": setup.get("state"),
                "grade": setup.get("grade"),
                "score": setup.get("score"),
                "entry_date": pd.Timestamp(ind.index[entry_idx]),
                "entry_price": round(entry_price, 2),
                "stop_loss": float(setup["stop_loss"]),
                "tp1": float(setup["tp1"]),
                "tp2": float(setup["tp2"]),
                **result,
            })
    return trades, skipped


def matched_random_trades(trades, frames: dict, mode: str = "swing_trade", bounds=None,
                          rng=None):
    """
    One random entry per real trade: same symbol, same fold, same price distances.

    Only the timing changes, so a difference in expectancy is attributable to the
    signal rather than to risk geometry, holding period or symbol mix.
    """
    cfg = get_config(mode)
    ema_period = int(cfg["trailing_ema"])
    rng = rng or np.random.default_rng(7)
    bounds = bounds or fold_bounds(min(t["entry_date"] for t in trades),
                                   max(t["entry_date"] for t in trades), 1)
    # Per-symbol caches: rebuilding the EMA and the candidate date list for every
    # single trade made 200 null iterations take minutes. The RNG call order is
    # unchanged, so results are identical -- just far cheaper.
    ema_cache = {}
    position_cache = {}
    out = []
    for trade in trades:
        df = frames.get(trade["symbol"])
        if df is None or len(df) < WARMUP_BARS + 5:
            return None
        window_index = next(
            (i for i, window in enumerate(bounds) if window[0] <= trade["entry_date"] <= window[1]),
            len(bounds) - 1,
        )
        cache_key = (trade["symbol"], window_index)
        positions = position_cache.get(cache_key)
        if positions is None:
            low, high = bounds[window_index]
            positions = [pos for pos in range(WARMUP_BARS, len(df) - 2)
                         if low <= df.index[pos] <= high]
            position_cache[cache_key] = positions
        if not positions:
            return None
        entry_idx = int(rng.choice(positions))
        entry_price = float(df.iloc[entry_idx]["Open"])
        if entry_price <= 0:
            return None
        stop_dist = trade["entry_price"] - trade["stop_loss"]
        tp1_dist = trade["tp1"] - trade["entry_price"]
        tp2_dist = trade["tp2"] - trade["entry_price"]
        if stop_dist <= 0:
            return None
        ema = ema_cache.get(trade["symbol"])
        if ema is None:
            ema = df["Close"].ewm(span=ema_period, adjust=False).mean()
            ema_cache[trade["symbol"]] = ema
        result = simulate_exit(df, entry_idx, entry_price, entry_price - stop_dist,
                               entry_price + tp1_dist, entry_price + tp2_dist, cfg, ema=ema)
        if result is None:
            return None
        out.append({
            "symbol": trade["symbol"],
            "entry_date": pd.Timestamp(df.index[entry_idx]),
            "entry_price": round(entry_price, 2),
            "stop_loss": round(entry_price - stop_dist, 2),
            **result,
        })
    return out


def _trade_stats(trades):
    """Trade stats via attribution, so walk-forward numbers match /attribution."""
    from attribution import trade_stats

    if not trades:
        return trade_stats(None)
    frame = pd.DataFrame([{
        "id": f"{t['symbol']}-{pd.Timestamp(t['entry_date']).strftime('%Y%m%d')}",
        "symbol": t.get("symbol", ""),
        "net_pnl_pct": t["net_pct"],
        "triggered_date": pd.Timestamp(t["entry_date"]).strftime("%Y-%m-%d"),
        "resolved_date": pd.Timestamp(t["exit_date"]).strftime("%Y-%m-%d"),
        "entry_max": t["entry_price"],
        "stop_loss": t["stop_loss"],
        "initial_stop_loss": t["stop_loss"],
        "status": t["reason"],
    } for t in trades])
    return trade_stats(frame)


def walk_forward(frames=None, mode: str = "swing_trade", folds: int = 3, iterations: int = 200,
                 seed: int = 7, cache_dirs=None, symbols=None, event_filter=None,
                 event_label: str = None) -> dict:
    """
    Evaluates the fixed rule set across folds and against matched random entries.

    Returns a report dict with pooled stats, per-fold stats, the null distribution and
    a plain-language verdict. Nothing here re-fits parameters: the folds are regime
    samples of one fixed strategy, which is what the live bot actually runs.
    """
    frames = frames if frames is not None else load_frames(symbols, cache_dirs)
    if not frames:
        return {"error": "No cached history found. Run a scan first or point --cache at a folder."}

    bench = build_benchmark(frames)
    trades, skipped = signal_trades(frames, mode=mode, bench=bench, event_filter=event_filter)
    report = {
        "mode": mode,
        "folds": max(1, int(folds)),
        "iterations": max(1, int(iterations)),
        "symbols": sorted(frames),
        "skipped": skipped,
        "trades": trades,
        "event_gate": event_label,
        "bounds": [],
        "by_fold": [],
        "overall": _trade_stats([]),
        "real_expectancy_pct": None,
        "null_expectancy_pct": None,
        "null_p95_pct": None,
        "p_value": None,
        "null_iterations": 0,
        "edge": False,
        "verdict": "",
    }
    if not trades:
        report["verdict"] = "No signals fired in the cached history — nothing to validate."
        return report

    bounds = fold_bounds(min(t["entry_date"] for t in trades),
                         max(t["entry_date"] for t in trades), report["folds"])
    for trade in trades:
        trade["fold"] = fold_of(trade["entry_date"], bounds)
    report["bounds"] = bounds

    real = np.array([t["net_pct"] for t in trades], dtype=float)
    null_rows = []
    for iteration in range(report["iterations"]):
        rng = np.random.default_rng(seed + iteration)
        null = matched_random_trades(trades, frames, mode=mode, bounds=bounds, rng=rng)
        if not null or len(null) != len(real):
            continue
        null_rows.append(np.array([n["net_pct"] for n in null], dtype=float))

    real_exp = float(real.mean())
    report["overall"] = _trade_stats(trades)
    report["real_expectancy_pct"] = round(real_exp, 2)
    report["null_iterations"] = len(null_rows)

    if null_rows:
        matrix = np.vstack(null_rows)
        null_overall = matrix.mean(axis=1)
        report["null_expectancy_pct"] = round(float(null_overall.mean()), 2)
        report["null_p95_pct"] = round(float(np.percentile(null_overall, 95)), 2)
        report["p_value"] = round(
            float((np.sum(null_overall >= real_exp) + 1) / (len(null_overall) + 1)), 4
        )

    for number, (low, high) in enumerate(bounds, start=1):
        index = [i for i, t in enumerate(trades) if t["fold"] == number]
        fold_trades = [trades[i] for i in index]
        entry = {
            "fold": number,
            "start": pd.Timestamp(low).strftime("%Y-%m-%d"),
            "end": pd.Timestamp(high).strftime("%Y-%m-%d"),
            "stats": _trade_stats(fold_trades),
            "null_expectancy_pct": None,
        }
        if null_rows and index:
            entry["null_expectancy_pct"] = round(float(np.vstack(null_rows)[:, index].mean()), 2)
        report["by_fold"].append(entry)

    sample = report["overall"]["n"]
    p_value = report["p_value"]
    if sample < 30:
        verdict = f"Insufficient sample ({sample} trades) — every number below is noise."
    elif real_exp <= 0:
        verdict = f"No edge: expectancy is {real_exp:+.2f}%/trade before any comparison."
    elif p_value is not None and p_value <= 0.05:
        verdict = (f"Signal beats matched random entries (p={p_value}) at "
                   f"{report['overall']['expectancy_pct']:+.2f}%/trade.")
        report["edge"] = True
    else:
        verdict = f"Indistinguishable from matched random entries (p={p_value})."
    report["verdict"] = verdict
    return report


def format_report_text(report: dict) -> str:
    """Plain-text report for the terminal."""
    if report.get("error"):
        return f"Walk-forward aborted: {report['error']}"

    overall = report["overall"]
    lines = [
        "=" * 74,
        f"PSX walk-forward — mode={report['mode']}  folds={report['folds']}  "
        f"null iterations={report['null_iterations']}",
        f"symbols: {', '.join(report['symbols']) or '(none)'}",
    ]
    if report.get("event_gate"):
        lines.append(f"event gate: {report['event_gate']}")
    for skipped in report.get("skipped", []):
        lines.append(f"  skipped {skipped['symbol']}: {skipped['reason']}")
    lines.append("=" * 74)

    if report.get("real_expectancy_pct") is None:
        lines.append(report["verdict"])
        return "\n".join(lines)

    def stat_line(stats, null_exp=None):
        profit_factor = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "n/a"
        text = (f"n={stats['n']:<4} expectancy {stats['expectancy_pct']:+7.2f}%  "
                f"win {stats['win_rate_pct']:5.1f}%  PF {profit_factor:>5}  "
                f"maxDD {stats['max_dd_pct']:7.2f}%  hold {stats['avg_hold_days'] or 0:5.1f}d")
        if null_exp is not None:
            text += f"  | random {null_exp:+7.2f}%"
        return text

    lines.append("POOLED   " + stat_line(overall, report.get("null_expectancy_pct")))
    for fold in report["by_fold"]:
        lines.append(
            f"fold {fold['fold']} ({fold['start']} → {fold['end']})  "
            + stat_line(fold["stats"], fold["null_expectancy_pct"])
        )

    lines.append("-" * 74)
    if report.get("p_value") is not None:
        lines.append(
            f"reality check: signal {report['real_expectancy_pct']:+.2f}%/trade vs random "
            f"{report['null_expectancy_pct']:+.2f}%/trade (95th pct {report['null_p95_pct']:+.2f}%), "
            f"p={report['p_value']}"
        )
    lines.append(f"VERDICT: {report['verdict']}")
    lines.append("=" * 74)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Walk-forward validation with a matched random-entry null model."
    )
    parser.add_argument("--mode", default="swing_trade", help="Trading mode (default: swing_trade)")
    parser.add_argument("--folds", type=int, default=3, help="Contiguous calendar folds (default: 3)")
    parser.add_argument("--iterations", type=int, default=200,
                        help="Random-entry null iterations (default: 200)")
    parser.add_argument("--seed", type=int, default=7, help="Null-model RNG seed")
    parser.add_argument("--symbols", nargs="*", help="Limit to these symbols (default: all cached)")
    parser.add_argument("--cache", nargs="*", help="Override cache directories")
    parser.add_argument("--event-proxy", action="store_true",
                        help="Also run with a gap/volume event gate (proxy for announcements)")
    parser.add_argument("--json", help="Also write the full report to this JSON path")
    args = parser.parse_args(argv)

    if args.event_proxy:
        frames = load_frames(args.symbols, args.cache)
        base = walk_forward(frames=frames, mode=args.mode, folds=args.folds,
                            iterations=args.iterations, seed=args.seed)
        gated = walk_forward(
            frames=frames, mode=args.mode, folds=args.folds, iterations=args.iterations,
            seed=args.seed,
            event_filter=lambda symbol, date, frame, idx: gap_event_proxy(frame, idx),
            event_label="gap/volume proxy: >=5% session or >=2.5x volume in the last 5 bars",
        )
        print(format_report_text(base))
        print()
        print(format_report_text(gated))
        if base.get("error") or gated.get("error"):
            return 1
        print(
            f"\nEvent-gate comparison: {base['overall']['n']} trades -> {gated['overall']['n']} trades | "
            f"expectancy {base['overall']['expectancy_pct']:+.2f}% -> "
            f"{gated['overall']['expectancy_pct']:+.2f}% | "
            f"win {base['overall']['win_rate_pct']}% -> {gated['overall']['win_rate_pct']}%"
        )
        report = gated
    else:
        report = walk_forward(
            mode=args.mode, folds=args.folds, iterations=args.iterations,
            seed=args.seed, cache_dirs=args.cache, symbols=args.symbols,
        )
        print(format_report_text(report))

    if args.json and not report.get("error"):
        import json

        serialisable = {k: v for k, v in report.items() if k != "trades"}
        for fold in serialisable.get("by_fold", []):
            fold["stats"] = {k: v for k, v in fold["stats"].items() if k != "ledger_df"}
        serialisable["trades"] = [
            {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in trade.items()}
            for trade in report.get("trades", [])
        ]
        with open(args.json, "w") as handle:
            json.dump(serialisable, handle, indent=2, default=str)
        print(f"Full report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





