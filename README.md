# Next-session briefing upgrade

Start with [NEXT_DAY_SETUP.md](NEXT_DAY_SETUP.md) for the new 8 p.m. Pakistan-time Telegram workflow. This copy preserves the previous dashboard; its original documentation follows.

The evening briefing's own outcome tracker and matching historical replay are
documented in [BRIEFING_SCORECARD.md](BRIEFING_SCORECARD.md). The older dashboard
backtester uses different signal rules.

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

### 4. Realistic PSX Market Microstructure Engine
* **Next-Day Open Execution ($T+1$ Open)**: Eliminates backtest lookahead bias by requiring that a signal generated on candle $T$'s close executes strictly on candle $T+1$'s Open.
* **PSX Daily Circuit Breakers ($\pm 7.5\%$ or PKR 1.00 Min)**:
  * Upper-circuit locked opens are rejected as unfillable due to zero market selling liquidity.
  * Overnight gap-downs and limit-down stop-outs are filled at the locked limit price rather than ideal stop levels.
* **Volume-Tiered Liquidity & Execution Friction**:
  * Real trading costs go beyond flat commissions. The engine calculates dynamic bid-ask spreads and slippage based on 20-day Average Daily Volume (ADV):
    * **Tier 1 (High Liquidity, $\text{ADV} \ge 2\text{M}$)**: 0.15% spread, 0.10% slippage.
    * **Tier 2 (Medium Liquidity, $500\text{k} - 2\text{M}$)**: 0.35% spread, 0.20% slippage.
    * **Tier 3 (Low Liquidity, $< 500\text{k}$)**: 0.65% spread, 0.35% slippage.

### 5. Institutional Statistical Rigor & Robustness Validation
* **1,000-Iteration Non-Parametric Bootstrap Resampling**: Replaces misleading point estimates with empirical 95% Confidence Intervals for Win Rate, Net P&L, and Profit Factor.
* **Sample Size Adequacy Gating ($N < 30$)**: Enforces statistical thresholds. Backtests with $N < 15$ are flagged as *Critically Low Sample*, and $N < 30$ are warned as *Low Statistical Confidence* to prevent overfitting to small-sample noise.
* **9-Point Parameter Sensitivity Matrix**: Perturbs key strategy variables (volume surge threshold, RSI bands, minimum R:R) across 9 permutations. Rates strategies as either **Stable Parameter Plateau** (robust edge) or **Fragile Needle Peak** (overfit to historical noise).
* **Rolling Walk-Forward Optimization (WFO)**: Cross-validates in-sample performance against out-of-sample forward sessions to verify that edge persists out-of-sample.

### 6. Positive-Expectancy Quant Engine & Multi-Target Backtester
* **Macro Market Gate (KSE-100 Index)**: Official 5-year historical KSE-100 feed (`dps.psx.com.pk/timeseries/eod/KSE100`). Suppresses long entries when KSE-100 trades below its 50 EMA (`MARKET_CORRECTION - Cash Preservation Mode`), avoiding fighting market beta.
* **Relative Strength (RS vs KSE-100)**: Quantifies stock performance relative to the index ($\text{RS} \ge \text{RS\_MA20}$), filtering for equities receiving institutional sponsorship.
* **Volatility Compression Squeeze**: Measures Bollinger Bandwidth against 60-day compression minimums to ensure breakouts originate from tight bases rather than erratic exhaustion moves.
* **Disciplined Time-Stops (4-Bar Rule)**: Automatically exits stalled trades after 4 sessions without follow-through, eliminating dead-capital drag and mitigating drawdown.
* **Realistic Scale-Out Model**: 50% profit booked at TP1, Stop Loss trailed to Breakeven, remaining 50% trails toward TP2.
* **Ambiguous Candle Detection**: Bars that touch both Target and Stop Loss on the same day are flagged as ambiguous and conservatively counted as stopped out. Same-bar retracements to breakeven after TP1 are accurately captured.
* **Real Trading Costs**: Automatically factors in customizable PSX round-trip broker commissions and CDC/SECP taxes (default: 0.35%) plus dynamic volume-tiered liquidity friction.

