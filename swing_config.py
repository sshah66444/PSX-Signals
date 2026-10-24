"""
swing_config.py
Configuration file for swing trading parameters.
Extends holding periods, widens targets, and adjusts indicators for multi-week positions.
"""

# Trading Mode Configuration
TRADING_MODES = {
    "day_trade": {
        "name": "Day Trading / Short-term",
        "description": "Quick 2-4 day momentum plays with tight stops",
        "ema_fast": 20,
        "ema_medium": 50,
        "ema_slow": 200,
        "resistance_lookback": 20,
        "support_lookback": 20,
        "time_stop_bars": 4,
        "holding_max_bars": 30,
        "min_hold_before_trail": 3,
        "tp1_risk_multiplier": 1.35,
        "tp2_risk_multiplier": 2.5,
        "tp1_atr_multiplier": 1.4,
        "tp2_atr_multiplier": 2.6,
        "trailing_ema": 20,
        "volume_period": 20,
        "volume_surge_threshold": 1.20,
        "bb_period": 20,
        "squeeze_lookback": 60,
        "stop_atr_multiplier": 0.8,
        "mr_time_stop_bars": 2,
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    },
    "swing_trade": {
        "name": "Swing Trading",
        "description": "Multi-week position trades capturing larger trends",
        "ema_fast": 50,
        "ema_medium": 100,
        "ema_slow": 200,
        "resistance_lookback": 50,
        "support_lookback": 50,
        "time_stop_bars": 15,
        "holding_max_bars": 90,
        "min_hold_before_trail": 10,
        "tp1_risk_multiplier": 2.5,
        "tp2_risk_multiplier": 4.5,
        "tp1_atr_multiplier": 2.5,
        "tp2_atr_multiplier": 4.5,
        "trailing_ema": 50,
        "volume_period": 50,
        "volume_surge_threshold": 1.1,
        "bb_period": 50,
        "squeeze_lookback": 120,
        "stop_atr_multiplier": 1.2,
        "mr_time_stop_bars": 5,
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    },
    "position_trade": {
        "name": "Position Trading",
        "description": "Long-term multi-month positions following major trends",
        "ema_fast": 100,
        "ema_medium": 150,
        "ema_slow": 200,
        "resistance_lookback": 100,
        "support_lookback": 100,
        "time_stop_bars": 30,
        "holding_max_bars": 180,
        "min_hold_before_trail": 20,
        "tp1_risk_multiplier": 3.5,
        "tp2_risk_multiplier": 6.0,
        "tp1_atr_multiplier": 3.5,
        "tp2_atr_multiplier": 6.0,
        "trailing_ema": 100,
        "volume_period": 100,
        "volume_surge_threshold": 1.05,
        "bb_period": 100,
        "squeeze_lookback": 200,
        "stop_atr_multiplier": 1.5,
        "mr_time_stop_bars": 10,
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    }
}

# Default mode
DEFAULT_MODE = "swing_trade"

def get_config(mode: str = None):
    """
    Get trading configuration by mode.

    Args:
        mode: Trading mode ('day_trade', 'swing_trade', 'position_trade')
              If None, returns default mode.

    Returns:
        Dictionary with trading parameters
    """
    if mode is None:
        mode = DEFAULT_MODE

    if mode not in TRADING_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Choose from: {list(TRADING_MODES.keys())}")

    return TRADING_MODES[mode].copy()


def list_modes():
    """List available trading modes with descriptions."""
    for mode_key, mode_config in TRADING_MODES.items():
        print(f"\n{mode_key}:")
        print(f"  Name: {mode_config['name']}")
        print(f"  Description: {mode_config['description']}")
        print(f"  Max Holding Period: {mode_config['holding_max_bars']} days")
        print(f"  Time Stop: {mode_config['time_stop_bars']} days")
        print(f"  Target Multipliers: TP1={mode_config['tp1_risk_multiplier']}x, TP2={mode_config['tp2_risk_multiplier']}x")


if __name__ == "__main__":
    print("Available Trading Modes:")
    print("=" * 60)
    list_modes()
