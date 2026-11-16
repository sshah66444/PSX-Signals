"""
attribution.py
Ledger attribution: the honest scorecard for the live PSX signal ledger.

Why this module exists
----------------------
The ledger recorded ~161 setups between Sept and Nov 2026 but only ~14 were ever
entered, and 3 of those sat "open" forever after TP1 because a TP1 row fell out of
the audit query. Any win rate computed over *all* rows therefore mixes two very
different populations:

  * signals that never triggered (no P&L at all), and
  * trades that were actually taken and settled.

Everything here reports those separately, and only computes expectancy / win rate /
profit factor over settled trades. Buckets smaller than MIN_SAMPLE_TRADES are
flagged rather than tuned -- 11 observations cannot justify a parameter change.

Excess return (net P&L minus the KSE-100 over the identical holding window) is the
headline number: in a market that rose 7%, a +4% trade lost 3% relative.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from signal_tracker import get_connection, trading_days_between
from data_engine import get_symbol_sector

# Fewer settled trades than this and any conclusion is noise.
MIN_SAMPLE_TRADES = 30
# Below this share of trades with a reconstructable R, the R column is suppressed.
MIN_R_COVERAGE_PCT = 50.0


def _num(value):
    """Float or None -- NaN counts as missing (it is 'unknown', not zero)."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(parsed) else parsed


