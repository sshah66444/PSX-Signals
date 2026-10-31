"""
telegram_notifier.py
Production PSX Telegram Bot with Actionable Cards, Lifecycle Follow-ups,
and Interactive Commands (/scan, /active, /stock, /why, /performance, /watchlist).
"""

import os
import sys
import time
import argparse
import requests
import datetime

# Ensure project modules can be imported
sys.path.insert(0, os.path.dirname(__file__))

# Auto-load .env if present
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from data_engine import (
    get_watchlist,
    fetch_psx_stock,
    fetch_kse100_index,
    build_synthetic_benchmark,
)
from signal_engine import generate_signal, format_actionable_card
from signal_tracker import (
    record_or_update_setup,
    audit_active_signals,
    get_active_signals,
    get_performance_summary,
)
from swing_config import get_config


TELEGRAM_TEXT_LIMIT = 3800        # Telegram rejects text > 4096 chars; keep headroom for entities
TELEGRAM_MAX_ATTEMPTS = 4         # Retries per chunk (rate limits + transient failures)
TELEGRAM_PACING_SECONDS = 1.1     # ~1 message/sec per chat stays under flood limits


def _split_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """
    Splits a long HTML message into Telegram-safe chunks on line boundaries.

    Telegram's sendMessage rejects anything above 4096 characters, which is why
    long watchlist/active-setup dumps used to disappear silently.
    """
    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for line in text.split("\n"):
        # A single line longer than the limit has to be hard-split
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _post_with_retry(url: str, payload: dict, timeout: int = 20) -> tuple[bool, str]:
    """
    POSTs one message payload, honouring Telegram's 429 retry_after and retrying
    transient network/5xx failures with exponential backoff.
    """
    last_error = "unknown error"
    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
        except Exception as exc:
            last_error = f"connection error: {exc}"
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 200:
            return True, ""
        if resp.status_code == 429:
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
            except Exception:
                retry_after = 5
            last_error = f"429 rate limited (retry_after={retry_after}s)"
            time.sleep(min(max(retry_after, 1), 60))
            continue
        if resp.status_code >= 500:
            last_error = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(2 * attempt)
            continue
        return False, f"{resp.status_code}: {resp.text[:200]}"

    return False, f"failed after {TELEGRAM_MAX_ATTEMPTS} attempts ({last_error})"


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Sends an HTML formatted message to Telegram, chunking and retrying as needed."""
    if not token or not chat_id or not str(text).strip():
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    delivered = True
    for chunk in _split_message(str(text)):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        ok, error = _post_with_retry(url, payload)
        if not ok:
            print(f"✗ Telegram delivery failed for chat {chat_id}: {error}")
            delivered = False
    return delivered


def broadcast_telegram_message(token: str, chat_ids_str: str, text: str) -> bool:
    """Delivers message to one or more recipients, groups, or channels (comma-separated)."""
    if not token or not chat_ids_str:
        return False
    recipients = [c.strip() for c in str(chat_ids_str).split(",") if c.strip()]
    success = True
    for idx, cid in enumerate(recipients):
        if idx:
            time.sleep(TELEGRAM_PACING_SECONDS)
        ok = send_telegram_message(token, cid, text)
        if not ok:
            success = False
    return success


def build_no_signals_note(is_bullish: bool) -> str:
    """
    Builds the fallback message for a scan cycle that found nothing to report.
    Only claims the market is in a correction when the KSE-100 regime actually
    says so -- a bull-market day can easily produce zero fresh triggers for
    reasons that have nothing to do with the broad market (RSI/R:R filters,
    nothing near resistance, etc.), so that case must not assert a correction
    that isn't happening.
    """
    if not is_bullish:
        return "ℹ️ <i>No new triggered setups today. Broad market remains in correction: standing aside to preserve cash.</i>"
    return "ℹ️ <i>No new triggered setups or active updates today. Existing positions held.</i>"


def run_daily_market_cycle(
    token: str,
    chat_id: str,
    symbols: list = None,
    dry_run: bool = False,
    strict_freshness: bool = True,
    mode: str = None,
) -> list[str]:
    """
    Executes the complete daily workflow:
    1. Fetches KSE-100 index and macro market regime.
    2. Evaluates session settlement (Strict Freshness Guard).
    3. Fetches real market data.
    4. Audits existing open signals (checks TP/SL hits, sends updates).
    5. Scans for new or triggered setups.
    6. Records them in signals.db without repeating unchanged setups.
    """
    # Get trading mode from environment or parameter
    # Default to swing_trade mode for all scans
    if mode is None:
        mode = os.environ.get("PSX_TRADING_MODE", "swing_trade")

    config = get_config(mode)
    print(f"Running in {config['name']} mode (time_stop: {config['time_stop_bars']} days, targets: {config['tp1_risk_multiplier']}x/{config['tp2_risk_multiplier']}x)")

    watchlist = get_watchlist()
    target_symbols = symbols if symbols else list(watchlist.keys())
    messages = []

    # 1. Fetch KSE-100 Index and Macro Market Regime
    print("1. Fetching KSE-100 index and macro market regime...")
    df_kse, regime_info = fetch_kse100_index()
    regime_name = regime_info.get("regime", "UNKNOWN")
    is_bullish = regime_info.get("is_bullish", True)
    kse_close = regime_info.get("close", 0.0)
    kse_ema50 = regime_info.get("ema50", 0.0)
    is_kse_settled = regime_info.get("is_settled", True)
    expected_sess_date = regime_info.get("expected_date", "")
    kse_last_date = regime_info.get("last_date", "")
    print(f"   KSE-100 Regime: {regime_name} (Close: {kse_close:,.0f} vs 50 EMA: {kse_ema50:,.0f}) | Settled: {is_kse_settled} (Expected: {expected_sess_date})")

    # Strict Session Freshness Guard:
    # If today's market session has concluded, but upstream data feeds (Yahoo Finance) have not yet published
    # today's closing settlement candle, pause to prevent emitting buy signals on stale prior-session data.
    if strict_freshness and not is_kse_settled:
        today_str = datetime.date.today().strftime("%d-%b-%Y")
        pending_msg = (
            f"⏳ <b>PSX AlphaSignals — EOD Settlement Pending</b>\n"
            f"📅 Date: <b>{today_str}</b>\n"
            f"────────────────────────\n"
            f"The PSX market session for today (<b>{expected_sess_date}</b>) has concluded, "
            f"but upstream data feeds (Yahoo Finance) have not yet finalized today's closing settlement.\n\n"
            f"• Latest available session: <b>{kse_last_date}</b>\n"
            f"• Daily scan paused to protect execution integrity (no trades on stale data).\n"
            f"• Scans will evaluate once today's closing candles are ingested (typically by 7:30 PM PKT).\n\n"
            f"<i>Execution integrity guard active: no stale trades emitted.</i>"
        )
        print(f"   [FRESHNESS GUARD] Session {expected_sess_date} pending EOD settlement (latest bar: {kse_last_date}). Halting daily scan.")
        if not dry_run and token and chat_id:
            broadcast_telegram_message(token, chat_id, pending_msg)
        return [pending_msg]

    print(f"2. Fetching real market data for {len(target_symbols)} symbols...")
    market_data = {}
    skipped_symbols = []

    for sym in target_symbols:
        res = fetch_psx_stock(sym, period="6mo")
        if res.get("status") == "OK":
            market_data[sym] = res
        else:
            skipped_symbols.append((sym, res.get("error", "Data unavailable")))

    # 1b. Benchmark fallback. PSX retired the public KSE-100 history endpoint, so a
    # stale/missing official index would gate every breakout on month-old data.
    # Build an equal-weight proxy from the constituent data we just fetched.
    if df_kse is None or regime_info.get("benchmark_stale"):
        df_synth, regime_synth = build_synthetic_benchmark(market_data)
        if df_synth is not None and len(df_synth) >= 50:
            df_kse, regime_info = df_synth, regime_synth
            regime_name = regime_info.get("regime", "UNKNOWN")
            is_bullish = regime_info.get("is_bullish", True)
            kse_close = regime_info.get("close", 0.0)
            kse_ema50 = regime_info.get("ema50", 0.0)
            is_kse_settled = regime_info.get("is_settled", True)
            expected_sess_date = regime_info.get("expected_date", "")
            kse_last_date = regime_info.get("last_date", "")
            print(
                f"   Benchmark: PSX index feed unavailable -> equal-weight proxy "
                f"({regime_info.get('constituents')} names, {regime_name}, last {kse_last_date})"
            )

    # 3. Audit existing open setups. Collected only -- broadcasting happens
    # after the header is built (see step 6), so subscribers always see the
    # summary banner before the detail alerts it summarizes, not after.
    print("3. Auditing open setups in signals.db...")
    lifecycle_updates = audit_active_signals(market_data)
    for upd in lifecycle_updates:
        messages.append(upd["text"])

    # 4. Evaluate setups for new/updated triggers. Collected only, same reason.
    print("4. Evaluating setups across watchlist...")
    new_setup_cards = []
    state_counts = {"BUY_NOW": 0, "BUY_ZONE": 0, "WATCH": 0, "AVOID": 0}
    for sym, m_info in market_data.items():
        df = m_info["df"]
        setup = generate_signal(sym, df, data_meta=m_info, df_kse=df_kse, market_regime=regime_info, mode=mode)
        state = setup.get("state")
        state_counts[state] = state_counts.get(state, 0) + 1

        # Only tradeable states are delivered. WATCH stays in the dashboard/ledger
        # so the chat is not flooded with "still waiting" noise.
        if state in ("BUY_NOW", "BUY_ZONE"):
            if dry_run:
                # A dry run must never mutate the live ledger: otherwise the real
                # scan that follows sees the setup as "already active" and stays
                # silent, silently swallowing the alert.
                is_new_or_updated = True
            else:
                is_new_or_updated, note = record_or_update_setup(setup, sym)
            if is_new_or_updated:
                comp_name = watchlist.get(sym, {}).get("name", sym)
                card = format_actionable_card(setup, company_name=comp_name, mode=mode)
                new_setup_cards.append(card)
    print(
        "   Signal states: "
        f"BUY_NOW={state_counts.get('BUY_NOW', 0)} "
        f"BUY_ZONE={state_counts.get('BUY_ZONE', 0)} "
        f"WATCH={state_counts.get('WATCH', 0)} "
        f"AVOID={state_counts.get('AVOID', 0)}"
    )

    # 5. Summary header
    today_str = datetime.date.today().strftime("%d-%b-%Y")
    regime_badge = "🟢 BULL MARKET" if is_bullish else "🔴 MARKET CORRECTION (Cash Preservation Mode)"
    if regime_info.get("benchmark_stale"):
        regime_badge = "⚪ BENCHMARK STALE (Macro Gate Neutral)"

    if regime_info.get("benchmark_source") == "synthetic_equal_weight":
        bench_line = (
            f"📈 <b>Market Proxy</b> (equal-weight, {regime_info.get('constituents')} names): "
            f"<b>{kse_close:,.2f}</b> | 50 EMA: <b>{kse_ema50:,.2f}</b>\n"
        )
    else:
        bench_line = f"📈 KSE-100: <b>{kse_close:,.2f}</b> | 50 EMA: <b>{kse_ema50:,.2f}</b>\n"

    header = (
        f"📊 <b>PSX AlphaSignals Daily Market Scan</b>\n"
        f"📅 Date: <b>{today_str}</b>\n"
        f"🏛️ <b>Macro Regime:</b> {regime_badge}\n"
        f"{bench_line}"
        f"<i>{regime_info.get('note', '')}</i>\n"
        f"────────────────────────\n"
        f"Audited active signals: {len(lifecycle_updates)} updates.\n"
        f"🟢 BUY NOW: {state_counts.get('BUY_NOW', 0)} | 🟡 BUY ZONE: {state_counts.get('BUY_ZONE', 0)} | ⚪ WATCH: {state_counts.get('WATCH', 0)}\n"
        f"New alerts in this scan: {len(new_setup_cards)}.\n"
    )
    if skipped_symbols:
        header += f"<i>Note: {len(skipped_symbols)} symbols skipped due to data unavailability.</i>\n"

    no_signals_note = None
    if len(new_setup_cards) == 0 and len(lifecycle_updates) == 0:
        no_signals_note = build_no_signals_note(is_bullish)

    # Prepend header, then every detail message, in the exact order they will
    # be broadcast below -- so the returned list (used for the dry-run
    # preview) always matches what a live subscriber actually receives.
    messages.insert(0, header)
    messages.extend(new_setup_cards)
    if no_signals_note:
        messages.append(no_signals_note)

    # 6. Broadcast in banner-first order: header, then lifecycle updates, then
    # new setup cards, then the no-signals fallback if there was nothing else.
    # Each message is paced so a 40-60 alert evening does not trip Telegram's
    # flood control (previously the tail of the batch was dropped silently).
    if not dry_run and token and chat_id:
        outbox = [header]
        outbox.extend([upd["text"] for upd in lifecycle_updates])
        outbox.extend(new_setup_cards)
        if no_signals_note:
            outbox.append(no_signals_note)

        delivered = 0
        for idx, item in enumerate(outbox):
            if idx:
                time.sleep(TELEGRAM_PACING_SECONDS)
            if broadcast_telegram_message(token, chat_id, item):
                delivered += 1
        print(f"   Telegram delivery: {delivered}/{len(outbox)} messages sent successfully.")

    return messages


def handle_interactive_command(token: str, chat_id: str, cmd_text: str):
    """
    Processes interactive user commands sent via Telegram.
    """
    parts = cmd_text.strip().split()
    cmd = parts[0].lower()
    watchlist = get_watchlist()

    if cmd in ["/start", "/help"]:
        reply = (
            "🤖 <b>PSX AlphaSignals Command Center</b>\n\n"
            "Available commands:\n"
            "• <b>/scan</b> — Request an immediate fresh scan across the PSX\n"
            "• <b>/active</b> — View currently active & watching setups\n"
            "• <b>/stock &lt;SYM&gt;</b> — View full checklist and levels for a stock (e.g. <code>/stock OGDC</code>)\n"
            "• <b>/why &lt;SYM&gt;</b> — Detailed pass/fail reasoning for a stock\n"
            "• <b>/performance</b> — Audited track record from the live signal ledger\n"
            f"• <b>/watchlist</b> — List all {len(watchlist)} tracked PSX equities\n"
        )
        send_telegram_message(token, chat_id, reply)

    elif cmd == "/watchlist":
        lines = [f"• <b>${s}</b>: {info['name']} ({info['sector']})" for s, info in watchlist.items()]
        reply = "📋 <b>Tracked PSX Watchlist:</b>\n" + "\n".join(lines)
        send_telegram_message(token, chat_id, reply)

    elif cmd == "/active":
        track_arg = parts[1].lower() if len(parts) > 1 and parts[1].lower() in ["swing", "tactical"] else None
        active_list = get_active_signals(track=track_arg)
        if not active_list:
            track_label = f" in {track_arg.upper()} track" if track_arg else ""
            reply = f"ℹ️ No currently active setups{track_label} in the ledger. Send /scan to run a fresh evaluation."
        else:
            cards = []
            for s in active_list:
                sym = s["symbol"]
                t_label = (s.get("track") or ("swing" if s["strategy"] in ["BREAKOUT", "PULLBACK"] else "tactical")).upper()
                card = (
                    f"• <b>${sym}</b> [{t_label}] ({s['status']})\n"
                    f"  Strategy: {s['strategy']} | Entry: {s['entry_min']:.2f}–{s['entry_max']:.2f}\n"
                    f"  SL: {s['stop_loss']:.2f} | TP1: {s['tp1']:.2f} | TP2: {s['tp2']:.2f}\n"
                )
                cards.append(card)
            header_str = f"⚡ <b>Active & Watching Setups ({track_arg.upper()} TRACK):</b>\n\n" if track_arg else "⚡ <b>Active & Watching Setups:</b>\n\n"
            reply = header_str + "\n".join(cards)
        send_telegram_message(token, chat_id, reply)

    elif cmd == "/performance":
        track_arg = parts[1].lower() if len(parts) > 1 and parts[1].lower() in ["swing", "tactical"] else None
        summary = get_performance_summary(track=track_arg)
        header_title = f"Audited Live Performance Ledger ({track_arg.upper()} TRACK)" if track_arg else "Audited Live Performance Ledger"
        if summary.get("closed_count", 0) == 0:
            reply = (
                f"📊 <b>{header_title}</b>\n\n"
                f"Total setups recorded: <b>{summary['total_recorded']}</b>\n"
                f"Active / In-progress: <b>{summary['active_count']}</b>\n"
                f"Closed trades: <b>0</b> (waiting for resolutions).\n\n"
                "<i>Ledger tracks real signals issued by this bot, completely separate from historical backtests.</i>"
            )
        else:
            reply = (
                f"📊 <b>{header_title}</b>\n\n"
                f"Total Closed Trades: <b>{summary['closed_count']}</b>\n"
                f"Wins: <b>{summary['wins']}</b> | Losses: <b>{summary['losses']}</b>\n"
                f"Win Rate: <b>{summary['win_rate_pct']}%</b>\n"
                f"Total Net P&L: <b>{summary['net_pnl_pct']:+.2f}%</b> (after costs)\n"
                f"Avg Trade P&L: <b>{summary['avg_trade_pnl']:+.2f}%</b>\n"
            )
            if not track_arg and "swing" in summary and "tactical" in summary:
                s_sum = summary["swing"]
                t_sum = summary["tactical"]
                reply += (
                    f"\n<b>• Swing Track (Breakout/Pullback):</b> "
                    f"{s_sum['closed_count']} closed, {s_sum['win_rate_pct']}% WR, {s_sum['net_pnl_pct']:+.2f}% net\n"
                    f"<b>• Tactical Track (Mean Reversion):</b> "
                    f"{t_sum['closed_count']} closed, {t_sum['win_rate_pct']}% WR, {t_sum['net_pnl_pct']:+.2f}% net\n"
                    f"\n<i>Tip: Use <code>/performance swing</code> or <code>/performance tactical</code> for filtered views.</i>"
                )
        send_telegram_message(token, chat_id, reply)

    elif cmd in ["/stock", "/why"]:
        if len(parts) < 2:
            send_telegram_message(token, chat_id, f"Please specify a symbol, e.g. <code>{cmd} OGDC</code>")
            return
        sym = parts[1].upper().replace("$", "")
        res = fetch_psx_stock(sym, period="6mo")
        if res.get("status") != "OK":
            send_telegram_message(token, chat_id, f"❌ Data unavailable for ${sym}: {res.get('error')}")
            return

        df_kse, regime_info = fetch_kse100_index()
        setup = generate_signal(sym, res["df"], data_meta=res, df_kse=df_kse, market_regime=regime_info)
        comp_name = watchlist.get(sym, {}).get("name", sym)

        if cmd == "/why":
            checklist = setup.get("checklist", {})
            chk_text = "\n".join([f"  {'[✓]' if v else '[✗]'} {k}" for k, v in checklist.items()])
            reply = (
                f"🔍 <b>Condition Breakdown for ${sym}</b>\n"
                f"Strategy: <b>{setup.get('strategy')}</b> | Status: <b>{setup.get('status')}</b>\n\n"
                f"<b>Checklist:</b>\n{chk_text}\n\n"
                f"<b>Note:</b> {setup.get('trigger_note')}\n"
                f"RSI: {setup.get('rsi')} | Vol/20MA: {setup.get('vol_ratio')}x"
            )
            send_telegram_message(token, chat_id, reply)
        else:
            card = format_actionable_card(setup, company_name=comp_name)
            send_telegram_message(token, chat_id, card)

    elif cmd == "/scan":
        send_telegram_message(token, chat_id, "🔄 Running fresh market scan...")
        run_daily_market_cycle(token, chat_id)


def poll_incoming_commands(token: str, chat_id: str):
    """
    Checks for any pending interactive commands from the user and replies.
    """
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return
        data = resp.json()
        results = data.get("result", [])
        if not results:
            return

        authorized_ids = [c.strip() for c in str(chat_id).split(",") if c.strip()] if chat_id else []
        max_update_id = 0
        for item in results:
            upd_id = item.get("update_id", 0)
            if upd_id > max_update_id:
                max_update_id = upd_id

            msg = item.get("message", {})
            sender_chat_id = str(msg.get("chat", {}).get("id"))
            text = msg.get("text", "")

            # Security: Allow if sender is in authorized_ids list, or if chat_id contains '*'
            is_authorized = not authorized_ids or "*" in authorized_ids or sender_chat_id in authorized_ids
            if is_authorized and text.startswith("/"):
                print(f"Processing command from user {sender_chat_id}: {text}")
                handle_interactive_command(token, sender_chat_id, text)

        # Clear processed updates
        if max_update_id > 0:
            requests.get(f"{url}?offset={max_update_id + 1}", timeout=5)
    except Exception as e:
        print(f"Error checking commands: {e}")


def main():
    parser = argparse.ArgumentParser(description="PSX Telegram Notifier & Lifecycle Engine")
    parser.add_argument("--symbol", type=str, help="Specific symbol to scan")
    parser.add_argument("--dry-run", action="store_true", help="Print output without delivering to Telegram")
    parser.add_argument("--check-commands", action="store_true", help="Poll and execute interactive Telegram commands")
    parser.add_argument("--test", action="store_true", help="Send a single diagnostic message to Telegram and exit")
    parser.add_argument("--check-feeds", action="store_true", help="Report data-feed health (Yahoo, PSX DPS, benchmark) and exit")
    parser.add_argument("--allow-stale", action="store_true", default=True, help="Bypass strict session freshness guard (default: on, so a one-session feed lag still delivers alerts with a pending notice)")
    parser.add_argument("--strict-freshness", action="store_true", help="Halt the scan when today's EOD bar has not settled yet")
    parser.add_argument("--mode", type=str, default=None, choices=["day_trade", "swing_trade", "position_trade"],
                       help="Trading mode (default: swing_trade or PSX_TRADING_MODE env var)")
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if args.check_feeds:
        from data_engine import dps_is_retired, _fetch_from_psx_dps

        print("=== DATA FEED HEALTH ===")
        probes = ["OGDC", "MEBL", "LUCK"]
        frames = {}
        for sym in probes:
            res = fetch_psx_stock(sym, period="6mo")
            status = res.get("status")
            print(f"Yahoo {sym:6s}: {status:11s} last={res.get('last_date')} source={res.get('source')}")
            if status == "OK":
                frames[sym] = res

        try:
            df_dps = _fetch_from_psx_dps("OGDC")
            print(f"PSX DPS: {'OK rows=' + str(len(df_dps)) if df_dps is not None else 'unavailable'}")
        except Exception as exc:
            print(f"PSX DPS: unavailable -> {exc}")
        print(f"PSX DPS retired flag: {dps_is_retired()}")

        print(f"Official KSE-100: {'available' if not fetch_kse100_index()[1].get('benchmark_stale') else 'stale/unavailable'}")
        bench, reg = build_synthetic_benchmark(frames)
        if bench is not None:
            print(
                f"Synthetic benchmark: OK ({reg.get('constituents')} probes, last {reg.get('last_date')}, "
                f"regime {reg.get('regime')})"
            )
        else:
            print("Synthetic benchmark: unavailable (needs >= 10 symbols with data)")
        return

    if args.test:
        if not token or not chat_id:
            print("✗ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from environment or .env")
            sys.exit(1)
        test_mode = args.mode or os.environ.get("PSX_TRADING_MODE", "swing_trade")
        cfg = get_config(test_mode)
        ok = send_telegram_message(
            token,
            chat_id,
            "✅ <b>PSX AlphaSignals connectivity test</b>\n"
            f"⚙️ Mode: {cfg['name']} (time stop {cfg['time_stop_bars']} sessions, trail EMA-{cfg['trailing_ema']})\n"
            f"🕒 {datetime.datetime.now().strftime('%d-%b-%Y %H:%M')}\n"
            "<i>Delivery to this chat is working.</i>",
        )
        print("✓ Test message delivered." if ok else "✗ Test message delivery failed.")
        sys.exit(0 if ok else 1)

    if args.check_commands:
        print("Checking for interactive Telegram user commands...")
        poll_incoming_commands(token, chat_id)
        return

    symbols = [args.symbol.upper()] if args.symbol else None
    messages = run_daily_market_cycle(
        token,
        chat_id,
        symbols=symbols,
        dry_run=args.dry_run,
        strict_freshness=args.strict_freshness,
        mode=args.mode,
    )

    if args.dry_run or not token or not chat_id:
        print("\n=== [ACTIONABLE MARKET CARDS PREVIEW] ===")
        import re
        for m in messages:
            clean = re.sub(r"<[^>]+>", "", m)
            print(clean)
            print("-" * 40)


if __name__ == "__main__":
    main()
