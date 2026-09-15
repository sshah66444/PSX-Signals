# PSX AlphaSignals — Professional Screener, Signal Engine & Audited Ledger

An institutional-grade technical screener, transparent signal engine, and audited trade ledger built with **Streamlit**, **Plotly**, and **Python** designed specifically for the Pakistan Stock Exchange (PSX).

The app demystifies and audits social media trade signals (such as the viral *"TP 1 2 3 hit Alhamdulilah"* posts) by replacing vague claims with mathematically grounded trade setups, verified real-market data, transparent condition checklists, multi-target backtesting with friction costs, and a persistent SQLite lifecycle ledger.

---

## 🚀 Key Features

### 1. 100% Real Market Data & Zero Synthetic Production Prices
* **Dual Real-Market Feeds**:
  * **Primary**: Yahoo Finance (`.KA` suffix) providing verified true intraday OHLCV.
  * **Secondary / Scraper**: Official Pakistan Stock Exchange Data Portal (`dps.psx.com.pk/timeseries/eod/{symbol}`).
* **Zero Synthetic Fabrication**: Production alerts and backtests strictly refuse to invent or approximate prices. If market data is unavailable or stale (> 5 days), the system explicitly reports `UNAVAILABLE` and skips the signal. Isolated test generators are strictly quarantined to automated unit tests.
* **Intraday Wick Verification**: The engine deterministically verifies that datasets contain true candle wicks. Datasets lacking true High/Low (e.g. DPS Open/Close only) are flagged as `Approximated OHLC` and disqualified from triggering live orders, protecting ATR and stop-loss fidelity.

### 2. Full PSX Liquid Watchlist (75 Equities across 10 Sectors)
Scans the 75 most liquid and active stocks in the Pakistani stock market:
* **Commercial & Islamic Banks**: MEBL, MCB, UBL, HBL, BAFL, BAHL, FABL, BIPL, AKBL, NBP.
* **Oil & Gas Exploration (E&P)**: OGDC, PPL, MARI, POL.
* **Fertilizer & Conglomerates**: FFC, EFERT, ENGRO, FATIMA, FFBL.
* **Cement**: LUCK, DGKC, MLCF, PIOC, CHCC, FCCL, KOHC, POWER.
* **Technology & Telecom**: SYS, TRG, AVN, AIRLINK, PTC, NETSOL, OCTOPUS, WTL.
* **Power Generation & Distribution**: HUBC, KAPCO, KEL, NCPL, NPL.
* **Oil & Gas Marketing (OMCs) & Gas**: PSO, APL, SHEL, SNGP, SSGC, HASCOL.
* **Refineries**: ATRL, PRL, NRL, CYNERGICO.
* **Pharmaceuticals & Chemicals**: SEARL, AGP, CPHL, GLAXO, HINOON, LOTCHEM, EPCL, GLL.
* **Automobile, Engineering & Steel**: MTL, INDU, SAZEW, AGTL, THALL, MUGHAL, ISL, INIL, TGL.
* **Textiles, Packaging & Consumer Foods**: NML, ILP, KTML, GATM, IPAK, UNITY, FCEPL, NATF.

### 3. Unified Strategy Engine & Transparent Condition Checklists
Both live alerts and historical backtests use the **exact same deterministic strategy function**:
* **Breakout Strategy (`BREAKOUT`)**: Consolidations near 20-day resistance, emerging from a tight volatility compression base, triggered on high-volume breakout ($> 1.20\times$ 20MA) with KSE-100 Relative Strength leadership.
* **Pullback Strategy (`PULLBACK`)**: Macro uptrend (Price > 50 EMA), testing 20 EMA / support band with stabilizing momentum and benchmark outperformance.
* **Non-Cosmetic Condition Checklist**: Status is strictly gated on 100% checklist pass rate:
  ```text
  📋 Condition Checklist:
     [✓] Macro Market Gate (KSE-100 > 50 EMA)
     [✓] Relative Strength Leader (vs KSE-100)
     [✓] Volatility Compression (Tight Base)
     [✓] Verified Intraday OHLC (True High/Low)
     [✓] Trend Alignment (Price > 20 & 50 EMA)
     [✓] Resistance Test / Clearance
     [✓] Volume Surge (Vol >= 1.20x 20MA)
     [✓] Minimum Liquidity (20MA Vol >= 80k)
     [✓] Momentum Health (MACD Histogram Expanding)
     [✓] Healthy RSI Range (42 - 68)
     [✓] Favorable Risk:Reward (>= 1.2:1)
  ⚡ Status: TRIGGERED
  ```

