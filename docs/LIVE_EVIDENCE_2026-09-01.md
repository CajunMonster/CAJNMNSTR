# September 1, 2026 Autonomous PAPER Session

This is a truthful, immutable competition checkpoint derived from CAJNMNSTR's durable journal
and authenticated Alpaca PAPER reconciliation. Losing results are retained as evidence.

## Session result

- Eligible five-minute epochs: 45
- Canonical Terra calls: 45
- Terra directions: 3 `LONG_PUT`, 42 `NO_TRADE`, 0 `LONG_CALL`
- Referee outcomes: 2 `APPROVE`, 1 `REDUCE`, 42 `ABSTAIN`, 0 `BLOCK`
- Actionable candidates: 3
- Completed autonomous PAPER positions: 2
- Realized lifecycle P&L: **-$92.00**
- Peak-to-final maximum drawdown: **0.224%**
- Final Alpaca PAPER equity: **$99,907.79**
- Session-loss authority remaining: **$1,908.00**
- End-of-session positions: 0
- End-of-session open orders: 0
- Broker-flat verification: passed for both lifecycles

## Completed lifecycles

1. Two SPY September 10 $759 puts were bought at $4.18 and sold at $4.09 after the
   fill-anchored 75-minute time stop. Realized P&L: **-$18.00**.
2. Two SPY September 9 $759 puts were bought at $4.23 and sold at $3.86 after deterministic
   structural thesis invalidation. Realized P&L: **-$74.00**.

The account was reconciled broker-flat before the 3:35 PM ET forced-flatten boundary. A later
`REDUCE` candidate was recorded but could not submit after that boundary.

## Equity reconciliation residual

The lifecycle fills derive $99,908.00 from the $100,000.00 session start, while Alpaca reports
$99,907.79. Authenticated account activity contains the five expected fill records, no `FEE`
activity, no other cash activity, and the account reports zero accrued fees. The **-$0.21**
difference is therefore retained as an explicit **unattributed broker-equity reconciliation
residual**; no explanation or forced equality is asserted.

## Operational follow-up

The strategy, Referee, selector, risk, position plan, and session-loss settings were not changed.
Operational cleanup addresses only clean end-of-session watchdog handling, stale lifecycle and
incident snapshots, and separation of current-session from competition-to-date dashboard metrics.
