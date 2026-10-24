"""
mode_comparison.py
Quick comparison tool to visualize differences between trading modes
"""

from swing_config import TRADING_MODES
import pandas as pd

def compare_modes():
    """Display side-by-side comparison of all trading modes"""

    comparison_data = []

    for mode_key, config in TRADING_MODES.items():
        comparison_data.append({
            'Mode': mode_key,
            'Name': config['name'],
            'Time Stop (days)': config['time_stop_bars'],
            'Max Hold (days)': config['holding_max_bars'],
            'TP1 Target': f"{config['tp1_risk_multiplier']}x",
            'TP2 Target': f"{config['tp2_risk_multiplier']}x",
            'Trailing EMA': config['trailing_ema'],
            'Stop ATR': f"{config['stop_atr_multiplier']}x",
            'Volume Period': config['volume_period'],
            'Vol Surge %': f"{(config['volume_surge_threshold']-1)*100:.0f}%",
        })

    df = pd.DataFrame(comparison_data)

    print("\n" + "="*100)
    print("PSX SIGNAL SCREENER - TRADING MODE COMPARISON")
    print("="*100)
    print()
    print(df.to_string(index=False))
    print()
    print("="*100)
    print("\nKey Differences:")
    print("  • Day Trading: Quick 2-4 day plays with tight stops and fast exits")
    print("  • Swing Trading: Multi-week positions with wider stops and larger targets")
    print("  • Position Trading: Multi-month trends with maximum patience")
    print()
    print("Recommendation for PSX:")
    print("  → Swing Trading mode is optimal for most retail traders")
    print("  → Gives trends time to develop while avoiding intraday noise")
    print("  → Better risk:reward ratios (2.5x to 4.5x vs 1.35x to 2.5x)")
    print("="*100)
    print()

if __name__ == "__main__":
    compare_modes()
