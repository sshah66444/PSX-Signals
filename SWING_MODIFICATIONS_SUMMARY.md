# PSX Signal Screener - Swing Trading Modifications

## ✅ Changes Completed

The PSX Signal Screener has been successfully modified to support **swing trading** with extended holding periods and optimized parameters for multi-week position trades.

## 📁 Files Created/Modified

### New Files
1. **`swing_config.py`** - Configuration module with 3 trading modes
2. **`run_screener.py`** - Wrapper script to easily switch between modes
3. **`SWING_TRADING_GUIDE.md`** - Comprehensive usage guide
4. **`mode_comparison.py`** - Tool to compare trading modes

### Modified Files
1. **`signal_engine.py`** - Added `mode` parameter support throughout
2. **`backtester.py`** - Updated to use mode configuration

## 🚀 How to Use

### Quick Start

```bash
cd ~/Downloads/psx-signal-screener

# Run in swing trading mode (default)
python run_screener.py --mode swing_trade

# Or use the original for day trading
python run_screener.py --mode day_trade

# Compare all modes
python mode_comparison.py
```

### Three Trading Modes Available

| Mode | Time Stop | Max Hold | TP1 | TP2 | Best For |
|------|-----------|----------|-----|-----|----------|
| **day_trade** | 4 days | 30 days | 1.35x | 2.5x | Quick momentum plays |
| **swing_trade** | 15 days | 90 days | 2.5x | 4.5x | Multi-week positions (RECOMMENDED) |
| **position_trade** | 30 days | 180 days | 3.5x | 6.0x | Long-term trends |

## 🎯 Key Swing Trading Features

### Extended Holding Periods
- **Time Stop**: 15 days (vs 4 days in day trading)
- **Max Holding**: 90 days (vs 30 days)
- **Min Hold Before Trail**: 10 days (vs 3 days)

### Wider Profit Targets
- **TP1**: 2.5x initial risk (vs 1.35x)
- **TP2**: 4.5x initial risk (vs 2.5x)
- Allows bigger winners to develop

### More Conservative Stops
- **Stop Loss**: 1.2x ATR (vs 0.8x)
- **Trailing**: 50 EMA (vs 20 EMA)
- Fewer whipsaws, better trend following

### Adjusted Technical Parameters
- **Resistance Lookback**: 50 days (vs 20 days)
- **Volume Period**: 50 days (vs 20 days)
- **Bollinger Period**: 50 days (vs 20 days)
- **Squeeze Lookback**: 120 days (vs 60 days)

## 📊 Expected Results

### Swing Trading Benefits
✅ Larger average winners (2.5x to 4.5x targets)  
✅ Fewer false breakouts (50-day consolidation required)  
✅ Less whipsaw (50 EMA more stable)  
✅ Better for working professionals (less monitoring)  
✅ Optimal for PSX liquidity patterns  

### Trade-offs
⚠️ Fewer total signals  
⚠️ Wider stops require adjusted position sizing  
⚠️ Requires more patience  

## 🔧 Programmatic Usage

```python
from signal_engine import generate_signal
from swing_config import get_config

# Get configuration
config = get_config("swing_trade")

# Generate signal
setup = generate_signal(
    symbol="OGDC",
    df=price_data,
    df_kse=kse_data,
    mode="swing_trade"  # <-- Pass mode parameter
)

# Run backtest
from backtester import run_signal_backtest

results = run_signal_backtest(
    df=price_data,
    symbol="OGDC",
    df_kse=kse_data,
    holding_max_bars=config["holding_max_bars"],
    time_stop_bars=config["time_stop_bars"],
    mode="swing_trade"  # <-- Pass mode parameter
)
```

## 📖 Documentation

Read **`SWING_TRADING_GUIDE.md`** for:
- Complete parameter explanations
- Usage examples
- Backtesting guide
- Tips for swing trading
- Troubleshooting

## ✅ Testing Results

All modifications have been tested and verified:
- ✅ Configuration module working
- ✅ Signal engine integration working
- ✅ Mode parameter passing correctly
- ✅ Backward compatible with existing code

## 🎓 Recommendations

1. **Start with swing_trade mode** - Best balance for PSX
2. **Paper trade for 2-4 weeks** before going live
3. **Adjust position sizes** for wider stops
4. **Be patient** - Swing trades take time to develop
5. **Run backtests** to validate performance on your favorite stocks

## 📞 Next Steps

1. Review the comparison: `python mode_comparison.py`
2. Read the guide: `SWING_TRADING_GUIDE.md`
3. Test the system: `python run_screener.py --mode swing_trade`
4. Run backtests to compare day vs swing performance
5. Customize parameters in `swing_config.py` if needed

---

**Default Mode**: The system now defaults to `swing_trade` mode, which is optimal for most PSX retail traders.
