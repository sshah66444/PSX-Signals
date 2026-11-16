"""
event_risk.py
The announcement / event-risk agent: a deterministic gate with veto rights.

Why this agent exists (and why only this one)
---------------------------------------------
The walk-forward showed the entry logic is indistinguishable from matched random
entries (p = 0.055) and that its results are regime-dependent. The one thing a
PSX book reacts to that technicals cannot see is the corporate calendar: results
days, board meetings, book closures, ex-dividend dates, right/bonus issues and
suspensions. Those produce overnight repricing that no ATR stop can defend.

This module is deliberately NOT an LLM. Every verdict is a rule over a stored row,
and every veto must cite the row it came from (symbol, type, date, headline, source).
If there is no event data for a symbol, the gate stays silent -- absence of data is
never treated as evidence of safety *or* danger.

Verdicts
--------
  CLEAR  -> no curated event inside the windows; the setup passes unchanged
  WATCH  -> event inside the escalate window: BUY_NOW / BUY_ZONE demoted to WATCH
  BLOCK  -> event inside the hard window (or a suspension): demoted to AVOID

Data lives in `.state/events.json` and is refreshed from the PSX announcements page
on a best-effort basis; it can also be seeded by hand (`--add`) when the feed is
unavailable. Nothing is fabricated: a failed refresh leaves the previous file intact.
"""

import argparse
import datetime
import hashlib
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, ".state")
EVENTS_PATH = os.path.join(STATE_DIR, "events.json")
# Append-only record of every veto the gate issued. Blocked setups never reach the
# trade ledger, so without this there would be no way to price them later.
GATE_LOG_PATH = os.path.join(STATE_DIR, "event_gate_log.jsonl")

PSX_SOURCES = (
    "https://dps.psx.com.pk/announcements",
    "https://www.psx.com.pk/announcements",
)
# The announcements page is a JS shell; the rows come from a form POST to this URL.
PSX_POST_URL = "https://dps.psx.com.pk/announcements"

# Per-type windows in calendar days. `block` = hard veto lead time, `escalate` =
# demote-to-WATCH lead time. -1 days of look-back means "also the session after",
# because results and book closures are usually announced after the close.
EVENT_TYPES = {
    "RESULTS":        {"block": 2, "escalate": 6, "label": "financial results"},
    "BOARD_MEETING":  {"block": 1, "escalate": 5, "label": "board meeting"},
    "BOOK_CLOSURE":   {"block": 2, "escalate": 6, "label": "share transfer book closure"},
    "EX_DIVIDEND":    {"block": 2, "escalate": 6, "label": "ex-dividend date"},
    "RIGHT_ISSUE":    {"block": 3, "escalate": 8, "label": "right issue"},
    "BONUS_ISSUE":    {"block": 3, "escalate": 8, "label": "bonus issue"},
    "SUSPENSION":     {"block": 30, "escalate": 60, "label": "suspension / delisting"},
    "MATERIAL_NOTICE": {"block": 1, "escalate": 4, "label": "material announcement"},
    "AGM":            {"block": 0, "escalate": 2, "label": "AGM / EOGM notice"},
    "OTHER":          {"block": 0, "escalate": 1, "label": "announcement"},
}
LOOKBACK_DAYS = 1     # the session after an event is still event risk (gap repricing)

_CLASSIFIERS = (
    ("SUSPENSION", r"suspend|delist"),
    ("BOOK_CLOSURE", r"book\s*closure|share transfer book"),
    ("EX_DIVIDEND", r"ex-?dividend|ex\s*date"),
    ("RIGHT_ISSUE", r"right\s*issue"),
    ("BONUS_ISSUE", r"bonus\s*(issue|shares)"),
    ("RESULTS", r"financial results|results for the (year|quarter|period)|consider\w*\s+(the\s+)?(annual\s+)?accounts"),
    ("BOARD_MEETING", r"board of directors.*meeting|board meeting"),
    ("AGM", r"\bagm\b|annual general meeting|eogm|extraordinary general meeting"),
    ("MATERIAL_NOTICE", r"material|disclosure|insider|sponsor"),
)

_DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"),
)


def classify(headline: str) -> str:
    """Maps an announcement subject onto one of EVENT_TYPES (deterministic)."""
    text = (headline or "").lower()
    for event_type, pattern in _CLASSIFIERS:
        if re.search(pattern, text):
            return event_type
    return "OTHER"


def _parse_date(text: str):
    """First date found in a cell, as ISO string (dd/mm/yyyy assumed for PSX)."""
    text = (text or "").strip()
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", text)
    if match:
        day, month, year = match.groups()
        year = year if len(year) == 4 else f"20{year}"
        try:
            return datetime.date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _event_key(event: dict) -> str:
    raw = "|".join(str(event.get(field, "")) for field in ("symbol", "type", "event_date", "headline"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _clean_event(event: dict) -> dict:
    cleaned = {
        "symbol": str(event.get("symbol", "")).upper().strip(),
        "type": str(event.get("type") or "OTHER").upper(),
        "event_date": str(event.get("event_date") or ""),
        "headline": html.unescape(str(event.get("headline", ""))).strip()[:300],
        "source": str(event.get("source", "psx"))[:60],
        "url": str(event.get("url", ""))[:400],
        "fetched_at": str(event.get("fetched_at") or datetime.date.today().isoformat()),
    }
    if cleaned["type"] not in EVENT_TYPES:
        cleaned["type"] = "OTHER"
    return cleaned


def _as_date(value):
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def load_events(path: str = None) -> dict:
    """Returns {'events': [...], 'updated_at', 'error'}; never raises."""
    path = path or EVENTS_PATH
    if not os.path.exists(path):
        return {"events": [], "updated_at": None, "error": None}
    try:
        with open(path, "r") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"events": [], "updated_at": None, "error": f"unreadable events file: {exc}"}
    if isinstance(payload, dict):
        raw_events = payload.get("events", [])
        updated_at = payload.get("updated_at")
    else:
        raw_events, updated_at = payload, None
    if not isinstance(raw_events, list):
        return {"events": [], "updated_at": updated_at, "error": "events payload is not a list"}
    return {
        "events": [_clean_event(event) for event in raw_events if isinstance(event, dict)],
        "updated_at": updated_at,
        "error": None,
    }


def save_events(events, path: str = None, max_rows: int = 5000) -> dict:
    """Atomic write: a failed save never leaves a half-written store behind."""
    path = path or EVENTS_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    rows = sorted((_clean_event(event) for event in events),
                  key=lambda event: (event["event_date"], event["symbol"]))
    if max_rows and len(rows) > max_rows:
        rows = rows[-max_rows:]
    payload = {
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "events": rows,
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as handle:
        json.dump(payload, handle, indent=1)
    os.replace(tmp_path, path)
    return payload


def upsert_events(new_events, path: str = None) -> dict:
    """Merges new rows into the store; identical rows are not duplicated."""
    existing = load_events(path)
    merged = {_event_key(event): event for event in existing["events"]}
    added = 0
    for event in new_events or []:
        cleaned = _clean_event(event)
        if not cleaned["symbol"] or not cleaned["event_date"]:
            continue
        key = _event_key(cleaned)
        if key not in merged:
            added += 1
        merged[key] = cleaned
    payload = save_events(list(merged.values()), path)
    return {"added": added, "total": len(payload["events"]), "updated_at": payload["updated_at"]}


def upcoming_events(events, symbol: str, as_of=None, horizon_days: int = 60) -> list:
    """Events for one symbol inside [-LOOKBACK_DAYS, horizon], nearest first."""
    as_of_date = _as_date(as_of) or datetime.date.today()
    wanted = str(symbol).upper()
    found = []
    for event in events or []:
        if event.get("symbol") != wanted:
            continue
        event_date = _as_date(event.get("event_date"))
        if event_date is None:
            continue
        days_ahead = (event_date - as_of_date).days
        if -LOOKBACK_DAYS <= days_ahead <= horizon_days:
            found.append({**event, "days_ahead": days_ahead})
    return sorted(found, key=lambda item: abs(item["days_ahead"]))


def assess(symbol: str, as_of=None, events=None, horizon_days: int = 60) -> dict:
    """
    Event-risk verdict for one symbol on one date.

    BLOCK inside the hard window (and the session after the event), WATCH inside the
    escalate window, CLEAR otherwise. No stored events -> CLEAR, with `events_known`
    telling the caller the store was empty so silence stays visible.
    """
    as_of_date = _as_date(as_of) or datetime.date.today()
    if events is None:
        events = load_events()["events"]

    blockers, warnings, cited = [], [], []
    for event in upcoming_events(events, symbol, as_of_date, horizon_days=horizon_days):
        spec = EVENT_TYPES[event["type"]]
        days_ahead = event["days_ahead"]
        cited.append({
            "type": event["type"],
            "event_date": event["event_date"],
            "headline": event["headline"],
            "source": event["source"],
            "url": event.get("url", ""),
            "days_ahead": days_ahead,
        })
        label = f"{spec['label']} on {event['event_date']} ({days_ahead:+d}d): {event['headline']}"
        if spec["block"] > 0 and -LOOKBACK_DAYS <= days_ahead <= spec["block"]:
            blockers.append(label)
        elif days_ahead <= spec["escalate"]:
            warnings.append(label)

    verdict = "BLOCK" if blockers else ("WATCH" if warnings else "CLEAR")
    return {
        "symbol": str(symbol).upper(),
        "as_of": as_of_date.isoformat(),
        "verdict": verdict,
        "risk_score": 100 if blockers else (60 if warnings else 0),
        "blockers": blockers,
        "warnings": warnings,
        "cited": cited,
        "events_known": len([e for e in events if e.get("symbol") == str(symbol).upper()]),
    }


def apply_event_gate(setup: dict, assessment: dict) -> dict:
    """
    Applies an event verdict to a setup. Pure: returns a copy, never mutates input.

    BLOCK demotes a tradeable setup to AVOID, WATCH demotes it to WATCH. The original
    state is preserved in `gate_downgraded_from`, so the ledger can tell "the engine
    liked it" apart from "the engine liked it and the calendar vetoed it".
    """
    gated = dict(setup or {})
    gated["event_risk"] = assessment or {"verdict": "CLEAR"}
    verdict = (assessment or {}).get("verdict", "CLEAR")
    if verdict not in ("BLOCK", "WATCH"):
        return gated

    reason = "; ".join((assessment.get("blockers") or assessment.get("warnings") or [])[:2])
    current = gated.get("state")
    if current in ("BUY_NOW", "BUY_ZONE"):
        gated["gate_downgraded_from"] = current
        gated["status"] = "WATCHING"
        gated["state"] = "AVOID" if verdict == "BLOCK" else "WATCH"
    gated["event_note"] = reason
    gated["blockers"] = list(gated.get("blockers") or []) + [f"event risk: {reason}"]
    note = gated.get("trigger_note") or ""
    gated["trigger_note"] = f"Event gate ({verdict}): {reason}. {note}".strip()
    return gated


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
_TICKER_RE = re.compile(r"^[A-Z]{2,6}$")
_NOT_TICKERS = {"PSX", "AGM", "EOGM", "THE", "LTD", "PDF", "LIST", "DATE", "COMPANY", "NOTICE"}


def parse_announcements(page_html: str, source: str = "psx", fetched_at: str = None) -> list:
    """
    Best-effort parser for the PSX announcements table.

    Structural rather than positional: any row with a date cell, a ticker-looking
    token and a text cell becomes an event. Returns [] instead of guessing when the
    page layout changes, so a stale store is never polluted with junk.
    """
    rows = []
    for row_html in _ROW_RE.findall(page_html or ""):
        cells = [
            re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", cell)).replace("\xa0", " ")).strip()
            for cell in _CELL_RE.findall(row_html)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        event_date = _parse_date(cells[0]) or _parse_date(" | ".join(cells))
        if not event_date:
            continue
        symbol = None
        for cell in cells:
            token = cell.split(" ")[0].upper()
            if _TICKER_RE.fullmatch(token) and token not in _NOT_TICKERS:
                symbol = token
                break
        if not symbol:
            continue
        headline = max(cells, key=len)
        if len(headline) < 8 or headline.upper() == symbol:
            continue
        href = _HREF_RE.search(row_html)
        rows.append(_clean_event({
            "symbol": symbol,
            "type": classify(headline),
            "event_date": event_date,
            "headline": headline,
            "source": source,
            "url": href.group(1) if href else "",
            "fetched_at": fetched_at or datetime.date.today().isoformat(),
        }))

    seen, unique = set(), []
    for row in rows:
        key = _event_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def fetch_announcements(timeout: int = 25) -> dict:
    """
    Best-effort fetch of the PSX announcements feed.

    PSX serves announcements as an HTML fragment from a POST to dps.psx.com.pk (the
    page itself is a JS shell), and it currently answers automated requests with 403.
    This function tries the documented form POST and then the plain page; when both
    fail it reports why and leaves the store untouched. The supported fallback is
    `import_file` (save the announcements page or export JSON) or `--add`.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a project dependency
        return {"events": [], "source": None, "error": f"requests unavailable: {exc}"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://dps.psx.com.pk/announcements",
    }
    payload = {
        "type": "C", "symbol": "", "query": "", "count": 100, "offset": 0,
        "date_from": "", "date_to": "", "page": "annc",
    }
    errors = []
    attempts = (("POST", PSX_POST_URL, payload), ("GET", PSX_SOURCES[0], None),
                ("GET", PSX_SOURCES[1], None))
    for method, url, body in attempts:
        try:
            if method == "POST":
                response = requests.post(url, data=body, headers=headers, timeout=timeout)
            else:
                response = requests.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            errors.append(f"{method} {url}: {exc}")
            continue
        if response.status_code != 200:
            errors.append(f"{method} {url}: HTTP {response.status_code}")
            continue
        parsed = parse_announcements(response.text, source=url)
        if parsed:
            return {"events": parsed, "source": url, "error": None}
        errors.append(f"{method} {url}: no announcement rows parsed")
    return {"events": [], "source": None, "error": " | ".join(errors)}


def import_file(path: str, event_path: str = None) -> dict:
    """
    Imports announcements from a saved HTML page or a JSON export.

    This is the supported path when PSX blocks automation: save the announcements
    page from a browser (or export the rows) and import it. Nothing is invented --
    an unrecognised file reports an error and adds nothing.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError as exc:
        return {"added": 0, "error": f"cannot read {path}: {exc}"}

    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            payload = json.loads(text)
        except ValueError as exc:
            return {"added": 0, "error": f"invalid JSON: {exc}"}
        rows = payload.get("events", payload) if isinstance(payload, dict) else payload
        rows = [row for row in rows if isinstance(row, dict)]
    else:
        rows = parse_announcements(text, source=os.path.basename(path))

    if not rows:
        return {"added": 0, "error": "no events recognised in file"}
    result = upsert_events(rows, event_path)
    result["error"] = None
    return result



def refresh_events(path: str = None, timeout: int = 25) -> dict:
    """Fetch and merge. A failed fetch leaves the existing store untouched."""
    fetched = fetch_announcements(timeout=timeout)
    if fetched.get("error"):
        return {"added": 0, "error": fetched["error"], "source": None}
    result = upsert_events(fetched["events"], path)
    result["source"] = fetched.get("source")
    result["error"] = None
    return result


def log_gate_decision(assessment: dict, original_state: str = None, path: str = None) -> bool:
    """
    Appends one veto to the gate log (JSONL). CLEAR verdicts are not logged: the log
    exists to price the trades the calendar blocked, and only vetoes block anything.
    """
    if not assessment or assessment.get("verdict") not in ("BLOCK", "WATCH"):
        return False
    path = path or GATE_LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    first = (assessment.get("cited") or [{}])[0]
    row = {
        "logged_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "as_of": assessment.get("as_of"),
        "symbol": assessment.get("symbol"),
        "verdict": assessment.get("verdict"),
        "original_state": original_state,
        "blockers": assessment.get("blockers") or [],
        "warnings": assessment.get("warnings") or [],
        "event_type": first.get("type"),
        "event_date": first.get("event_date"),
        "headline": first.get("headline"),
        "source": first.get("source"),
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def load_gate_log(path: str = None) -> list:
    """Reads the gate log, skipping malformed lines instead of failing."""
    path = path or GATE_LOG_PATH
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def format_assessment(assessment: dict) -> str:
    """Human-readable verdict, used by the CLI and the /events command."""
    lines = [
        f"{assessment.get('symbol')} — {assessment.get('verdict')} "
        f"(risk {assessment.get('risk_score', 0)}/100, as of {assessment.get('as_of')})"
    ]
    for blocker in assessment.get("blockers") or []:
        lines.append(f"  ⛔ {blocker}")
    for warning in assessment.get("warnings") or []:
        lines.append(f"  ⚠️ {warning}")
    if not assessment.get("cited"):
        lines.append("  no stored events inside the window")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Event-risk gate: refresh PSX announcements, inspect verdicts, seed events."
    )
    parser.add_argument("--path", help="Events store (default: .state/events.json)")
    parser.add_argument("--refresh", action="store_true", help="Fetch announcements and merge them")
    parser.add_argument("--import", dest="import_path", help="Import a saved announcements HTML page or JSON export")
    parser.add_argument("--check", nargs="*", help="Assess these symbols, e.g. --check OGDC MEBL")
    parser.add_argument("--add", nargs=3, metavar=("SYMBOL", "TYPE", "DATE"),
                        help="Seed one event, e.g. --add OGDC RESULTS 2026-11-20 --headline 'Board meeting'")
    parser.add_argument("--headline", default="Manually seeded event", help="Headline used by --add")
    parser.add_argument("--horizon", type=int, default=60, help="Assessment horizon in days")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    exit_code = 0
    if args.refresh:
        result = refresh_events(args.path)
        if result.get("error"):
            print(f"refresh failed (store untouched): {result['error']}")
            exit_code = 1
        else:
            print(f"announcements: {result['added']} added, {result['total']} stored "
                  f"from {result['source']}")

    if args.import_path:
        result = import_file(args.import_path, args.path)
        if result.get("error"):
            print(f"import failed (store untouched): {result['error']}")
            exit_code = 1
        else:
            print(f"imported {result['added']} new event(s); {result['total']} stored")

    if args.add:
        symbol, event_type, event_date = args.add
        event_type = event_type.upper()
        if event_type not in EVENT_TYPES:
            print(f"unknown type '{event_type}'. Choose from: {', '.join(EVENT_TYPES)}")
            return 2
        if _as_date(event_date) is None:
            print(f"invalid date '{event_date}' (expected YYYY-MM-DD)")
            return 2
        result = upsert_events([{
            "symbol": symbol, "type": event_type, "event_date": event_date,
            "headline": args.headline, "source": "manual",
        }], args.path)
        print(f"seeded {symbol} {event_type} {event_date}: {result['total']} events stored")

    if args.check:
        store = load_events(args.path)
        if store.get("error"):
            print(f"warning: {store['error']}")
        assessments = [assess(symbol, events=store["events"], horizon_days=args.horizon)
                       for symbol in args.check]
        if args.json:
            print(json.dumps(assessments, indent=2))
        else:
            for assessment in assessments:
                print(format_assessment(assessment))
                print()

    if not (args.refresh or args.add or args.check or args.import_path):
        store = load_events(args.path)
        print(f"events store: {len(store['events'])} rows "
              f"(updated {store.get('updated_at') or 'never'})")
        print("use --refresh, --add or --check")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())




