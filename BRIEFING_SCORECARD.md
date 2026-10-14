# Briefing rule validation

`briefing_performance.py` scores the **next-day Telegram briefing**, not the
older Streamlit `signal_engine.py` strategy. The live workflow records each
completed PSX session's OHLCV and market breadth in the encrypted delivery
journal, then prints the delivered-signal scorecard in the Actions log. Only
setups from a Telegram plan with confirmed `sent` status are included.

## Forward scorecard

Each delivered breakout is evaluated from the next trading session's opening
price. A delivered conditional dip setup first requires an opening within its
zone and a trade through its trigger during that session; otherwise it expires
unfilled. Daily bars do not reveal the order of intraday prices, so a trigger
and stop on the same session count conservatively as a stop. The first stop or
TP1 touch within four completed sessions closes
the hypothetical trade. If both touch on one day, the stop is counted first;
gaps beyond a level use the opening price. Trades still open after four
sessions exit at the fourth close. The reported net return subtracts an
assumed 0.35% round-trip cost. It cannot prove a real fill, account for exact
order-book slippage, or predict future returns. Breakout and dip results are
reported separately; a conditional dip is never counted as a fill merely
because it appeared in a message.

The journal starts collecting outcome bars when this upgrade is deployed.
Existing Telegram messages are retained, but their earlier prices are not
silently reconstructed. Until enough filled setups have closed, the report
states that an edge has not been established.

Run against an **unlocked** local journal:

```sh
python briefing_performance.py --forward-report
```

The live workflow unlocks and relocks its journal automatically. Do not commit
the plaintext SQLite file.

## Historical replay

Provide genuine dated OHLCV bars for all configured watchlist symbols and
official advancing/declining counts for each source session. The replay calls
`daily_briefing.evaluate_candidate` and `evaluate_dip_watch` directly and
applies the live 80% coverage and top-three-per-strategy rules. It skips days
lacking market breadth; it does not assume a favourable market. Each setup is
simulated using later bars only.

Price CSV columns: `date,symbol,Open,High,Low,Close,Volume`.
Breadth CSV columns: `date,advancing,declining`.

```sh
python briefing_performance.py --replay \
  --prices verified_psx_daily_prices.csv \
  --breadth verified_psx_daily_breadth.csv
```

The replay deliberately requires a supplied historical dataset. The older
`backtester.py` calls a different strategy and must not be presented as proof
that the evening Telegram rule works. Keep a later date range untouched while
developing rules, then use it for an out-of-sample check.
