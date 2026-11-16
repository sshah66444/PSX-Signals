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

## 📐 Ledger Integrity & Attribution (Stage 0)

Signals and trades are two different populations, and mixing them made every
performance number wrong. A setup that never triggered has **no P&L**, yet it used to
sit in the `/performance` denominator — and a TP1 row fell out of the audit query
entirely, so a runner stayed "open" forever. Both are fixed at the ledger level:

* **TP1 runners resolve.** After half the position is booked at TP1 the stop moves to
  breakeven, but the runner is still audited. It now closes at `TP1_BE` (breakeven
  stop) or `TP2_HIT`, and `finalize_partials()` marks any leftover at the latest
  *verified* close (`PARTIAL_CLOSED`). No verified close → the row is left open and
  reported, never guessed.
* **Stale setups expire.** `resolve_stale()` marks `WATCHING` rows that never triggered
  within `STALE_WATCH_TRADING_DAYS` (15, Mon–Fri) as `EXPIRED` with `net_pnl_pct`
  deliberately `NULL`. They are not trades and can never enter a win rate again.
* **Every trade is measured against the benchmark.** `benchmark_pnl_pct` and
  `excess_pnl_pct` compare net P&L with KSE-100 over the identical holding window
  (`backfill_benchmark_returns()`), because +4% is a loss when the index did +7%.
* **`initial_stop_loss`** preserves the pre-TP1 stop, so R-multiples survive the
  breakeven move.
* **`closed_reason`** records *why* a row closed: `STOP`, `STOP_AMBIGUOUS`, `TP1_BE`,
  `TP2`, `PARTIAL_MARK`, `EXPIRED`, `INVALIDATED`.

`/attribution` reports expectancy, profit factor, payoff, max drawdown, average hold,
excess return and per-archetype / state / grade / sector buckets — and it refuses to
let you tune on noise: anything under `MIN_SAMPLE_TRADES` (30) is flagged
`⚠️n<30`, with an explicit warning rather than a confident-looking number.

**Measured on the live ledger at the time this landed** (16 Sep – 4 Nov 2026):
166 signals → 14 entered → **11 settled**: expectancy **−2.26%/trade**, win rate
9.1%, profit factor 0.19, max drawdown −19.97%. Ten setups expired without ever
triggering. The honest conclusion is that the sample is far too small to tune, and
that the one archetype producing all the flow (`MEAN_REVERSION`) has no demonstrated
edge after costs.

---

## 🎯 Stage 1: Exit Profile & Out-of-Sample Reality Check

### The asymmetry that was fixed

The live ledger held every loser until the stop or a 2.5–4.5R target: 10 stop-outs at
−3.1% held **23.8 trading days** against a single +5.95% winner. Three changes, all in
position management rather than entry logic:

* **TP1 is now a scale-out at 1.5R** (was 2.5R). Both legs move together
  (`tp1_risk_multiplier` *and* `tp1_atr_multiplier`) because the engine takes
  `max(risk_mult x risk, atr_mult x ATR)` — changing one alone would have left TP1
  at 2.5 ATR. The hard R:R gate (`min_rr_tp1 = 1.5`) still passes by construction.
* **Time stop on no progress.** Once `time_stop_bars` (15 for swing) have elapsed with
  a best excursion under `MIN_TIME_STOP_MFE_R` (0.5R) and TP1 never reached, the
  position closes at the close (`TIME_STOP`). Dead money stops bleeding.
* **Trailing runner.** After TP1 the stop ratchets up to the mode's trailing EMA
  (50 for swing), never below breakeven, once `min_hold_before_trail` bars have
  passed since TP1. Exits record `TRAIL_STOP`, plus a hard `HOLDING_CAP`.

Every exit now writes `closed_reason`, so `/attribution` can show which exit type
actually makes or loses the money.

### The reality check

