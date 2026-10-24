#!/usr/bin/env python3
"""
run_screener.py
Wrapper script to run the PSX Signal Screener with different trading modes.
Usage: python run_screener.py --mode swing_trade
"""

import sys
import os
import argparse
from swing_config import list_modes, get_config, TRADING_MODES

def main():
    parser = argparse.ArgumentParser(
        description="PSX Signal Screener - Multi-timeframe Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available Trading Modes:
  day_trade       - Quick 2-4 day momentum plays (4 day time stop)
  swing_trade     - Multi-week position trades (15 day time stop, default)
  position_trade  - Long-term multi-month positions (30 day time stop)

Examples:
  python run_screener.py --mode swing_trade
  python run_screener.py --mode day_trade
  python run_screener.py --list-modes
        """
    )

    parser.add_argument(
        "--mode",
        type=str,
        default="swing_trade",
        choices=list(TRADING_MODES.keys()),
        help="Trading mode (default: swing_trade)"
    )

    parser.add_argument(
        "--list-modes",
        action="store_true",
        help="List all available trading modes and exit"
    )

    args = parser.parse_args()

    if args.list_modes:
        list_modes()
        return

    # Set environment variable for the app to read
    os.environ["PSX_TRADING_MODE"] = args.mode

    config = get_config(args.mode)

    print("=" * 70)
    print(f"PSX Signal Screener - {config['name']}")
    print("=" * 70)
    print(f"Description: {config['description']}")
    print(f"Time Stop: {config['time_stop_bars']} days")
    print(f"Max Holding: {config['holding_max_bars']} days")
    print(f"Trailing EMA: {config['trailing_ema']}")
    print(f"TP1 Target: {config['tp1_risk_multiplier']}x risk")
    print(f"TP2 Target: {config['tp2_risk_multiplier']}x risk")
    print("=" * 70)
    print()

    # Import and run Streamlit app
    import subprocess
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app.py"])

if __name__ == "__main__":
    main()
