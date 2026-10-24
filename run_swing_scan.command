#!/bin/bash
# run_swing_scan.command
# Double-click launcher (macOS): runs the live swing-trading scan and delivers the
# result to Telegram. Uses the same entry point as the scheduled GitHub Action.
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate || exit 1
export PSX_TRADING_MODE=swing_trade

echo "=============================================="
echo " PSX AlphaSignals — Swing Trade Scan"
echo "=============================================="
echo

python telegram_notifier.py --mode swing_trade

echo
read -r -p "Press [Enter] to close..."
