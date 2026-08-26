# Closing out the NVDA 2026-08-26 row

`reaction_1d` is deliberately NULL. The T+1 close-to-close move is not defined
until the **Thursday 2026-08-27 regular-session close**. The 218.06 figure used
in the notes is an after-hours mark, which is why it is recorded in prose rather
than in the numeric column.

Once Thursday closes, with `C` = NVDA closing price:

```sql
UPDATE earnings_vol
SET reaction_1d = (:C - 209.66) / 209.66
WHERE ticker = 'NVDA' AND report_date = '2026-08-26';
```

Then the ratio against implied is `reaction_1d / implied_move_perc`, i.e.
`reaction_1d / 0.050677`. Compare to `rv_1d_last_12q` = 0.695.

Reference points off the 209.66 close:

| Close | reaction_1d | ratio vs implied |
|-------|-------------|------------------|
| 206.29 | -1.61% | 0.32 |
| 213.00 | +1.59% | 0.31 |
| 218.06 | +4.01% | 0.79 |
| 219.75 | +4.81% | 0.95 |
| 222.50 | +6.13% | 1.21 |

## Resolving the trade_log row

The condor expires Friday 2026-08-28. Since `position_taken = 0`, `outcome`
should record the **counterfactual** P&L per contract so the hypothesis can be
scored without implying a fill:

- settle inside 200-222.5 -> +103
- settle at/above 225 or at/below 197.5 -> -147
- between -> linear interpolation across the breached wing

Keep `fill_credit` NULL permanently. That NULL is the record that the execution
edge went untested, and it is the field that separates a scored hypothesis from
a real trade.
