#!/bin/bash
# Quick start script for PSX Signal Screener with Swing Trading

cd ~/Downloads/psx-signal-screener

echo "================================================"
echo "PSX Signal Screener - Swing Trading Quick Start"
echo "================================================"
echo ""

# Activate virtual environment
source .venv/bin/activate

# Set swing trading as default mode
export PSX_TRADING_MODE=swing_trade

echo "✓ Virtual environment activated"
echo "✓ Trading mode set to: swing_trade"
echo ""
echo "Choose an option:"
echo ""
echo "1. Test Telegram bot (dry run - no messages sent)"
echo "2. Send test message to Telegram"
echo "3. Run Streamlit dashboard"
echo "4. Compare trading modes"
echo "5. View configuration"
echo ""
read -p "Enter choice (1-5): " choice

case $choice in
  1)
    echo ""
    echo "Running dry-run scan (swing mode)..."
    python telegram_notifier.py --mode swing_trade --dry-run
    ;;
  2)
    echo ""
    echo "Sending test scan to Telegram (swing mode)..."
    python telegram_notifier.py --mode swing_trade --symbol OGDC
    ;;
  3)
    echo ""
    echo "Starting Streamlit dashboard..."
    python run_screener.py --mode swing_trade
    ;;
  4)
    echo ""
    python mode_comparison.py
    ;;
  5)
    echo ""
    python -c "from swing_config import get_config; import json; print(json.dumps(get_config('swing_trade'), indent=2))"
    ;;
  *)
    echo "Invalid choice"
    ;;
esac
