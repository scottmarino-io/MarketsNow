-- NVDA Q2 FY2027 print, 2026-08-26 postmarket.
-- reaction_1d intentionally NULL: the T+1 close-to-close move is not defined
-- until Thursday 2026-08-27 close. 218.06 is an after-hours mark, not a close.

INSERT INTO earnings_vol (
    ticker, report_date, report_time,
    implied_move, implied_move_perc, rv_1d_last_12q,
    reaction_1d, price_at_report,
    ah_high, ah_low, notes
) VALUES (
    'NVDA', '2026-08-26', 'postmarket',
    10.625005, 0.050677, 0.695,
    NULL, 209.66,
    219.75, 206.29,
    'AH traversed 6.42% of spot vs 5.07% implied (range/implied = 1.27). Low -1.61%, high +4.81% (95% of implied). Mark 218.06 = +4.01%, ratio 0.79 vs 12q mean 0.695 -- above average, unfavourable observation for the RV<IV thesis. Broke a run of 5 consecutive negative reactions. Pre-print per-expiry net GEX was negative below spot / positive above; move went up, so the directional GEX lean did not hold.'
)
ON CONFLICT(ticker, report_date) DO UPDATE SET
    ah_high = excluded.ah_high,
    ah_low  = excluded.ah_low,
    notes   = excluded.notes;

INSERT INTO trade_log (
    ticker, logged_at, event_date,
    structure, legs,
    limit_credit, max_risk, pop, short_delta_sum,
    be_low, be_high, ev_estimate,
    thesis, position_taken, fill_credit, outcome, notes
) VALUES (
    'NVDA', '2026-08-26T19:45:00Z', '2026-08-26',
    'Iron condor 197.5/200 - 222.5/225, Aug-28 expiry (2 DTE)',
    '[{"leg":"long put","strike":197.5},{"leg":"short put","strike":200},{"leg":"short call","strike":222.5},{"leg":"long call","strike":225}]',
    1.03, 147.0, 0.53, 0.468,
    198.97, 223.53, -8.60,
    'Sell event premium into 2DTE expiry on the basis that realized has averaged 0.695x implied over 12 quarters. Both shorts sit essentially on the implied-move boundaries. Negative EV under market-implied probabilities; only identified edge is execution quality (~$6/condor mid-vs-paid-up).',
    0, NULL, NULL,
    'PRE-REGISTERED, NOT EXECUTED. Ticket staged at 1.03 limit and screenshotted pre-print, so this is a timestamped hypothesis rather than a post-hoc claim. Zero size means the execution edge -- the one real EV source identified -- went untested.'
)
ON CONFLICT(ticker, event_date, structure) DO UPDATE SET
    notes   = excluded.notes,
    outcome = excluded.outcome;