### 7. Persistent SQLite Signal Ledger (`signals.db`)
* Database tracks setups across their entire lifecycle: `WATCHING` $\rightarrow$ `TRIGGERED` $\rightarrow$ `TP1_HIT` $\rightarrow$ `TP2_HIT` / `STOPPED_OUT` / `EXPIRED`.
* Configured with **SQLite WAL (Write-Ahead Logging)** mode and 30-second busy timeouts for concurrent access between Streamlit UI sessions and background Telegram bots.
* Deduplicates unchanged setups so daily notifications only announce fresh triggers or lifecycle events.

### 8. Autonomous Telegram Bot with Interactive Commands
* **Daily Morning Scan**: Automatically audits active setups and alerts on newly confirmed triggers.
* **Interactive Command Handler**:
  * `/scan` — Trigger an immediate market-wide scan across all 75 stocks.
  * `/active` — View all open, watching, and triggered setups in the ledger.
  * `/stock <SYM>` — View full setup card for any symbol (e.g. `/stock OGDC`).
  * `/why <SYM>` — View the complete condition checklist breakdown.
  * `/performance` — View audited live ledger performance after costs.
  * `/watchlist` — List all 75 tracked PSX equities.

### 9. Tiered Signal Model (v2): BUY NOW / BUY ZONE / WATCH

Every setup is graded on **conviction** instead of being gated on an all-or-nothing checklist:

| State | Meaning |
|---|---|
| 🟢 **BUY NOW** | Every hard risk gate passes, the entry trigger is met, conviction ≥ 80 |
| 🟡 **BUY ZONE** | Price is inside the entry zone and every risk gate passes — the card prints the exact trigger price |
| ⚪ **WATCH** | Setup forming; kept in the dashboard/ledger, not sent to Telegram |
| ⛔ **AVOID** | A hard risk gate failed (approximated OHLC, thin liquidity, R:R below the mode minimum, absurd stop distance) |

**Hard gates** (the only vetoes): verified intraday OHLC, 20-day liquidity ≥ 80k shares,
reward:risk ≥ the mode minimum (swing 1.5:1; tactical 2:1 to TP2), stop distance between
0.4% and 12% of price, RSI not overbought.

**Evidence** (weighted, 0–100): trend alignment, relative strength, volume, MACD, RSI,
volatility compression, proximity to the trigger, candle confirmation. Missing
confirmations cost points — they do not veto. Relative strength is excluded from scoring
while the KSE-100 benchmark feed is stale.

**Entry archetypes** — all three can reach BUY NOW / BUY ZONE:

* **Breakout** — close above the range high with volume.
* **Momentum (breakout continuation)** — price already 0.5–12% above the 20-day high with
  price above both EMAs and the 20 EMA rising. Without this, anything that ran past its
  breakout level was reported as "Consolidating / Choppy" and silently ignored — which is
  exactly what happens in a fast recovery.
* **Pullback (shallow)** — uptrend intact, price tags the 20 EMA.
* **Pullback (deep)** — long-term uptrend intact, price retraces into the 50 EMA.
* **Mean reversal** — oversold at support with a reversal candle and ≥ 1.5:1 to TP1.

**Graded macro gate** — `bull` (index above its 50 EMA) and `early` (back above its 20 EMA)
allow longs; `correction` (below the 20 EMA) and `bear` (below the 200 EMA) block them. The
previous binary "index above its 50 EMA" veto kept the engine silent through every recovery,
because the index has to first reclaim a *falling* 50 EMA.

**Delivery cap** — Telegram sends at most `PSX_MAX_CARDS` (default 12) cards per run, ranked
by conviction; the header reports the full count (e.g. "35 alerts — showing top 12"), and
everything is always recorded in the ledger and visible in the dashboard.

Each card carries the conviction score, a grade (A requires ≥ 1.5:1 to the first target),
the exact buy trigger, stop, targets and the full gate/evidence breakdown. Setups upgrade
`WATCH → BUY ZONE → BUY NOW`, and each step notifies once. Backtests enter on both
`BUY_NOW` and `BUY_ZONE` (`entry_states` parameter), matching what the bot tells you to do.

### 10. Data Feeds & Failure Handling

