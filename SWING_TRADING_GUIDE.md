# Swing Trading Mode - Setup Guide

## Overview
The PSX Signal Screener has been modified to support **swing trading** with extended holding periods, wider profit targets, and parameters optimized for multi-week position trades.

## What Changed

### 1. Three Trading Modes Available
- **day_trade**: Quick 2-4 day momentum plays (original settings)
- **swing_trade**: Multi-week positions (NEW - Default)
- **position_trade**: Multi-month trend following

### 2. Key Parameter Differences

| Parameter | Day Trade | Swing Trade | Position Trade |
|-----------|-----------|-------------|----------------|
| Time Stop | 4 days | 15 days | 30 days |
| Max Holding | 30 days | 90 days | 180 days |
| TP1 Target | 1.35x risk | 2.5x risk | 3.5x risk |
| TP2 Target | 2.5x risk | 4.5x risk | 6.0x risk |
| Trailing EMA | 20 EMA | 50 EMA | 100 EMA |
| Stop ATR Multiplier | 0.8x | 1.2x | 1.5x |
| Volume Period | 20 days | 50 days | 100 days |
| Resistance Lookback | 20 days | 50 days | 100 days |

### 3. Modified Files
- `swing_config.py` - Configuration for all trading modes
- `signal_engine.py` - Updated to accept mode parameter
- `backtester.py` - Updated to use mode configuration
- `run_screener.py` - Wrapper script to easily switch modes

## How to Use

### Method 1: Using the Run Script (Recommended)

```bash
# Run in swing trading mode (default)
python run_screener.py --mode swing_trade

# Run in day trading mode
python run_screener.py --mode day_trade

# Run in position trading mode
python run_screener.py --mode position_trade

# List all available modes
python run_screener.py --list-modes
```

### Method 2: Set Environment Variable

```bash
# Set the mode
export PSX_TRADING_MODE=swing_trade

# Run the app
streamlit run app.py
```

### Method 3: Programmatic Usage

```python
from signal_engine import generate_signal
from swing_config import get_config

# Load swing trading configuration
config = get_config("swing_trade")

# Generate signal with swing parameters
setup = generate_signal(
    symbol="OGDC",
    df=price_data,
    df_kse=kse_data,
    mode="swing_trade"
)

print(f"Time Stop: {config['time_stop_bars']} days")
print(f"TP1: {setup['tp1']} (Target: {config['tp1_risk_multiplier']}x risk)")
```

## Swing Trading Characteristics

### Entry Criteria (Same as before)
- BREAKOUT: Price clearing 50-day resistance with volume
- PULLBACK: Price testing 50 EMA support in uptrend

### Exit Strategy (Modified)
1. **Trailing Stop**: Exit on daily close below 50 EMA (was 20 EMA)
2. **Time Stop**: Exit after 15 days if no progress (was 4 days)
3. **Profit Targets**: 
   - TP1: 2.5x initial risk (was 1.35x)
   - TP2: 4.5x initial risk (was 2.5x)

### Risk Management
- Wider stops: 1.2x ATR (was 0.8x) to avoid premature stop-outs
- Longer holding period allows trends to develop
- Min hold before trailing: 10 days (was 3 days)

## Backtesting with Swing Mode

```python
from backtester import run_signal_backtest
from swing_config import get_config

# Get swing config
config = get_config("swing_trade")

# Run backtest with swing parameters
results = run_signal_backtest(
    df=price_data,
    symbol="OGDC",
    df_kse=kse_data,
    holding_max_bars=config["holding_max_bars"],  # 90 days
    time_stop_bars=config["time_stop_bars"],      # 15 days
    mode="swing_trade"
)
```

## Expected Changes in Behavior

### Swing vs Day Trading

**Swing Trading:**
- Fewer signals (requires 50-day consolidation)
- Larger average winners (4.5x vs 2.5x targets)
- Lower win rate but better risk:reward
- Fewer whipsaws (50 EMA more stable than 20 EMA)
- Holds through minor pullbacks

**Day Trading:**
- More frequent signals
- Quick exits (4 days)
- Tighter stops
- Higher win rate but smaller winners

## Example Workflow

1. **Morning Scan** (8:00 AM Pakistan Time)
```bash
python run_screener.py --mode swing_trade
```

2. **Review Signals** 
   - Look for BREAKOUT setups clearing 50-day resistance
   - Check PULLBACK setups testing 50 EMA support

3. **Position Management**
   - Hold for minimum 10 days before trailing
   - Trail stop below 50 EMA
   - Exit if no progress after 15 days
   - Scale out at TP1 (2.5x), trail remainder to TP2 (4.5x)

## Tips for Swing Trading

1. **Be Patient**: Swing trades take time to develop (weeks, not days)
2. **Trust the 50 EMA**: Don't exit early - let the trailing stop work
3. **Avoid Overtrading**: Fewer signals means better quality setups
4. **Position Sizing**: Swing trades have wider stops - adjust position size accordingly
5. **Monitor Weekly**: Daily monitoring is less critical than in day trading

## Telegram Bot Integration

To receive swing trade alerts via Telegram, the bot will automatically use the mode set in your environment:

```bash
# Set mode
export PSX_TRADING_MODE=swing_trade

# Run telegram bot
python telegram_notifier.py --check-commands
```

## Troubleshooting

**Issue**: Getting same signals as before
- **Fix**: Ensure you're passing `mode="swing_trade"` parameter

**Issue**: Stops too tight
- **Fix**: Swing mode uses 1.2x ATR stops (vs 0.8x in day trading)

**Issue**: Not enough signals
- **Fix**: Swing trading naturally produces fewer but higher-quality setups

## Next Steps

1. Run backtests comparing swing vs day trading performance
2. Paper trade for 2-4 weeks to validate the new parameters
3. Adjust configuration in `swing_config.py` based on your risk tolerance
4. Consider creating custom modes for specific strategies

## Configuration Customization

Edit `swing_config.py` to create your own custom modes:

```python
TRADING_MODES["my_custom_mode"] = {
    "name": "My Custom Strategy",
    "time_stop_bars": 20,
    "holding_max_bars": 120,
    # ... other parameters
}
```

## Support

For questions or issues, review:
- `swing_config.py` - All configuration parameters
- `signal_engine.py` - Strategy logic
- `backtester.py` - Backtesting implementation