`walkforward.py` replays the **live engine** bar-by-bar over cached history
(`.cache_2y/`, then `.cache/`), with a per-date market regime (no look-ahead), next-open
entries, unfillable >7.5% gap-ups skipped, and the exits above. It then re-runs the
whole simulation for **matched random entries** — same symbol, same fold, same price
distances to stop/TP1/TP2 — 200 times, and reports the p-value of the real result
against that null distribution.

```
python walkforward.py --folds 3 --iterations 200 --json wf_report.json
```

Result on the uniform 2-year cache (71 symbols, 18 Nov 2024 → 13 Nov 2026, **1,361
trades**, identical coverage in every fold):

| Window | Trades | Expectancy | Win rate | Profit factor | Random entries |
|---|---|---|---|---|---|
| Pooled | 1361 | **−0.06%** | 35.5% | 0.98 | **+0.49%** |
| Fold 1 (2025-02 → 2025-09) | 420 | +1.15% | 39.3% | 1.40 | +1.70% |
| Fold 2 (2025-09 → 2026-04) | 466 | −0.47% | 33.7% | 0.85 | +0.38% |
| Fold 3 (2026-04 → 2026-11) | 475 | −0.72% | 33.9% | 0.73 | −0.45% |

**Verdict: no edge — p = 0.99, and matched random entries did better (+0.49% vs
−0.06%).** The signal sits at the bottom of the random distribution, not the top.

An earlier run on the *mixed* cache (12 symbols with two years, 62 with about six
months) showed +0.38%/trade and p = 0.055. `refresh_cache.py` exists precisely to test
whether that was regime or coverage — it was coverage. On equal data the apparent edge
disappears and reverses. The old number stays documented because it is the cleanest
example of why this harness exists: a promising result evaporated once the comparison
was made fair. (The `maxDD` column compounds every trade at full size; it is a
sequence artifact, not a portfolio drawdown.)

What this means in practice: Stage 1 made the *exits* honest and the *measurement*
auditable, but the **entry logic has no demonstrated edge**. Do not add agents, size up
or tune parameters on this evidence. The next honest step is to change what is tested
(different universe, different archetype weighting, a longer window) rather than
re-cutting the same rules.

---

## 🗓 Stage 2: The Event-Risk Agent (veto only, and measured)

Stage 1 showed the entry logic is indistinguishable from matched random entries
(p = 0.055) and regime-dependent. The first agent that earns its place is therefore
**not** another opinion generator — it is the one input technicals cannot see: the
corporate calendar.

### What it does

`event_risk.py` is a deterministic gate with veto rights. It classifies stored
announcements, scores them, and **cites the row it acted on**:

| Verdict | Window | Effect |
|---|---|---|
| `BLOCK` | results ±2d, board meeting ±1d, book closure ±2d, ex-dividend ±2d, right/bonus ±3d, suspension ±30d, material notice ±1d | `BUY_NOW`/`BUY_ZONE` → `AVOID` |
| `WATCH` | the escalate window (results 6d, AGM 2d, …) | `BUY_NOW`/`BUY_ZONE` → `WATCH` |
| `CLEAR` | nothing inside the window | passes through untouched |

The session **after** an event is still blocked (`LOOKBACK_DAYS`), because that is
where the gap repricing lands. No stored event → `CLEAR`, and the assessment says
`events_known: 0` so silence stays visible instead of implying safety.

Every downgrade is applied by `apply_event_gate()` (pure, returns a copy), which keeps
`gate_downgraded_from` so a card can never be mistaken for a fresh signal, appends the
reason to `blockers`, and prefixes the trigger note. The daily scan reports
`event-gated: N` in its header, and `/events OGDC` shows the verdict plus the next
90 days of the calendar.

### Data reality (the honest part)

PSX's own endpoints do **not** serve announcements to scripts: the page is a JS shell
and the underlying `POST dps.psx.com.pk/announcements` answers **403**. The working
source is the SCS Trade announcement grid
(`POST scstrade.com/MarketStatistics/MS_Announcements.aspx/chartact`), which returns
real JSON rows: company code, board-meeting date, quarter, EPS, declared dividend,
bonus, right %, book-closure/ex-dividend date and the PSX PDF link.

