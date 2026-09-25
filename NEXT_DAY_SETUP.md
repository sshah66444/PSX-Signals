# Next-trading-day Telegram briefing

## Your selected behavior

- **Delivery target: 20:00 Asia/Karachi (Pakistan time).**
- **Horizon: trades held a few sessions.**
- **Host: GitHub Actions**, with its best-effort scheduling limitation.
- Prepare ahead of delivery; send a report every completed trading session, even if
  there are no qualifying setups. Friday reports name the next open session.
- Show both the source session and the intended trading session. Never relabel old
  prices as today’s report. No entry plan after the 21:00 Pakistan cutoff.
- Maximum three candidates, with conditional entry band, stop, two targets,
  reward/risk, plain-language reasoning and no-chasing instruction.

## What changed

`daily_briefing.py` is a separate reporting path using your current indicator engine.
It does not import or mutate `signals.db` or claim that a setup is an executed trade.
Every evening repeats the relevant shortlist, rather than suppressing an otherwise
useful briefing because a setup already exists in the trade ledger.

The official PSX market-summary page supplies the completed session's real
OPEN/HIGH/LOW/CLOSE/VOLUME and current KSE-100 close/breadth. Yahoo supplies only
earlier daily bars for indicators. The prior Yahoo close must match PSX's LDCP,
and missing or conflicting stocks are excluded. If the official page is not marked
closed with the expected date, the bot withholds all entry plans. The report does
not claim historical KSE-100 relative strength, because that feed was unavailable
in the live review.

A candidate needs a fresh 20-session closing breakout, rising EMA20/EMA50,
improving MACD, bounded RSI, volume at least 1.2 times its 20-session average,
and nonnegative market breadth. These are conditional short-horizon setups, not
predictions or orders. On the 2026-09-24 live validation of the original
30-symbol list, 27 stocks were verified and no setup qualified; three stocks
had missing or conflicting history. BOP was subsequently added to the watchlist,
but the expanded list was not live-validated because Yahoo rate-limited the replay.

The separate **dip watchlist** is observation only. It looks for a recovering
share near recent support, with RSI 35–60 and rising, improving MACD, adequate
average volume, and a close below its recent high. It shows a level to monitor on
a later completed close and an invalidation level. It never prints an entry band
or a buy instruction. This is a new, unvalidated heuristic inspired by the
broker reports dated 21, 23, and 24 September 2026; agreement with a broker
is not evidence of profitability. Paper-track outcomes before using it for trades.

## Broker-call scorecard

`broker_scorecard.py` reads manually entered calls in `broker_calls.json` and
daily OHLC bars in `broker_prices.csv`. The six calls from the supplied JS Global
reports are seeded, with dated source references. Run `python broker_scorecard.py`
to see each call's first-target or stop outcome. The GitHub workflow records a
completed official PSX bar for every symbol in the calls file each evening and
commits the updated CSV with the delivery journal. Add new calls to the JSON
before their report-date session; later additions need missing prices backfilled
before their outcome can be scored.

The scorecard **assumes the report was available before that date's market open**
and buys at the open, even though the broker says "buy on dips" and does not give
an executable entry. It exits at the first target or stop, uses the open for a gap,
and assumes the stop came first if both levels fall within one day's high/low.
It excludes fees, spread and slippage. Repeated calls on the same share are
counted separately and can represent overlapping exposure. This is a comparison
method, not a record of actual trades or evidence of strategy profitability.

The configurable universe starts with 31 symbols from your watchlist and the
broker comparison (which added BOP),
limiting cold-start download load. This is not a claim of verified present liquidity;
strategy liquidity filters still apply. Edit `briefing_config.json` to change symbols.
At least 80% of the configured universe and the official market summary must have verified data
for the exact completed session before candidate cards are released. Missing stocks
are identified. Unadjusted prices with large discontinuities are held for review.

The configured 2026 holiday calendar comes from PSX’s published page. Moon-dependent
holidays and exceptional closures need updating when PSX announces changes. The
worker fails closed when its calendar year expires.

## GitHub deployment

This is a **separate local copy**. Creating these files has not changed the running
repository or sent Telegram messages. To activate on `sshah66444/PSX-Signals`, add:

- `daily_briefing.py`
- `briefing_config.json`
- `requirements.txt` (adds Beautiful Soup and journal encryption)
- `test_briefing.py`
- `broker_scorecard.py`, `broker_calls.json`, `broker_prices.csv`, `test_broker_scorecard.py`
- `state_crypto.py`, `test_state_crypto.py`
- `.github/workflows/next_day_briefing.yml`
- `.github/workflows/briefing_tests.yml`
- `.github/workflows/daily_psx_signals.yml` (disables the old scheduled sender)
- `NEXT_DAY_SETUP.md`

Keep the existing `data_engine.py`, `signal_engine.py`, dashboard, and live ledger.
The new module uses the reviewed September 22 strategy API.

Use existing repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The new
personal briefing requires one recipient, not a comma-separated broadcast list.
Repository workflow permissions must allow the action to push its journal commit.
Protected default branches may require a dedicated state store instead; a failed
state push must be fixed before relying on deduplication across runners.

The workflow warms data at **19:05 and 19:35 PKT**, attempts delivery at **20:00**, then
retries at **20:20, 20:40, and 20:55**. GitHub may start any of these late or drop a
scheduled job. Install/setup time also shifts receipt slightly. This is an 8 p.m.
target, not a guaranteed deadline. An always-on worker is needed for tighter timing.
Only the new workflow should be scheduled, or the old bot can still send stale digests.

The workflow encrypts the delivery journal and commits only
`.state/briefing.sqlite3.enc` after each run. It contains prepared reports,
delivery states, Telegram message IDs and a hash of the recipient ID. The
encryption key is derived from the two Telegram secrets, which are never
committed. Rotating either secret requires securely migrating the journal first;
otherwise the old encrypted state cannot be opened and delivery stops. Concurrent
jobs are serialized. State persistence failure or a runner crash
between sending and saving can still create duplicate-delivery risk; GitHub runners
are not an exactly-once delivery system.

## Retry behavior

At the delivery target:

- Prepared, verified report: send once for that source session.
- Data incomplete: send one pending notice, then retry. No stale candidate cards.
- Data still incomplete after cutoff: a late job emits one failure notice, not a
  next-day plan. If GitHub never starts a job, it cannot emit any notice.
- Explicit Telegram rejection: retry on a later run.
- Network timeout with unknown delivery outcome: retain `uncertain` and require
  review before resending, to avoid silently duplicating a possibly delivered plan.

Inspect GitHub Actions failures and uncertain journal entries. Successful send
responses are saved per session. Failed delivery never marks a report as delivered.

## Local commands

Use Python 3.11+ with the existing requirements:

```bash
python -m unittest -v test_briefing
python -m unittest -v test_broker_scorecard
python -m unittest -v test_state_crypto
python broker_scorecard.py       # hypothetical broker-call scorecard
python daily_briefing.py --preview  # after 19:00 PKT; no Telegram sends or journal writes
python daily_briefing.py --tick     # production scheduled invocation; may send
python daily_briefing.py --daemon   # optional always-on host; background preparation
```

No flags means preview. Preview cannot change trade or delivery state. Set the
same environment variables or use `.env` for local sends.
The existing venv uses Python 3.9/LibreSSL and emitted an SSL compatibility warning;
GitHub uses Python 3.11.

## Sources

- PSX market summary: https://www.psx.com.pk/market-summary/
- PSX holidays: https://www.psx.com.pk/psx/exchange/general/calendar-holidays
- GitHub scheduling limitations: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