| Feed | Role | Status |
|---|---|---|
| **PSX market-summary (HTML)** | Official EOD snapshot: LDCP/Open/High/Low/Current/Volume for ~688 listed symbols | ✅ Scraped once per trading day, imported first |
| **Yahoo Finance (`.KA`)** | Deep price history for indicator warm-up | ✅ 69 of 75 watchlist symbols, verified true OHLC |
| **PSX Data Portal JSON API** | Official timeseries + KSE-100 history | ❌ Retired: `/timeseries/*` answers 404/403, downloads are CSRF-gated |
| **Equal-weight proxy** | Macro regime + relative-strength benchmark | ✅ Built from the watchlist itself |

The snapshot is the important one: PSX's JSON API may be gone, but the public
market-summary page still publishes the exchange's own closing prices for the
whole market. `psx_snapshot.py` parses it (only when the session banner says
**Closed**, so intraday prices are never stored as EOD), appends one bar per
symbol to the cache and marks it `PSX official EOD`, which outranks every other
source.

Because a snapshot yields **one bar per day**, symbols that Yahoo never carried
(`ENGRO`, `FFBL`, `SHEL`, `CYNERGICO`, `GLL`, `IPAK`) accumulate history from the
first run onward: they become signal-capable after ~20 sessions (RSI/ATR warm-up)
and fully comparable after ~50 (50 EMA). Symbols Yahoo already covers keep their
deep history and simply gain today's official bar.

Because PSX withdrew its public history API, the bot no longer depends on it:

* **Benchmark fallback** — if the official KSE-100 series is missing or older than a week,
  `build_synthetic_benchmark()` rebases every fetched symbol to 100 at its first session and
  averages them into an equal-weight index. Regime rules, macro gate and relative strength
  run on that proxy. Telegram labels it `📈 Market Proxy (equal-weight, N names)`.
* **Circuit breaker** — a 404/403 (or three consecutive failures) marks the DPS endpoint
  retired for the process, so a 75-symbol scan makes **one** attempt instead of 150.
* **Ticker drift** — five watchlist entries had gone stale: `ENGRO → ENGROH`,
  `CYNERGICO → CNERGY` and `GLL → GGL` were renamed, while `FFBL` and `SHEL` were
  delisted and removed. `audit_watchlist()` compares the watchlist against the live
  PSX listing, and `--check-feeds` prints coverage, so this cannot silently rot again.
* **Coverage today** — 71 of 73 tracked symbols are usable. `ENGROH` and `IPAK` have
  no Yahoo history and accumulate from the daily snapshot until they own a contiguous
  run (20+ sessions).
* **Archived backfill (optional)** — `python wayback_backfill.py --symbols ENGRO --snapshots 30`
  recovers bars from Internet Archive captures of the retired API and of historical
  market-summary pages. Good for history and backtests; a series with a multi-week
  hole is never fed to indicators (`_trim_at_gaps` cuts at the last gap).
* **Health check** — `python telegram_notifier.py --check-feeds` prints Yahoo coverage, DPS
  status and the benchmark source.

If PSX restores or licenses the feed, no code change is needed for the official index — the
proxy is only used when the official series is stale or missing.

### 11. Importing Broker EOD Data (JS Global / any broker)

If you have a brokerage account, its EOD exports are the highest-quality source
available and they cover symbols Yahoo has dropped (`ENGRO`, `FFBL`, `SHEL`,
`CYNERGICO`, `GLL`, `IPAK`).

```bash
# 1. Inspect an unfamiliar export — shows how columns were detected
python import_broker_data.py --file jsg_eod.csv --list-columns

# 2. Dry run (parse + report, writes nothing)
python import_broker_data.py --file jsg_eod.csv --symbol OGDC --dry-run

# 3. Import one file, or a whole folder of per-symbol files
python import_broker_data.py --file jsg_eod.xlsx --sheet EOD --symbol OGDC
python import_broker_data.py --dir ~/Downloads/jsg_exports
```

The importer auto-detects headers (`Close` / `Close Price` / `LDCP` / `Last`,
`Volume` / `Volume Traded` / `Turnover`, …), parses day-first dates
(`dd/mm/yyyy`, `dd-Mon-yyyy`, epochs), infers the ticker from the filename
(`OGDC.csv`, `2026-10-31_OGDC.csv`) or a symbol column, and merges into the
existing cache without truncating history.

