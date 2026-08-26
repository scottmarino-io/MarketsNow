-- MarketsNow :: Unusual Whales capture store
--
-- Design intent: this store is the durable artifact. UW is a one-month
-- subscription; the app must keep working after it lapses. Nothing in the
-- Streamlit pages should call the UW API directly -- they read from here.
--
-- Realized volatility is deliberately NOT taken from UW. It is computed from
-- stored closes (see store.realized_vol) so the IV-vs-RV spread has a
-- definition we control and can reproduce. UW's own `variance_risk_premium`
-- field does not reconcile to (implied - trailing realized) and is not stored.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- Daily per-ticker volatility + flow state.
-- Source: ticker OHLC / day-state endpoint (up to 500 trading days back).
-- This is the backbone table -- IV rank percentiles and VRP both derive from it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vol_state (
    ticker                TEXT    NOT NULL,
    date                  TEXT    NOT NULL,          -- ISO yyyy-mm-dd

    -- price
    open                  REAL,
    high                  REAL,
    low                   REAL,
    close                 REAL,

    -- implied vol surface (interpolated). May be NULL on older rows
    -- depending on plan tier -- capture_log records how deep it went.
    iv_rank               REAL,                      -- 0-100, vs own trailing year
    volatility_30         REAL,                      -- annualized decimal, e.g. 0.126
    volatility_60         REAL,
    implied_move_30       REAL,                      -- dollars
    implied_move_perc_30  REAL,                      -- decimal
    implied_move_60       REAL,
    implied_move_perc_60  REAL,

    -- options flow aggregates
    call_volume           INTEGER,
    put_volume            INTEGER,
    call_premium          REAL,
    put_premium           REAL,
    net_premium           REAL,
    bullish_premium       REAL,
    bearish_premium       REAL,
    call_open_interest    INTEGER,
    put_open_interest     INTEGER,
    total_open_interest   INTEGER,

    captured_at           TEXT    NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS ix_vol_state_date   ON vol_state (date);
CREATE INDEX IF NOT EXISTS ix_vol_state_ivrank ON vol_state (ticker, iv_rank);


-- ---------------------------------------------------------------------------
-- Daily aggregate greek exposure per ticker.
-- Source: greek-exposure-by-ticker endpoint (accepts a lookback timeframe).
-- Feeds the Market Stress composite as a factor orthogonal to the FRED set.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gex_daily (
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,

    call_gex      REAL,
    put_gex       REAL,
    net_gex       REAL,                              -- call_gex + put_gex
    call_delta    REAL,
    put_delta     REAL,
    call_charm    REAL,
    put_charm     REAL,
    call_vanna    REAL,
    put_vanna     REAL,

    captured_at   TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS ix_gex_daily_date ON gex_daily (date);


-- ---------------------------------------------------------------------------
-- Named GEX levels per ticker per day.
-- Source: gex-levels endpoint, which accepts a `date` -- so it backfills.
-- `source` distinguishes directionalized-volume basis from open-interest basis;
-- they answer different questions and both are worth keeping.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gex_levels (
    ticker         TEXT NOT NULL,
    date           TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('vol', 'oi')),

    call_wall      REAL,
    put_wall       REAL,
    gamma_flip     REAL,
    gamma_magnet   REAL,
    spot           REAL,                             -- for flip-vs-spot distance

    captured_at    TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (ticker, date, source)
);


-- ---------------------------------------------------------------------------
-- Earnings event volatility context.
-- This is the dataset that answers "is the realized/implied ratio real."
-- rv_1d_last_12q is UW's own 12-quarter ratio; reaction_1d lets you rebuild it
-- yourself rather than trusting the composite.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS earnings_vol (
    ticker             TEXT NOT NULL,
    report_date        TEXT NOT NULL,
    report_time        TEXT,                         -- premarket / postmarket
    implied_move       REAL,                         -- dollars, as of T-1
    implied_move_perc  REAL,
    rv_1d_last_12q     REAL,                         -- UW trailing ratio
    reaction_1d        REAL,                         -- realized T+1 move, decimal
    price_at_report    REAL,

    captured_at        TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (ticker, report_date)
);


-- ---------------------------------------------------------------------------
-- Capture bookkeeping. Makes backfill resumable and records how deep each
-- endpoint actually returned data -- important because interpolated IV depth
-- is plan-gated and you want that recorded, not rediscovered.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capture_log (
    endpoint        TEXT NOT NULL,
    ticker          TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,                   -- ok | partial | error
    rows_written    INTEGER NOT NULL DEFAULT 0,
    earliest_date   TEXT,
    latest_date     TEXT,
    iv_earliest     TEXT,                            -- oldest row with non-null IV
    note            TEXT,
    ran_at          TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (endpoint, ticker, ran_at)
);

CREATE INDEX IF NOT EXISTS ix_capture_log_lookup
    ON capture_log (endpoint, ticker, ran_at DESC);
