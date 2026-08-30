# Pre-Monday SPY replay decision cycle

This checkpoint proves the CAJNMNSTR decision path with checked-in evidence only. It never
contacts Alpaca, never arms execution, and has no broker-coordinator dependency.

## Verified vertical slice

```text
checked-in SPY replay inputs
→ deterministic numeric features
→ OPEN Evidence Passport
→ Terra structured proposal
→ deterministic Referee decision
→ deterministic option selection when authority permits
→ complete SEALED Evidence Passport
→ stored Referee result
→ READY_FOR_OPERATOR_REVIEW or NOT_ELIGIBLE
→ STOP BEFORE BROKER SUBMISSION
```

The Referee decision is calculated before option selection. It is persisted only after the
complete Passport is sealed, preserving the existing sealed-Passport authority invariant.

## Deterministic evidence model

The calculator derives only values supported by the replay input:

- 5-, 15-, and 60-minute returns;
- previous-close gap;
- volume-weighted average price and above/below/at relationship;
- opening-range state using the first six five-minute bars;
- day-range location;
- relative volume only when expected volume at the replay decision time is present;
- annualized realized volatility from the five-minute close series;
- preferred-expiry contract count;
- ATM implied volatility;
- simple put-minus-call IV skew only when the nearest call and put share a strike;
- event-calendar state; and
- news context only when the fixture supplies an identified, evidence-backed item.

Missing required inputs, malformed bars, invalid bar ranges, a malformed option chain, or a
symbol outside SPY are hard data failures. Market or option observations older than five
minutes relative to the replay clock are stale. Both conditions are journaled with protective
action and receive `BLOCK`.

## Terra boundary

Terra receives the calculated Evidence Passport, not raw arithmetic work. Its strict schema is:

- `LONG_CALL`, `LONG_PUT`, or `NO_TRADE`;
- `INTRADAY` time horizon;
- thesis;
- strongest counterargument;
- `LOW`, `MEDIUM`, or `HIGH` uncertainty;
- Evidence Passport IDs; and
- structured invalidation containing a condition and supporting Evidence Passport IDs.

Unknown citations, malformed output, timeout, incomplete response, refusal, provider failure,
or an unexpected tool call fail to `ABSTAIN`. The adapter exposes no Alpaca, MCP, contract
selection, sizing, Referee, or broker method.

## Referee policy

The replay Referee distinguishes hard and soft gates:

- hard invalidity or stale evidence: `BLOCK`;
- AI adapter failure or `NO_TRADE`: `ABSTAIN`;
- fewer than three directionally supporting feature states: `ABSTAIN`;
- at least five supporting states, no opposing state, and uncertainty below `HIGH`: `APPROVE`;
- otherwise, at least three supporting states: `REDUCE`.

The six directional states are the three returns, VWAP relationship, opening-range state, and
day-range location. Conflict and ordinary uncertainty are soft; they do not automatically
become `BLOCK`.

The existing locked authority values were not changed: full authority is two contracts,
reduced authority is one contract, and the maximum limit premium is $4.25 per share.

## Deterministic option selector

Selection is attempted only after `APPROVE` or `REDUCE` and is limited to:

- SPY long calls for `LONG_CALL` or SPY long puts for `LONG_PUT`;
- 7–21 DTE, preferring 10–14 DTE;
- absolute delta 0.40–0.55, targeting 0.50;
- all five required Greeks: delta, gamma, theta, vega, and rho;
- a timestamped OPRA quote no older than five minutes on the replay clock;
- positive bid, ask at or above bid, and spread no wider than 10% of midpoint; and
- ask no greater than the Referee's locked $4.25 premium authority.

Preference order is the preferred DTE window, distance from 12 DTE, distance from 0.50 delta,
spread, distance from the underlying, and stable symbol order. No eligible contract returns
`NO_SUITABLE_CONTRACT`; it never relaxes a constraint.

## Replay results

| Verdict | Checked-in Terra-shaped outputs | Configured Terra replay run |
|---|---:|---:|
| APPROVE | 5 | 5 |
| REDUCE | 1 | 0 |
| ABSTAIN | 1 | 2 |
| BLOCK | 2 | 2 |

The configured Terra run completed all nine calls without adapter or schema failure. It chose
`NO_TRADE` with high uncertainty for the designed soft-conflict/pullback scenario, converting
the fixture's expected `REDUCE` into `ABSTAIN`. This is analyst caution, not a Referee hard gate.
The Referee's one-contract `REDUCE` path is independently proven by the checked-in structured
proposal.

The Referee is not excessively conservative in this small corpus: seven of nine deterministic
fixture cases avoid `BLOCK`, and six receive entry authority. Contract validity then correctly
prevents three of the five approved bullish cases from becoming operator-review candidates
because of missing Greeks, a wide spread, or no eligible contract. No threshold was tuned.

## Safety result

Every scenario creates a complete sealed Passport and stored Referee result. Every final
transition records Passport ID, verdict, authority, allow/deny state, reason code, and a null
broker result. `ORDER_ATTEMPT` and `BROKER_LIFECYCLE` counts remain zero. Even a
`READY_FOR_OPERATOR_REVIEW` result has `broker_submission_allowed=false`.

Run the deterministic corpus with:

```powershell
.venv\Scripts\cajnmnstr.exe replay-cycle
```

The optional command below contacts OpenAI with replay evidence only; it never contacts Alpaca:

```powershell
.venv\Scripts\cajnmnstr.exe replay-cycle --live-terra
```
