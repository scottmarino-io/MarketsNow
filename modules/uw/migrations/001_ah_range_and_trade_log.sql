-- Migration 001
-- (a) earnings_vol: record the after-hours traversal, not just close-to-close.
--     Rationale: NVDA 2026-08-26 printed a 13.46pt AH range (206.29-219.75) that
--     a single reaction_1d figure hides entirely.
-- (b) trade_log: pre-registered hypotheses, including ones not executed.
--     Separate table because a hypothesis is not a vol observation.

ALTER TABLE earnings_vol ADD COLUMN ah_high REAL;
ALTER TABLE earnings_vol ADD COLUMN ah_low  REAL;
ALTER TABLE earnings_vol ADD COLUMN notes   TEXT;

CREATE TABLE IF NOT EXISTS trade_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT NOT NULL,
    logged_at        TEXT NOT NULL,
    event_date       TEXT,
    structure        TEXT NOT NULL,
    legs             TEXT,
    limit_credit     REAL,
    max_risk         REAL,
    pop              REAL,
    short_delta_sum  REAL,
    be_low           REAL,
    be_high          REAL,
    ev_estimate      REAL,
    thesis           TEXT,
    position_taken   INTEGER NOT NULL DEFAULT 0,
    fill_credit      REAL,
    outcome          REAL,
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_log_ticker_event
    ON trade_log (ticker, event_date);

-- Prevent duplicate hypothesis rows on seed re-run, while still allowing
-- several distinct structures to be logged against the same event.
CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_log_unique
    ON trade_log (ticker, event_date, structure);
