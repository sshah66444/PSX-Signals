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
  reward/risk, plain-language reasoning and no-chasing instruction. WATCHING means
  no entry until missing conditions are confirmed on a later completed close.

## What changed

`daily_briefing.py` is a separate reporting path using your current signal engine.
It does not import or mutate `signals.db` or claim that a setup is an executed trade.
Every evening repeats the relevant shortlist, rather than suppressing an otherwise
useful briefing because a setup already exists in the trade ledger.

`psx_provider.py` reads the official PSX monthly historical table with real daily
OPEN/HIGH/LOW/CLOSE/VOLUME. It does not manufacture highs/lows from open and close.
The new path does not wait for Yahoo's individual-stock feed. The KSE-100 index uses
your existing DPS index adapter, with an exact source-session date check.

The configurable universe starts with 30 established symbols from your watchlist,
limiting cold-start download load. This is not a claim of verified present liquidity;
strategy liquidity filters still apply. Edit `briefing_config.json` to change symbols.
At least 80% of the configured universe and the benchmark must have verified data
for the exact completed session before candidate cards are released. Missing stocks
are identified. Unadjusted prices with large discontinuities are held for review.

The configured 2026 holiday calendar comes from PSX’s published page. Moon-dependent
holidays and exceptional closures need updating when PSX announces changes. The
worker fails closed when its calendar year expires.

## GitHub deployment

This is a **separate local copy**. Creating these files has not changed the running
repository or sent Telegram messages. To activate on `sshah66444/PSX-Signals`, add:

- `daily_briefing.py`
- `psx_provider.py`
- `briefing_config.json`
- `test_briefing.py`
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

The workflow caches monthly market files and commits `.state/briefing.sqlite3` after
each run. This file contains prepared reports, delivery states, Telegram message IDs
and a hash of the recipient ID; no bot token. Avoid publishing a personal repo's
journal. Concurrent jobs are serialized. State persistence failure or a runner crash
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
python daily_briefing.py --preview  # after 19:00 PKT; no Telegram sends or journal writes
python daily_briefing.py --tick     # production scheduled invocation; may send
python daily_briefing.py --daemon   # optional always-on host; background preparation
```

No flags means preview. Preview may populate the price cache, but cannot change trade
or delivery state. Set the same environment variables or use `.env` for local sends.
The existing venv uses Python 3.9/LibreSSL and emitted an SSL compatibility warning;
GitHub uses Python 3.11.

## Sources

- PSX history: https://dps.psx.com.pk/historical
- PSX holidays: https://www.psx.com.pk/psx/exchange/general/calendar-holidays
- GitHub scheduling limitations: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