Imported symbols get a `.cache/<SYM>_broker.json` marker, after which
`fetch_psx_stock()` **prefers** that data over scraped sources (freshness rules
still apply). Close-only exports are accepted but flagged as approximated, so
they can never trigger a live entry.

Set `PSX_CACHE_DIR` to import into an isolated cache (useful for testing).

### 12. Running Fully Unattended

The scan itself needs nothing from you: `.github/workflows/swing_signals.yml`
runs it in the cloud at **20:45 PKT** every trading day (with a 21:45 retry),
delivers to Telegram and commits the ledger. Your Mac does not have to be on and
no local cron/launchd job is involved.

To make the **broker feed** equally hands-off, point the app at a folder once:

```env
# .env (local runs)
PSX_BROKER_DIR=/Users/you/Dropbox/jsg_reports
```

Any export that appears there (browser download, cloud-sync, SFTP mirror) is
imported automatically at the start of each scan — **only new or changed files**,
tracked by `.cache/broker_import_state.json`. You never run the importer manually.

| Piece | Manual? |
|---|---|
| Daily scan + Telegram delivery | No |
| Broker file → cache → signals | No (drop the file, nothing else) |
| Credentials / API keys (one-time) | Yes, once |
| Re-auth if the broker enforces SMS OTP | Yes — see below |

**Failure visibility:** if a scheduled run throws, it now sends
`⚠️ PSX scan FAILED` with the traceback to Telegram and marks the CI job failed,
so silence in your chat means "nothing to buy", not "the bot died".

The official PSX snapshot is refreshed automatically at the start of every scan
(`PSX_SNAPSHOT=0` disables it), so the cloud job needs no broker at all. The
broker folder is an optional extra layer, not a requirement.

#### Choosing an automation path for broker data

| What JS Global gives you | Automation | Notes |
|---|---|---|
| API key + secret (no interactive login) | **Fully unattended** | Store in GitHub Secrets, call from the workflow |
| Username/password API | **Unattended** | Automate login + token refresh; alert on expiry |
| Login with **SMS OTP** | Needs input per login | Ask for a data-only/service account, or a long-lived refresh token |
| Login with **authenticator-app 2FA** | **Unattended** | Store the TOTP seed secret and generate codes in code |
| Daily EOD file (portal download / email) | **Unattended** | Folder watch (above), or IMAP attachment pickup |
| Terminal/desktop app only | Fragile | Headless automation is possible but breaks on UI changes |

The blocker to avoid is **SMS-based OTP** — that is the one thing that genuinely
forces your involvement every time. Ask specifically for a service account or
refresh token when you request access.

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
# Verify bot credentials + chat delivery (sends one diagnostic message):
python telegram_notifier.py --test

# Swing-trading scan, delivered live to Telegram:
python telegram_notifier.py --mode swing_trade

# Preview cards without sending or touching the ledger:
python telegram_notifier.py --mode swing_trade --dry-run

# Start interactive Telegram command listener (/scan, /active, /stock, /why, /performance):
python telegram_notifier.py --check-commands
```

On macOS you can also double-click `run_swing_scan.command` for a live swing scan.

**Automatic daily delivery:** `.github/workflows/swing_signals.yml` runs the swing
scan at 20:45 PKT (15:45 UTC) every trading day, with a 21:45 PKT retry that is
skipped once the day's scan has already delivered. Long cards are chunked under
Telegram's 4096-character limit, rate-limited sends are retried using Telegram's
`retry_after`, and the ledger (`signals.db`) plus the delivery marker are committed
back to the repo so signals are never duplicated.

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
├── run_swing_scan.command  # macOS double-click launcher for a live swing scan
├── test_engine.py          # Automated verification test suite
├── signals.db              # SQLite persistent trade ledger (WAL enabled)
└── .github/workflows/
    ├── daily_psx_signals.yml # Telegram /command polling runner
    └── swing_signals.yml     # Daily 20:45 PKT swing-scan delivery (+ 21:45 PKT retry)
```
