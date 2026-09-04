# CAJNMNSTR demo and submission plan

This document contains draft competition material. It is not a submission record and does not
authorize posting, deployment access changes, broker actions, or trading.

## Short project description

CAJNMNSTR is an evidence-governed SPY options PAPER agent that completed 9 autonomous Alpaca trades
and finished above starting equity, with sealed Passports, deterministic risk, and broker-flat
reconciliation.

## Long project description

CAJNMNSTR is an evidence-governed agent for single-leg SPY options in a dedicated Alpaca PAPER
account. It normalizes authenticated SIP equity data, OPRA option snapshots, account state, and
event context into an Evidence Snapshot. Deterministic code calculates market features, validates
freshness and provenance, and opens an Evidence Passport.

OpenAI GPT-5.6 Terra sees that evidence—not broker tools—and returns `LONG_CALL`, `LONG_PUT`, or
`NO_TRADE` with a thesis, counterargument, uncertainty, citations, and invalidation. A deterministic
Referee independently returns `APPROVE`, `REDUCE`, `ABSTAIN`, or `BLOCK`. Only permitted proposals
reach deterministic contract, quantity, premium, health, and session-risk gates.

Across the competition window, CAJNMNSTR evaluated 101 decision epochs, produced 13 actionable
candidates, and autonomously completed nine entries and nine exits. Every lifecycle reached
`CLOSED_BROKER_FLAT`; no manual order was placed and no submission entered `SUBMIT_UNKNOWN`.
Results were three wins and six losses: -$92 Tuesday, -$150 Wednesday, and +$332 Thursday.
Lifecycle P&L was +$90; Alpaca equity finished at $100,088.55, up $88.55 from the $100,000 start.
The $1.45 difference remains an explicit unattributed reconciliation residual. Maximum drawdown was
0.259%.

This is a small, PAPER-only sample with modest profitability—not evidence of a statistically proven
edge. The demonstrated result is autonomous, auditable operation with deterministic risk, evidence
provenance, and verified broker-flat reconciliation.

## Technology list

- Alpaca Trading API and `alpaca-py` for deterministic PAPER account, market-data, options, and
  reconciliation interfaces
- Alpaca MCP Server v2 restricted to read-oriented market toolsets
- OpenAI Responses API through a model-neutral structured-output adapter; Terra is the current
  analyst tier
- Python 3.12+ for evidence, health, journal, Referee, selector, authority, and reconciliation
- SQLite with WAL and `synchronous=FULL` for local authoritative trading state
- React 19, TypeScript, vinext, and Vite for the dashboard
- Cloudflare Worker-compatible sanitized public build through OpenAI Sites packaging
- pytest, Ruff, ESLint, and Node's test runner for verification

## Architecture summary

```text
Alpaca PAPER account + SIP + OPRA
            |
            v
deterministic normalization and features
            |
            v
open Evidence Passport -> Terra structured proposal
            |                       |
            |                 no broker tools
            v
deterministic Referee: APPROVE / REDUCE / ABSTAIN / BLOCK
            |
            v
deterministic SPY option selector
            |
            v
sealed Passport + operator authority + reconciliation
            |
            v
READY_FOR_OPERATOR_REVIEW -> STOP in the public/read-only demo
```

The reasoning ladder and market-authority ladder remain independent. More model reasoning cannot
increase trading authority. Entry authority, position-management authority, and the hard broker
lock are separate controls.

## Judge-facing feature summary

1. **Evidence Passport:** one durable, attributable record connects source data, features, AI
   reasoning, counterargument, uncertainty, Referee result, contract selection, and lifecycle.
2. **AI without broker authority:** Terra produces a constrained proposal but has no Alpaca,
   sizing, Referee, selector, MCP, or execution interface.
3. **Deterministic authority:** hard failures block, normal uncertainty can abstain or reduce, and
   no model can override the limits.
4. **One decision per evidence epoch:** materially identical evidence reuses one canonical model
   decision instead of querying repeatedly for a preferred trade.
5. **Fail-loud operations:** health is component-specific and reports `HEALTHY`, `DEGRADED`, or
   `PAUSED` with protective action.
6. **Recovery correctness:** submit uncertainty requires reconciliation; an exit is not complete
   until the broker proves the position is flat.
7. **Truthful demo separation:** the public UI consumes sanitized recorded JSON and contains no
   broker-writing path.

## Limitations and additional information

- This is a seven-day hackathon prototype, not financial advice or a production trading system.
- It uses a dedicated Alpaca PAPER account and does not trade live capital.
- The strategy is deliberately narrow: intraday SPY long calls and puts only.
- Replay outcomes and Alpaca paper fills do not establish expected real-market performance.
- The modest positive PAPER result is a small sample and does not establish a statistically proven
  edge, expected profitability, fill quality, uptime, or live-capital safety.
- Closed, stale, malformed, unreconciled, or insufficiently entitled data remains non-actionable.
- The public dashboard is a recorded visualization, not a broker-connected control plane.
- Sol escalation and broker-native option stop orders are deferred; neither is needed to explain
  or verify the core authority architecture.