def load_ledger(track: str = None) -> list:
    """All ledger rows (dicts), optionally filtered to one track."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if track:
            cursor.execute("SELECT * FROM signals WHERE track = ?", (track,))
        else:
            cursor.execute("SELECT * FROM signals")
        return [dict(row) for row in cursor.fetchall()]


def _holding_days(row) -> int:
    start = row.get("triggered_date") or row.get("created_date")
    end = row.get("resolved_date")
    if not start or not end:
        return 0
    return trading_days_between(start, end)


def _r_multiple(row):
    """R-multiple using the *initial* stop (survives the breakeven move at TP1)."""
    entry = _num(row.get("entry_max"))
    stop = _num(row.get("initial_stop_loss"))
    if stop is None:
        stop = _num(row.get("stop_loss"))
    net = _num(row.get("net_pnl_pct"))
    if entry is None or stop is None or net is None or entry <= 0:
        return None
    risk_pct = (entry - stop) / entry * 100.0
    if risk_pct <= 0.05:      # no stop distance left (row modified before Stage 0)
        return None
    return net / risk_pct


def trade_stats(df: pd.DataFrame) -> dict:
    """Statistics over settled trades only (rows carrying a net P&L)."""
    empty = {
        "n": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0, "expectancy_pct": 0.0,
        "median_pct": 0.0, "sum_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "profit_factor": None, "payoff": None, "best_pct": 0.0, "worst_pct": 0.0,
        "avg_hold_days": None, "avg_benchmark_pct": None, "avg_excess_pct": None,
        "beat_benchmark_pct": None, "avg_r": None, "r_coverage_pct": 0.0,
        "compound_pct": 0.0, "max_dd_pct": 0.0,
    }
    if df is None or df.empty:
        return empty

    pnl = pd.to_numeric(df["net_pnl_pct"], errors="coerce").dropna().astype(float)
    n = len(pnl)
    if n == 0:
        return empty

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    stats = dict(empty)
    stats.update({
        "n": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "expectancy_pct": round(float(pnl.mean()), 2),
        "median_pct": round(float(pnl.median()), 2),
        "sum_pct": round(float(pnl.sum()), 2),
        "avg_win_pct": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_pct": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "best_pct": round(float(pnl.max()), 2),
        "worst_pct": round(float(pnl.min()), 2),
    })

    gross_win = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    stats["profit_factor"] = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    if stats["avg_loss_pct"]:
        stats["payoff"] = round(stats["avg_win_pct"] / abs(stats["avg_loss_pct"]), 2)

    # Equity curve: every settled trade at full size, in the order it resolved.
    ordered = df[df["net_pnl_pct"].notna()].copy()
    if "resolved_date" in ordered.columns:
        ordered["_seq"] = ordered["resolved_date"].fillna("")
    else:
        ordered["_seq"] = ""
    ordered = ordered.sort_values(["_seq", "id"])
    equity = (1.0 + pd.to_numeric(ordered["net_pnl_pct"], errors="coerce").fillna(0) / 100.0).cumprod()
    if len(equity):
        stats["compound_pct"] = round((float(equity.iloc[-1]) - 1.0) * 100.0, 2)
        drawdown = (equity / equity.cummax()) - 1.0
        stats["max_dd_pct"] = round(float(drawdown.min()) * 100.0, 2)

    if "benchmark_pnl_pct" in df.columns:
        bench = pd.to_numeric(df["benchmark_pnl_pct"], errors="coerce").dropna()
        if len(bench):
            stats["avg_benchmark_pct"] = round(float(bench.mean()), 2)
    if "excess_pnl_pct" in df.columns:
        excess = pd.to_numeric(df["excess_pnl_pct"], errors="coerce").dropna()
        if len(excess):
            stats["avg_excess_pct"] = round(float(excess.mean()), 2)
            stats["beat_benchmark_pct"] = round(float((excess > 0).mean() * 100.0), 1)

    holds = [_holding_days(row) for _, row in df.iterrows()]
    holds = [d for d in holds if d > 0]
    if holds:
        stats["avg_hold_days"] = round(float(np.mean(holds)), 1)

    rs = [_r_multiple(row) for _, row in df.iterrows()]
    rs = [r for r in rs if r is not None]
    stats["r_coverage_pct"] = round(len(rs) / n * 100, 1) if n else 0.0
    if rs and stats["r_coverage_pct"] >= MIN_R_COVERAGE_PCT:
        stats["avg_r"] = round(float(np.mean(rs)), 2)

    return stats


def _bucket(df: pd.DataFrame, key: str, min_sample: int) -> list:
    """Per-group stats, largest groups first, each flagged if under sample."""
    if df is None or df.empty or key not in df.columns:
        return []
    rows = []
    for value, group in df.groupby(df[key].fillna("(unset)").astype(str)):
        stats = trade_stats(group)
        if stats["n"] == 0:
            continue
        rows.append({
            "key": value,
            "n": stats["n"],
            "win_rate_pct": stats["win_rate_pct"],
            "expectancy_pct": stats["expectancy_pct"],
            "avg_excess_pct": stats["avg_excess_pct"],
            "sum_pct": stats["sum_pct"],
            "insufficient": stats["n"] < min_sample,
        })
    return sorted(rows, key=lambda r: (-r["n"], r["key"]))


def build_attribution(rows, min_sample: int = MIN_SAMPLE_TRADES) -> dict:
    """Full attribution report from raw ledger rows (dicts or sqlite3.Row)."""
    df = pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
    result = {
        "counts": {
            "signals": 0, "entered": 0, "settled": 0, "open_runners": 0,
            "active": 0, "expired": 0, "invalidated": 0,
        },
        "overall": trade_stats(None),
        "buckets": {"strategy": [], "state": [], "grade": [], "sector": [], "track": []},
        "sample_ok": False,
        "min_sample": min_sample,
        "warnings": [],
        "has_benchmark": False,
    }
    if df.empty:
        result["warnings"].append("Ledger is empty: no signals recorded yet.")
        return result

    if "sector" not in df.columns:
        df["sector"] = df["symbol"].map(get_symbol_sector)

    status = df["status"].fillna("").astype(str)
    settled_mask = df["net_pnl_pct"].notna()
    entered_mask = df["triggered_date"].notna() | settled_mask
    settled = df[settled_mask]

    result["counts"] = {
        "signals": len(df),
        "entered": int(entered_mask.sum()),
        "settled": int(settled_mask.sum()),
        "open_runners": int(((status == "TP1_HIT") & ~settled_mask).sum()),
        "active": int(status.isin(["WATCHING", "TRIGGERED"]).sum()),
        "expired": int((status == "EXPIRED").sum()),
        "invalidated": int((status == "INVALIDATED").sum()),
    }
    overall = trade_stats(settled)
    result["overall"] = overall
    result["sample_ok"] = overall["n"] >= min_sample
    result["has_benchmark"] = "excess_pnl_pct" in df.columns and df["excess_pnl_pct"].notna().any()

    for key in ("strategy", "state", "grade", "sector", "track"):
        result["buckets"][key] = _bucket(settled, key, min_sample)

    warnings = result["warnings"]
    if overall["n"] < min_sample:
        warnings.append(
            f"Only {overall['n']} settled trade(s) — need {min_sample}+ before changing any parameter."
        )
    if result["counts"]["open_runners"]:
        warnings.append(
            f"{result['counts']['open_runners']} TP1 runner(s) never resolved — run finalize_partials()."
        )
    if result["counts"]["expired"]:
        warnings.append(
            f"{result['counts']['expired']} setup(s) expired without ever triggering "
            "(excluded from trade stats on purpose)."
        )
    if overall["n"] and overall["r_coverage_pct"] < MIN_R_COVERAGE_PCT:
        warnings.append(
            f"R-multiple hidden: an initial stop exists on only {overall['r_coverage_pct']}% of trades "
            "(rows written before Stage 0 overwrote the stop at TP1)."
        )
    if overall["n"] and not result["has_benchmark"]:
        warnings.append("No benchmark attached yet — run backfill_benchmark_returns().")
    if overall["avg_excess_pct"] is not None and overall["avg_excess_pct"] < 0:
        warnings.append(
            f"Negative excess return ({overall['avg_excess_pct']:+.2f}%): the engine underperformed "
            "KSE-100 over the same holding windows."
        )
    return result


def format_attribution_text(stats: dict, max_buckets: int = 6) -> str:
    """Telegram-ready HTML rendering of build_attribution() output."""
    counts = stats.get("counts", {})
    overall = stats.get("overall", {})
    lines = [
        "📐 <b>Ledger Attribution — settled trades only</b>",
        "<i>Never-triggered and expired setups are excluded from win rate and expectancy.</i>",
        "",
        f"Signals recorded: <b>{counts.get('signals', 0)}</b>",
        f"Entered: <b>{counts.get('entered', 0)}</b> | Settled: <b>{counts.get('settled', 0)}</b> | "
        f"Open runners: <b>{counts.get('open_runners', 0)}</b>",
    ]
    if counts.get("expired") or counts.get("invalidated"):
        lines.append(
            f"Never entered → expired <b>{counts.get('expired', 0)}</b>, "
            f"invalidated <b>{counts.get('invalidated', 0)}</b>"
        )
    lines.append("")

    if overall.get("n"):
        profit_factor = f"{overall['profit_factor']:.2f}" if overall.get("profit_factor") is not None else "—"
        lines += [
            "📊 <b>Settled trade stats</b>",
            f"Expectancy: <b>{overall['expectancy_pct']:+.2f}%</b>/trade over {overall['n']} trades",
            f"Win rate: <b>{overall['win_rate_pct']}%</b> ({overall['wins']}W / {overall['losses']}L)",
            f"Profit factor: <b>{profit_factor}</b>"
            + (f" | Payoff: {overall['payoff']:.2f}x" if overall.get("payoff") else ""),
            f"Best: {overall['best_pct']:+.2f}% | Worst: {overall['worst_pct']:+.2f}% | "
            f"Median: {overall['median_pct']:+.2f}%",
        ]
        if overall.get("avg_hold_days") is not None:
            lines.append(f"Avg hold: {overall['avg_hold_days']} trading days")
        lines.append(
            f"Full-size compounding: <b>{overall['compound_pct']:+.2f}%</b> | "
            f"Max DD: <b>{overall['max_dd_pct']:.2f}%</b>"
        )
        if overall.get("avg_r") is not None:
            lines.append(f"Avg R: <b>{overall['avg_r']:+.2f}R</b> (coverage {overall['r_coverage_pct']}%)")
        lines.append("")
        if overall.get("avg_excess_pct") is not None:
            lines += [
                "🎯 <b>vs benchmark (same holding windows)</b>",
                f"KSE-100: {overall['avg_benchmark_pct']:+.2f}% | "
                f"Excess: <b>{overall['avg_excess_pct']:+.2f}%</b>",
                f"Beat benchmark: {overall['beat_benchmark_pct']}% of trades",
                "",
            ]
    else:
        lines += ["No settled trades yet — nothing to attribute.", ""]

    if stats.get("warnings"):
        lines.append("⚠️ <b>Read this first</b>")
        lines += [f"• {warning}" for warning in stats["warnings"]]
        lines.append("")

    labels = (("Archetype", "strategy"), ("State", "state"), ("Grade", "grade"), ("Sector", "sector"))
    for label, key in labels:
        buckets = (stats.get("buckets") or {}).get(key) or []
        if not buckets:
            continue
        lines.append(f"📂 <b>By {label}</b>")
        for row in buckets[:max_buckets]:
            excess = (
                f", ex {row['avg_excess_pct']:+.2f}%" if row.get("avg_excess_pct") is not None else ""
            )
            flag = " ⚠️n&lt;30" if row["insufficient"] else ""
            trades = "trade" if row["n"] == 1 else "trades"
            lines.append(
                f"• {row['key']}: {row['n']} {trades}, {row['win_rate_pct']}% WR, "
                f"{row['expectancy_pct']:+.2f}%/trade{excess}{flag}"
            )
        if len(buckets) > max_buckets:
            lines.append(f"  … {len(buckets) - max_buckets} more bucket(s)")
        lines.append("")

    lines.append("<i>Attribution is measurement, not advice. ⚠️n&lt;30 means do not tune.</i>")
    return "\n".join(lines)
