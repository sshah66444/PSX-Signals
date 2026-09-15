"""
telegram_notifier.py
Production PSX Telegram Bot with Actionable Cards, Lifecycle Follow-ups,
and Interactive Commands (/scan, /active, /stock, /why, /performance, /watchlist).
"""

import os
import sys
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

from data_engine import get_watchlist, fetch_psx_stock
from signal_engine import generate_signal, format_actionable_card
from signal_tracker import (
    record_or_update_setup,
    audit_active_signals,
    get_active_signals,
    get_performance_summary,
)


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Sends an HTML formatted message to Telegram."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return True
        else:
            print(f"✗ Telegram API error ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"✗ Failed to connect to Telegram: {e}")
        return False


def broadcast_telegram_message(token: str, chat_ids_str: str, text: str) -> bool:
    """Delivers message to one or more recipients, groups, or channels (comma-separated)."""
    if not token or not chat_ids_str:
        return False
    recipients = [c.strip() for c in str(chat_ids_str).split(",") if c.strip()]
    success = True
    for cid in recipients:
        ok = send_telegram_message(token, cid, text)
        if not ok:
            success = False
    return success


def run_daily_market_cycle(token: str, chat_id: str, symbols: list = None, dry_run: bool = False) -> list[str]:
    """
    Executes the complete daily workflow:
    1. Fetches real market data.
    2. Audits existing open signals (checks TP/SL hits, sends updates).
    3. Scans for new or triggered setups.
    4. Records them in signals.db without repeating unchanged setups.
    """
    watchlist = get_watchlist()
    target_symbols = symbols if symbols else list(watchlist.keys())
    messages = []

    print(f"1. Fetching real market data for {len(target_symbols)} symbols...")
    market_data = {}
    skipped_symbols = []

    for sym in target_symbols:
        res = fetch_psx_stock(sym, period="6mo")
        if res.get("status") == "OK":
            market_data[sym] = res
        else:
            skipped_symbols.append((sym, res.get("error", "Data unavailable")))

    # 2. Audit existing open setups
    print("2. Auditing open setups in signals.db...")
    lifecycle_updates = audit_active_signals(market_data)
    for upd in lifecycle_updates:
        messages.append(upd["text"])
        if not dry_run and token and chat_id:
            broadcast_telegram_message(token, chat_id, upd["text"])

    # 3. Evaluate setups for new/updated triggers
    print("3. Evaluating setups across watchlist...")
    new_setup_cards = []
    for sym, m_info in market_data.items():
        df = m_info["df"]
        setup = generate_signal(sym, df, data_meta=m_info)
        status = setup.get("status")

        if status in ["TRIGGERED", "WATCHING"]:
            is_new_or_updated, note = record_or_update_setup(setup, sym)
            # Only send card if this is a newly detected setup or transitioned to TRIGGERED
            if is_new_or_updated:
                comp_name = watchlist.get(sym, {}).get("name", sym)
                card = format_actionable_card(setup, company_name=comp_name)
                new_setup_cards.append(card)
                if not dry_run and token and chat_id:
                    broadcast_telegram_message(token, chat_id, card)

    # 4. Summary header
    today_str = datetime.date.today().strftime("%d-%b-%Y")
    header = (
        f"📊 <b>PSX AlphaSignals Daily Market Scan</b>\n"
        f"📅 Date: <b>{today_str}</b>\n"
        f"────────────────────────\n"
        f"Audited active signals: {len(lifecycle_updates)} updates.\n"
        f"New/Triggered setups: {len(new_setup_cards)}.\n"
    )
    if skipped_symbols:
        header += f"<i>Note: {len(skipped_symbols)} symbols skipped due to data unavailability.</i>\n"

    # Prepend header
    messages.insert(0, header)
    messages.extend(new_setup_cards)

    if len(new_setup_cards) == 0 and len(lifecycle_updates) == 0:
        messages.append("ℹ️ <i>No new triggered setups or active updates today. Existing positions held.</i>")

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
        active_list = get_active_signals()
        if not active_list:
            reply = "ℹ️ No currently active setups in the ledger. Send /scan to run a fresh evaluation."
        else:
            cards = []
            for s in active_list:
                sym = s["symbol"]
                card = (
                    f"• <b>${sym}</b> ({s['status']})\n"
                    f"  Strategy: {s['strategy']} | Entry: {s['entry_min']:.2f}–{s['entry_max']:.2f}\n"
                    f"  SL: {s['stop_loss']:.2f} | TP1: {s['tp1']:.2f} | TP2: {s['tp2']:.2f}\n"
                )
                cards.append(card)
            reply = "⚡ <b>Active & Watching Setups:</b>\n\n" + "\n".join(cards)
        send_telegram_message(token, chat_id, reply)

    elif cmd == "/performance":
        summary = get_performance_summary()
        if summary.get("closed_count", 0) == 0:
            reply = (
                "📊 <b>Audited Live Performance Ledger</b>\n\n"
                f"Total setups recorded: <b>{summary['total_recorded']}</b>\n"
                f"Active / In-progress: <b>{summary['active_count']}</b>\n"
                f"Closed trades: <b>0</b> (waiting for resolutions).\n\n"
                "<i>Ledger tracks real signals issued by this bot, completely separate from historical backtests.</i>"
            )
        else:
            reply = (
                "📊 <b>Audited Live Performance Ledger</b>\n\n"
                f"Total Closed Trades: <b>{summary['closed_count']}</b>\n"
                f"Wins: <b>{summary['wins']}</b> | Losses: <b>{summary['losses']}</b>\n"
                f"Win Rate: <b>{summary['win_rate_pct']}%</b>\n"
                f"Total Net P&L: <b>{summary['net_pnl_pct']:+.2f}%</b> (after costs)\n"
                f"Avg Trade P&L: <b>{summary['avg_trade_pnl']:+.2f}%</b>\n"
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

        setup = generate_signal(sym, res["df"], data_meta=res)
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
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if args.check_commands:
        print("Checking for interactive Telegram user commands...")
        poll_incoming_commands(token, chat_id)
        return

    symbols = [args.symbol.upper()] if args.symbol else None
    messages = run_daily_market_cycle(token, chat_id, symbols=symbols, dry_run=args.dry_run)

    if args.dry_run or not token or not chat_id:
        print("\n=== [ACTIONABLE MARKET CARDS PREVIEW] ===")
        import re
        for m in messages:
            clean = re.sub(r"<[^>]+>", "", m)
            print(clean)
            print("-" * 40)


if __name__ == "__main__":
    main()
