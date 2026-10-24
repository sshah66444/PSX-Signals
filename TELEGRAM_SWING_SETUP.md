# Telegram Bot - Swing Trading Integration

## ✅ Your Telegram Bot is Now Ready for Swing Trading!

The telegram_notifier.py has been updated to support all three trading modes.

## How to Use

### Method 1: Environment Variable (Recommended for Automation)

```bash
# Set your preferred mode (persists across runs)
export PSX_TRADING_MODE=swing_trade

# Run your daily scan
python telegram_notifier.py

# Or dry-run to test
python telegram_notifier.py --dry-run
```

### Method 2: Command Line Argument (One-time override)

```bash
# Swing trading mode
python telegram_notifier.py --mode swing_trade

# Day trading mode
python telegram_notifier.py --mode day_trade

# Position trading mode
python telegram_notifier.py --mode position_trade

# Test without sending to Telegram
python telegram_notifier.py --mode swing_trade --dry-run
```

### Method 3: Update Your Cron Job / Scheduler

If you have automated scans (e.g., via cron), update them:

```bash
# Edit your crontab
crontab -e

# Add or update your daily scan (8 PM Pakistan time example)
0 20 * * * cd /path/to/psx-signal-screener && PSX_TRADING_MODE=swing_trade python telegram_notifier.py
```

## What Changed in Your Telegram Messages

### Message Header Now Shows Mode
```
📊 PSX AlphaSignals Daily Market Scan
📅 Date: 22-Oct-2024
🏛️ Macro Regime: 🟢 BULL MARKET
Running in Swing Trading mode (time_stop: 15 days, targets: 2.5x/4.5x)
────────────────────────
```

### Signal Cards Now Show Swing Parameters
- **Targets**: TP1: 2.5x risk, TP2: 4.5x risk (was 1.35x / 2.5x)
- **Trailing Rule**: "Exit on close below 50 EMA" (was 20 EMA)
- **Time Stop**: 15 days (was 4 days)

## Interactive Commands (Still Work)

All your existing Telegram commands work with the new mode:

```
/scan         - Run market scan in current mode
/active       - Show open positions
/stock OGDC   - Check specific stock
/why OGDC     - See condition checklist
/performance  - View trading stats
/watchlist    - List all 75 stocks
```

## Testing Your Setup

### 1. Test with Dry Run (No Telegram Messages)

```bash
python telegram_notifier.py --mode swing_trade --dry-run
```

This will:
- Fetch live market data
- Generate signals using swing parameters
- Print output to console (not send to Telegram)
- Show you what signals would be generated

### 2. Test with Single Stock

```bash
python telegram_notifier.py --mode swing_trade --symbol OGDC --dry-run
```

### 3. Send Actual Test Message

```bash
python telegram_notifier.py --mode swing_trade --symbol OGDC
```

Check your Telegram - you should receive signals with swing trading parameters!

## Example Output Comparison

### Day Trading Mode
```
🎯 Targets: TP1: 125.50 PKR | TP2: 132.80 PKR
📈 Exit Model: Exit on daily close below 20 EMA (120.15 PKR)
⏱️ Time Stop: 4 days
```

### Swing Trading Mode (NEW)
```
🎯 Targets: TP1: 135.20 PKR | TP2: 148.50 PKR
📈 Exit Model: Exit on daily close below 50 EMA (118.30 PKR)
⏱️ Time Stop: 15 days
```

## Automation Setup

### Daily 8 PM Scan (Swing Mode)

```bash
# Add to crontab (crontab -e)
0 20 * * 1-5 cd ~/Downloads/psx-signal-screener && PSX_TRADING_MODE=swing_trade python telegram_notifier.py >> ~/logs/psx_scan.log 2>&1
```

### Morning Pre-Market Check (9 AM)

```bash
0 9 * * 1-5 cd ~/Downloads/psx-signal-screener && PSX_TRADING_MODE=swing_trade python telegram_notifier.py --check-commands
```

## Troubleshooting

**Issue**: Still getting day trading parameters
```bash
# Check your environment
echo $PSX_TRADING_MODE

# Explicitly set it
export PSX_TRADING_MODE=swing_trade

# Or use --mode flag
python telegram_notifier.py --mode swing_trade --dry-run
```

**Issue**: No signals generated
- Swing mode requires 50-day consolidation patterns
- Fewer signals is normal (better quality)
- Check with --dry-run to see console output

**Issue**: Telegram messages not sending
```bash
# Verify credentials
cat .env | grep TELEGRAM

# Test connection
python telegram_notifier.py --dry-run
```

## Quick Reference

| Command | Purpose |
|---------|---------|
| `python telegram_notifier.py --mode swing_trade` | Run swing scan & send to Telegram |
| `python telegram_notifier.py --mode swing_trade --dry-run` | Test without sending |
| `python telegram_notifier.py --check-commands` | Listen for interactive commands |
| `export PSX_TRADING_MODE=swing_trade` | Set default mode |

## What Happens Next

1. **Your existing schedule continues working** - just add the mode parameter
2. **All historical data in signals.db stays intact** - no data loss
3. **New signals will use swing parameters** - wider targets, longer holds
4. **Existing open positions** - continue tracking with original parameters

## Ready to Go!

Your Telegram bot is now configured for swing trading. The default mode is `swing_trade`, so if you don't specify a mode, it will automatically use swing trading parameters.

Run this to test:
```bash
cd ~/Downloads/psx-signal-screener
python telegram_notifier.py --mode swing_trade --dry-run
```