- Strategy and risk parameters are frozen during competition trading unless an owner-approved,
  timestamped, versioned change is required. Bug and safety fixes remain allowed.

## Demo narrative

The demo should answer four questions in order:

1. **What does the agent see?** Show SPY SIP evidence, OPRA option state, provenance, freshness, and
   the sealed Evidence Passport.
2. **What does the AI believe?** Show Terra's direction, thesis, strongest counterargument,
   uncertainty, citations, and invalidation.
3. **Who controls the market action?** Show the deterministic Referee, selector, separate authority
   controls, broker lock, journal, and the deliberate stop at `READY_FOR_OPERATOR_REVIEW`.
4. **Did the full machine run?** Show the verified nine-trade result, 9/9 broker-flat lifecycles,
   zero manual orders, final flat account, and the modest positive finish with the small-sample
   limitation visible.

Every screen must retain its actual `PAPER`, `REPLAY`, `PAUSED`, `MARKET CLOSED`, stale/fresh, and
submission status. Do not crop away safety labels to make the demo look more active.

## Video outline (target 4 minutes 30 seconds)

### 0:00–0:25 — Problem and scope

Show the Command page. Introduce CAJNMNSTR as an evidence-governed SPY options PAPER agent. State
that AI may propose but cannot trade, size, or bypass deterministic authority.

### 0:25–1:05 — Evidence intake

Point to Alpaca PAPER authentication, SIP, OPRA, market session, timestamps, account state, and
health. Open Evidence and explain that live and replay inputs normalize to the same contract.

### 1:05–1:50 — Terra proposal

Show the constrained direction, thesis, counterargument, uncertainty, citations, and invalidation.
Explain that malformed output, refusal, timeout, or bad citations fails to abstain.

### 1:50–2:40 — Referee and selector

Show `APPROVE`, `REDUCE`, `ABSTAIN`, or `BLOCK`, then explain the fixed SPY-only contract rules:
7–21 DTE, preferred 10–14 DTE, target delta near 0.50, required Greeks, quote freshness, spread,
and premium authority. Do not imply that approval guarantees submission.

### 2:40–3:30 — Authority and recovery

Show Entry, Position Management, and Broker Lock as separate states. Explain durable client-order
identity, submit-unknown reconciliation, daily-loss behavior, forced-EOD independence from AI, and
broker-flat verification after exits.

### 3:30–4:05 — Public safety boundary

Show Journal and System. State that the public page is sanitized and read-only: no credentials,
private SQLite journal, account/order identifiers, mutation endpoints, or broker-writing runtime.

### 4:05–4:30 — Close

Return to Command and state the verified result: 101 decision epochs, 13 actionable candidates,
nine autonomous entries and exits, 9/9 lifecycles broker-flat, and final Alpaca equity of
$100,088.55. Explain that lifecycle P&L was +$90 while the broker-account gain was +$88.55, leaving
an explicit $1.45 unattributed residual. Call the outcome modestly profitable and operationally
healthy, while stating that nine PAPER trades are far too few to prove an edge.

## Pitch-deck outline

1. **Title:** CAJNMNSTR — SPY Options Agent
2. **Problem:** reasoning quality is not the same as permission to trade
3. **Evidence Passport:** provenance, features, proposal, counterargument, authority, lifecycle
4. **Two independent ladders:** computational effort versus deterministic market authority
5. **Fail-closed architecture:** Referee, health, selector, idempotency, reconciliation, verified flat
6. **Alpaca + result:** 101 epochs, 13 candidates, nine autonomous trades, +$90 lifecycle P&L,
   $100,088.55 final equity, 0.259% max drawdown, 9/9 broker-flat
7. **Limits and next steps:** modest positive PAPER result from a small sample; narrow SPY scope,
   deferred Sol and disaster stop

Use the approved skull/top-hat/spade identity, blackened metal, bronze, and silver. Prefer one
truthful 16:9 dashboard capture as the cover image. Use a 1600×900 crop of the final recorded
Command page or another sanitized competition state with its PAPER/session labels visible; do not
substitute invented market state or unrelated asset-class imagery.

## Social drafts — do not post without owner approval

### Short

Draft: Built CAJNMNSTR for the Alpaca AI Trading Agents Hackathon: a SPY options PAPER agent where
AI makes a structured case, but deterministic evidence, risk, authority, and reconciliation decide
what can proceed. It completed nine autonomous PAPER trades, reconciled all nine lifecycles to
broker-flat, and finished modestly above starting equity. Small sample—not a proven edge. Source:
<https://github.com/CajunMonster/CAJNMNSTR>. Add the demo link only after owner approval.

### Technical

Draft: CAJNMNSTR separates model reasoning from market authority. Alpaca SIP/OPRA data becomes a
sealed Evidence Passport; Terra returns a cited thesis and counterargument; a deterministic Referee
and option selector enforce freshness, Greeks, spread, premium, health, idempotency, and
reconciliation. Final PAPER result: nine trades, +$90 lifecycle P&L, $100,088.55 broker equity,
0.259% max drawdown, and 9/9 broker-flat. Small sample; no statistically proven edge. Source:
<https://github.com/CajunMonster/CAJNMNSTR>. Add the demo link only after owner approval.
