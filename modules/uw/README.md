# `modules/uw` — Unusual Whales capture layer

## Why this is a capture layer and not a fetcher

The UW subscription is one month. Every other data source in MarketsNow (FRED,
yfinance, Massive) is either free or ongoing. If UW gets wired into
`modules/screener/fetchers.py` alongside them, the Wheel and Stress pages break
on day 31.

So this module inverts the dependency. `capture.py` writes to a local SQLite
store; `store.py` reads from it. **No Streamlit page imports `client.py`.**

The reason this works rather than just deferring the problem: the Wheel
screener's real question is *"is this premium rich?"*, and that's a percentile
against a trailing distribution. Once the distribution is captured, you can
percentile a **live** IV reading from any source. UW is needed to build the
baseline, not to use it. The baseline ages slowly — a 252-day IV distribution is
still informative months later.

## Layout

| File | Role |
|---|---|
| `schema.sql` | DDL. Four data tables plus `capture_log` for resumability. |
| `client.py` | Auth, token-bucket rate limiting, retry/backoff, envelope unwrapping. |
| `capture.py` | Backfill + daily incremental. Resumable, idempotent. CLI entry point. |
| `store.py` | Read layer. The only module pages should import. |

## Before first run

**Verify `PATHS` in `client.py`.** The route map is the one thing not confirmed
against the live API. Get exact paths from:

```bash
curl -H "Accept: text/plain" https://api.unusualwhales.com/docs
curl https://api.unusualwhales.com/api/openapi
```

or the MCP server's `get_public_api_docs` tool. Nothing outside `PATHS`
hardcodes a route, so corrections are a single-dict edit.

Then add `UW_API_KEY` to `.env` and surface it through
`modules/shared/api_keys.py` for consistency with the existing keys.

## Running

```bash
python -m modules.uw.capture init                  # apply schema
python -m modules.uw.capture backfill --days 500   # the important one
python -m modules.uw.capture status                # coverage + errors
python -m modules.uw.capture daily                 # cron this until the sub lapses
```

Backfill upserts on `(ticker, date)`, so a failure mid-run costs API calls on
re-run but never duplicates or corrupts. `--only vol_state` narrows the task if
one endpoint is misbehaving.

Scale: ~170 tickers from `modules/screener/universe.py` × 500 days ≈ 85k rows.
Single-digit MB. Rate limiter defaults to 2 req/sec — conservative, raise it
once you've measured what your tier tolerates.

## Check this first: IV depth

`status` reports interpolated-IV coverage. The UW docs note that interpolated IV
older than roughly five open days may be plan-gated, while `iv_rank` is not.
Live testing showed IV populated at least 8 days back, but **500 days is
unverified** — coverage could be far shallower than the price history.

This matters because it caps every percentile in `store.py`. If coverage comes
back thin, `iv_rank` (ungated) becomes the primary baseline and
`volatility_30` becomes supplementary. `iv_baseline_depth()` exists so scoring
can guard on it — a percentile off 30 observations is not the same claim as one
off 500.

## Integration points

**Wheel Screener** — `store.vol_spread(ticker, current_iv)` returns implied,
realized, the spread, and the percentile. `VolSpread.is_rich` requires *both*
spread > 3 vol points *and* percentile ≥ 60. Both conditions matter: GDX
currently shows IV rank 57.8 with implied ~6 points **below** realized, so rank
alone would pass a name where you'd be selling cheap vol.

**Market Stress** — `store.gex_percentile("SPY")` as a composite factor. Worth
adding specifically because dealer gamma positioning is mechanically
uncorrelated with credit spreads and the yield curve, so it raises the
composite's effective rank instead of adding another column that moves with the
existing risk-off cluster. That was a named finding in the `agent.md` review.

**Momentum Screener** — nothing here. Its signals are price-derived and
computable from yfinance forever. Don't spend the window on data you can always
get free.

## Notes

Realized vol is computed from stored closes, not read from UW. UW's
`variance_risk_premium` field does not reconcile to (implied − trailing
realized) — it reads positive for SPY and QQQ on days when implied sits below
realized — so its definition is unclear and it isn't stored. `store.realized_vol`
is annualized close-to-close over a specified window; own definition, own
reproducibility.

`vrp_panel()` returns the cross-sectional IV-vs-RV snapshot across all captured
tickers. That panel is the actual point of the exercise: a single day's screen
across 20 names supports no conclusion, whereas 170 tickers × 500 days is enough
to test whether the variance risk premium is present in your universe and under
what conditions.
