"""
telegram_notifier.py
Scans PSX stocks and sends automated daily trade signal alerts directly to Telegram.
Supports dry-run preview mode if credentials are not yet configured.
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
from signal_engine import generate_signal


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Sends a markdown-formatted message to a Telegram chat/channel."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            print("✓ Telegram notification delivered successfully.")
            return True
        else:
            print(f"✗ Telegram API error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"✗ Failed to connect to Telegram API: {e}")
        return False


def build_daily_psx_digest(symbols: list = None, lang: str = "roman_urdu") -> list[str]:
    """
    Scans watchlist stocks and returns formatted message cards for active signals.
    """
    watchlist = get_watchlist()
    target_symbols = symbols if symbols else list(watchlist.keys())
    messages = []

    today_str = datetime.date.today().strftime("%d-%b-%Y")
    now_time = datetime.datetime.now().strftime("%I:%M %p")

    # Header message
    header = (
        f"📊 <b>PSX AlphaSignals Daily Market Alert</b>\n"
        f"📅 Date: <b>{today_str}</b> | ⏰ {now_time}\n"
        f"────────────────────────\n"
        f"Automatic post-market scan for high-probability setups.\n"
    )
    messages.append(header)

    signals_found = 0
    for sym in target_symbols:
        df, _ = fetch_psx_stock(sym, period="3mo", use_live=True)
        if df.empty or len(df) < 20:
            continue

        sig = generate_signal(sym, df)
        if not sig:
            continue

        # Filter for actionable opportunities (or explicit symbol request)
        is_actionable = (
            (symbols is not None)
            or ("BUY" in sig["signal_type"].upper())
            or (sig.get("star_count", 0) >= 3)
        )

        if is_actionable:
            signals_found += 1
            comp_name = watchlist.get(sym, {}).get("name", sym)

            if lang == "roman_urdu":
                card = (
                    f"🟢 <b>${sym}</b> — {comp_name}\n"
                    f"💰 <b>Price:</b> {sig['price']:.2f} PKR\n"
                    f"🎯 <b>Signal:</b> {sig['signal_ur']} {sig['stars']}\n"
                    f"⚠️ <b>Wajah:</b> {sig['wajah_ur']}\n"
                    f"🛒 <b>Buy Zone:</b> {sig['buy_zone_low']:.2f} – {sig['buy_zone_high']:.2f}\n"
                    f"🛑 <b>Stop Loss:</b> {sig['stop_loss']:.2f} (Risk: -{((sig['price'] - sig['stop_loss'])/sig['price'])*100:.1f}%)\n"
                    f"🎯 <b>TP1:</b> {sig['tp1']:.2f} (R:R = {sig['rr_tp1']}:1)\n"
                    f"🎯 <b>TP2:</b> {sig['tp2']:.2f} (R:R = {sig['rr_tp2']}:1)\n"
                    f"<i>Caution: {sig['caution_ur']}</i>\n"
                )
            else:
                card = (
                    f"🟢 <b>${sym}</b> — {comp_name}\n"
                    f"💰 <b>Price:</b> {sig['price']:.2f} PKR\n"
                    f"🎯 <b>Signal:</b> {sig['signal_type']} {sig['stars']}\n"
                    f"🛒 <b>Buy Zone:</b> {sig['buy_zone_low']:.2f} – {sig['buy_zone_high']:.2f}\n"
                    f"🛑 <b>Stop Loss:</b> {sig['stop_loss']:.2f}\n"
                    f"🎯 <b>TP1:</b> {sig['tp1']:.2f} | <b>TP2:</b> {sig['tp2']:.2f}\n"
                    f"⚖️ <b>R:R (TP1):</b> {sig['rr_tp1']}:1 | <b>RSI:</b> {sig['rsi']}\n"
                )
            messages.append(card)

    if signals_found == 0:
        messages.append("ℹ️ <i>No high-probability buy setups identified today. Market in consolidation.</i>")
    else:
        footer = (
            f"────────────────────────\n"
            f"💡 <i>Disclaimer: Educational purposes only. Always manage your position size and follow stop losses.</i>"
        )
        messages.append(footer)

    return messages


def main():
    parser = argparse.ArgumentParser(description="PSX Telegram Trade Signal Notifier")
    parser.add_argument("--symbol", type=str, help="Specific PSX symbol to scan (e.g. IPAK)")
    parser.add_argument("--lang", type=str, choices=["roman_urdu", "english"], default="roman_urdu")
    parser.add_argument("--dry-run", action="store_true", help="Print messages to console without sending to Telegram")
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    symbols = [args.symbol.upper()] if args.symbol else None
    print(f"Scanning PSX watchlist for actionable setups ({args.lang})...")
    messages = build_daily_psx_digest(symbols=symbols, lang=args.lang)

    full_output = "\n".join(messages)

    if args.dry_run or not token or not chat_id:
        print("\n=== [DRY RUN / PREVIEW MODE] ===")
        print("Telegram Bot Token or Chat ID not set in environment.")
        print("Showing message preview that will be sent to your phone:\n")
        # Strip HTML tags for clean console display
        import re
        clean_text = re.sub(r"<[^>]+>", "", full_output)
        print(clean_text)
        print("\nTo send to Telegram, set environment variables:")
        print("  export TELEGRAM_BOT_TOKEN='your_bot_token'")
        print("  export TELEGRAM_CHAT_ID='your_chat_id'")
        return

    # Send messages to Telegram
    # If the digest is long, send in chunks or combine
    print(f"Delivering {len(messages)} messages to Telegram chat {chat_id}...")
    combined_message = "\n".join(messages)
    # Telegram max message length is 4096 characters
    if len(combined_message) <= 4000:
        send_telegram_message(token, chat_id, combined_message)
    else:
        for chunk in messages:
            send_telegram_message(token, chat_id, chunk)


if __name__ == "__main__":
    main()