`parse_scstrade_announcements()` turns one row into up to four gate events, because
they carry different risk windows — the board meeting (results date), the book closure
(ex-dividend), a bonus issue and a right issue.

Resolution order, so nothing is ever invented:

1. `--refresh` → SCS Trade feed (primary), then PSX pages as fallback. On total failure
   it reports why and **leaves the store untouched**.
2. `--import page.html` / `--import events.json` — saved page or export.
3. `--add OGDC RESULTS 2026-11-20 --headline "..."` — seed a known event by hand.
4. `--check OGDC MEBL` — print verdicts.

Store: `.state/events.json` (atomic writes, deduped). The first live refresh (16 Nov
2026) imported **20 real watchlist events** (17 results, 3 ex-dividend, 28 Oct → 19 Nov)
and the gate immediately did its job: `SNGP — WATCH`, ex-dividend 30% in 3 days.
Vetoes are appended to `.state/event_gate_log.jsonl` — blocked setups never reach the
trade ledger, so this is the only way to price them later.

### The measurement

The announcement archive is not available historically, so the gate was evaluated with
an **explicit proxy**: block entries when the last 5 bars contain a ≥5% session or
≥2.5× median volume (i.e. "something just happened here").

```
python walkforward.py --cache .cache_2y --event-proxy --folds 3 --iterations 200
```

| Cache | No gate | With proxy gate | Random entries | p |
|---|---|---|---|---|
| Mixed (450 trades) | +0.38% | **−0.54%** | −0.10% | 0.851 |
| Uniform 2y (1,361 → 765 trades) | −0.06% | **+0.36%** | +0.46% | 0.682 |

**The proxy's effect flipped sign between the two data sets** — it hurt on the mixed
cache and helped on the uniform one, and even when it "helped" it still finished below
matched random entries (+0.36% vs +0.46%, p = 0.68). An unstable effect is not a
finding, so the proxy justifies nothing either way.

That fixes the design rule: the event gate stays **narrow** — scheduled corporate
events only (results, book closure, ex-dividend, right/bonus, suspension). Widening it
into a general "avoid recently active names" filter has no stable evidence behind it,
and the harness would reject it again.

What is still unproven is the *real* announcement gate, because no historical archive of
PSX announcements exists in the repo. `.state/event_gate_log.jsonl` is the answer to
that: each live veto is recorded with its symbol, verdict, original state and cited
event, so after a few months the blocked names can be priced against the taken trades
and the gate can be judged on evidence rather than intent.

What is still unproven is the *real* announcement gate, because no historical archive of
PSX announcements exists in the repo. That is what `.state/event_gate_log.jsonl` is for:
each live veto is recorded with its symbol, verdict, original state and cited event, so
after a few months the blocked names can be priced against the taken trades and the gate
can be judged on evidence rather than intent.

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
├── attribution.py          # Settled-trade attribution, excess vs KSE-100, sample guards
├── walkforward.py          # Out-of-sample replay + matched random-entry null model
├── refresh_cache.py        # Downloads one uniform history window for the watchlist
├── event_risk.py           # Announcement store, event verdicts, veto gate & forward log
├── telegram_notifier.py    # Telegram bot with daily scans, lifecycle updates & /commands
├── run_swing_scan.command  # macOS double-click launcher for a live swing scan
├── test_engine.py          # Automated verification test suite
├── test_ledger_integrity.py # Stage-0 ledger tests (isolated temp DB, never signals.db)
├── test_stage1.py          # Exit profile + walk-forward/null-model tests
├── test_stage2.py          # Event gate: classification, store, verdicts, parser, log
├── signals.db              # SQLite persistent trade ledger (WAL enabled)
└── .github/workflows/
    ├── daily_psx_signals.yml # Telegram /command polling runner
    └── swing_signals.yml     # Daily 20:45 PKT swing-scan delivery (+ 21:45 PKT retry)
```