### 4. Positive-Expectancy Quant Engine & Multi-Target Backtester
* **Macro Market Gate (KSE-100 Index)**: Official 5-year historical KSE-100 feed (`dps.psx.com.pk/timeseries/eod/KSE100`). Suppresses long entries when KSE-100 trades below its 50 EMA (`MARKET_CORRECTION - Cash Preservation Mode`), avoiding fighting market beta.
* **Relative Strength (RS vs KSE-100)**: Quantifies stock performance relative to the index ($\text{RS} \ge \text{RS\_MA20}$), filtering for equities receiving institutional sponsorship.
* **Volatility Compression Squeeze**: Measures Bollinger Bandwidth against 60-day compression minimums to ensure breakouts originate from tight bases rather than erratic exhaustion moves.
* **Disciplined Time-Stops (4-Bar Rule)**: Automatically exits stalled trades after 4 sessions without follow-through, eliminating dead-capital drag and mitigating drawdown.
* **Realistic Scale-Out Model**: 50% profit booked at TP1, Stop Loss trailed to Breakeven, remaining 50% trails toward TP2.
* **Ambiguous Candle Detection**: Bars that touch both Target and Stop Loss on the same day are flagged as ambiguous and conservatively counted as stopped out. Same-bar retracements to breakeven after TP1 are accurately captured.
* **Real Trading Costs**: Automatically factors in customizable PSX round-trip broker commissions and CDC/SECP taxes (default: 0.35%).

### 5. Persistent SQLite Signal Ledger (`signals.db`)
* Database tracks setups across their entire lifecycle: `WATCHING` $\rightarrow$ `TRIGGERED` $\rightarrow$ `TP1_HIT` $\rightarrow$ `TP2_HIT` / `STOPPED_OUT` / `EXPIRED`.
* Configured with **SQLite WAL (Write-Ahead Logging)** mode and 30-second busy timeouts for concurrent access between Streamlit UI sessions and background Telegram bots.
* Deduplicates unchanged setups so daily notifications only announce fresh triggers or lifecycle events.

### 6. Autonomous Telegram Bot with Interactive Commands
* **Daily Morning Scan**: Automatically audits active setups and alerts on newly confirmed triggers.
* **Interactive Command Handler**:
  * `/scan` — Trigger an immediate market-wide scan across all 75 stocks.
  * `/active` — View all open, watching, and triggered setups in the ledger.
  * `/stock <SYM>` — View full setup card for any symbol (e.g. `/stock OGDC`).
  * `/why <SYM>` — View the complete condition checklist breakdown.
  * `/performance` — View audited live ledger performance after costs.
  * `/watchlist` — List all 75 tracked PSX equities.

---

## 🛠️ Getting Started

### 1. Install Dependencies
```bash
# Using standard pip:
pip install -r requirements.txt
```

### 2. Configure Environment (Optional for Telegram Bot)
Create a `.env` file in the project root:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 3. Run the Web Dashboard
```bash
streamlit run app.py
```
Open your browser at `http://localhost:8501`.

### 4. Run Automated Test Suite
```bash
python test_engine.py
```

### 5. Run Telegram Bot
```bash
# Test alert formatting without sending:
python telegram_notifier.py --dry-run

# Start interactive Telegram command listener:
python telegram_notifier.py --check-commands
```

---

## 📂 Project Architecture

```
psx-signal-screener/
├── app.py                  # Streamlit Dashboard with @st.cache_data & 5 interactive tabs
├── data_engine.py          # Real-market data fetcher (Yahoo Finance .KA & PSX DPS with PKT tz)
├── signal_engine.py        # Unified strategy logic, indicators, checklists & card formatters
├── chart_engine.py         # 3-tier Plotly candlestick chart with trade level overlays
├── backtester.py           # Multi-target backtester with ambiguous bar detection & fee deduction
├── signal_tracker.py       # Persistent SQLite ledger (signals.db) with WAL concurrency
├── telegram_notifier.py    # Telegram bot with daily scans, lifecycle updates & /commands
├── test_engine.py          # Automated verification test suite
├── signals.db              # SQLite persistent trade ledger (WAL enabled)
└── .github/workflows/
    └── daily_psx_signals.yml # Autonomous GitHub Actions cloud runner with state persistence
```
