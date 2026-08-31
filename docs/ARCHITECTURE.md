# CAJNMNSTR paper integration boundary

CAJNMNSTR has two Alpaca paths with different authority.

## AI-facing market context

Alpaca MCP Server v2 runs through `uvx alpaca-mcp-server==2.3.0` with only:

- `assets` — asset lookup, option contracts, calendar, and clock
- `stock-data` — stock bars, quotes, trades, and snapshots
- `options-data` — option bars, quotes, trades, snapshots, and chains
- `news` — read-only news retrieval

The example config deliberately excludes `account`, `trading`, `watchlists`, and every other toolset. MCP credentials belong only in the local MCP client configuration and never in this repository.

## Deterministic paper execution

`alpaca-py` owns authenticated account reads, market clock reads, SPY quotes, option-contract retrieval, option-chain retrieval, paper order submission, order lookup, positions, cancellation, and reconciliation.

The operator authority path has independent safety conditions:

1. the referenced Evidence Passport exists and is `SEALED`;
2. a stored deterministic Referee result exists and is valid;
3. `APPROVE` stays inside its quantity and premium limits;
4. `REDUCE` materially lowers the candidate quantity;
5. `ABSTAIN` and `BLOCK` grant no order authority;
6. `EXIT` accepts only `sell_to_close` position management;
7. environment is exactly `paper` and the trading URL is the paper endpoint;
8. credentials are present locally, the exact paper confirmation is present, the applicable entry or position-management permission is armed, and the broker lock is clear;
9. entry-critical health is available, while `sell_to_close` applies the narrower documented position-management profile;
10. a unique `cajnmnstr-` client identity is durably authorized before submission.

The coordinator independently requires the durable authorization row, so a direct call from AI, MCP, or another bypass fails closed. The model-neutral AI port returns analysis only, and the MCP configuration exposes no trading toolset. The deterministic Referee, operator authority path, execution coordinator, and reconciliation remain separate layers.

## Replay decision path

The pre-Monday replay slice uses checked-in SPY five-minute bars, previous close, expected
volume when provided, event/news context when present, and OPRA-shaped option snapshots. Numeric
returns, VWAP, opening range, day-range location, relative volume, realized volatility, ATM IV,
and valid same-strike skew are deterministic. Terra receives the resulting evidence, then the
Referee distinguishes hard `BLOCK`, ordinary `ABSTAIN`, reduced authority, and full authority.
Only `APPROVE` or `REDUCE` reaches deterministic contract selection.

The complete Passport is sealed before the Referee result becomes durable. The replay operator
gate can report `READY_FOR_OPERATOR_REVIEW`, but always records
`broker_submission_allowed=false`; the replay pipeline has no coordinator dependency. See
[REPLAY_DECISION_CYCLE.md](REPLAY_DECISION_CYCLE.md) for exact gates and measured results.

## Authenticated live Evidence Snapshot path

The live collector reads the dedicated PAPER account, positions, open orders, market clock, SPY
SIP quote, completed regular-session five-minute SIP bars, daily bars for previous close, and the
7–21 DTE SPY OPRA chain. It passes a replay-shaped document through the same
`EvidenceCalculator`, so Terra, the Referee, option selector, Passport, and operator-review gate
receive one normalized contract. Only the source mode and provenance differ.

The collector first reconciles durable order identities with Alpaca and requires the account to
be active, flat, and free of open orders. Any mismatch is a hard snapshot failure. Closed market,
SIP quote age over 30 seconds, OPRA quote age over 30 seconds, insufficient completed bars, or
invalid evidence makes the snapshot non-actionable. The command has no execution-coordinator
dependency and always records `broker_submission_allowed=false`.

Open-market readiness and the decision snapshot both use the same 30-second SIP/OPRA freshness
policy. The snapshot timestamp is captured after the authenticated read set completes; the earlier
market-clock response is retained as provenance but is not used as the post-read freshness clock.

From the project root, Monday's single read-only operator path is:

```powershell
.venv\Scripts\cajnmnstr.exe live-decision --dashboard-path public\dashboard-state.json
```

Run it only after enough regular-session five-minute bars exist. A safe result requires PAPER,
fresh SIP/OPRA, successful reconciliation, a sealed Passport, and
`READY_FOR_OPERATOR_REVIEW`; even then entry authority remains disabled and the command stops
before broker submission.

The optional continuous PAPER read-only controller polls component and broker health every 60
seconds and defines a decision epoch by the newest completed regular-session five-minute bar. It
invokes Terra once per new actionable epoch, journals non-actionable evidence and `NO_TRADE` /
`ABSTAIN` / `BLOCK`, and pauses when a candidate reaches `READY_FOR_OPERATOR_REVIEW`. The loop
requires the literal `PAPER_READ_ONLY_LOOP` operator confirmation, rejects enabled entry
authority, and has no broker-submission path. If a verified position exists, new-entry evaluation
stops and the loop requires an explicitly attached deterministic position-management handler; it
never improvises an exit or treats missing position management as safe.

## Terra proposal boundary

The initial AI baseline uses the OpenAI Responses API with `gpt-5.6-terra`, no tools, no response storage, and a strict JSON schema. Terra returns only `LONG_CALL`, `LONG_PUT`, or `NO_TRADE`, plus an `INTRADAY` time horizon, thesis, counterargument, uncertainty, cited Evidence IDs, and structured invalidation. `NO_TRADE` maps to `ABSTAIN`. Timeouts, refusals, incomplete responses, unexpected tool calls, unknown citations, malformed JSON, and schema failures also map to `ABSTAIN`. The adapter has no broker, MCP, sizing, Referee, or execution method.

Weekend AI verification accepts only checked-in fixture/replay evidence explicitly marked non-actionable. Unverified or Basic indicative options data remains informational and cannot relax the deterministic authority path. SIP and OPRA configuration is accepted only with a locally recorded, authenticated Algo Trader Plus entitlement; feed authorization never overrides freshness or execution gates.

## Fail-loud health

The health supervisor emits `HEALTHY`, `DEGRADED`, or `PAUSED`. A critical configuration, stale-data, Alpaca, or evidence-store failure carries a protective action. Journal failures fall back to an ignored local emergency incident log so loss of the evidence store is still visible and persistent.

Health authority is intentionally different for new entries and existing-position exits:

- `ENTRY_CRITICAL`: configuration, evidence store, Alpaca connectivity, known broker state, broker reconciliation, executable market session, fresh SPY and option quotes, risk limits, AI provider, news health, and event-calendar health.
- `EXIT_CRITICAL`: configuration, evidence store/durable authority, Alpaca connectivity, known broker state, broker reconciliation, executable market session, and an executable option quote for the current limit-only exit path.
- `NONCRITICAL_FOR_EXIT`: AI provider, SPY analytical quote, daily-loss entry lock, news, and event-calendar context.

Only explicitly named noncritical components may be ignored for `sell_to_close`. A missing required component, any aggregate-only health result, or an unknown non-healthy condition still fails closed. A stale option quote or closed/halted market retains `EXIT` authority as pending without recording a fill. Uncertain Alpaca or broker state requires reconciliation before any retry, preventing a blind duplicate close. Daily-loss locks block new exposure but cannot trap an existing position. Forced-EOD liquidation does not depend on Terra or Sol.

Broker authority has three explicit controls:

- `CAJNMNSTR_ENTRY_ENABLED` controls only `buy_to_open` entry authority and defaults to `false`.
- `CAJNMNSTR_POSITION_MANAGEMENT_ENABLED` controls only verified `sell_to_close` authority and defaults to `true`.
- `CAJNMNSTR_BROKER_LOCK` is the highest-level owner freeze and blocks every submission when active.

Both paths still require the exact paper confirmation, credentials, paper endpoint, sealed Passport, Referee authority, durable client-order identity, and their component-specific health profile. Position management additionally reads the broker position immediately before submission and requires one matching long contract position with at least the requested close quantity. It cannot open, average down, reverse, or over-close exposure. A disabled position-management path or failed position verification creates a persistent `CRITICAL` incident, with the emergency incident file as the journal-failure fallback.

The deprecated `CAJNMNSTR_EXECUTION_ENABLED` input is accepted only as an entry-only migration alias. If it conflicts with `CAJNMNSTR_ENTRY_ENABLED`, configuration fails closed. It is not emitted as broker authority and has no position-management meaning.

Broker-native option stop and stop-limit protection is deferred. It may be reconsidered only as a disaster backstop after the first live competition session and after its separate lifecycle, cancellation, replacement, and reconciliation design is proven.

## Durable evidence

The SQLite journal under `CAJNMNSTR_DATA_ROOT` records connection checks, data-health failures, proposals, Referee verdicts, every authority transition, order attempts, broker lifecycle events, reconciliation, and incidents. Each authority-transition record includes Passport ID, verdict, authority, allow/deny result, reason code, and broker result when applicable. Evidence Passports are opened and sealed explicitly. Broker client-order IDs are unique and stored before any submission. The authoritative database uses WAL mode with `synchronous=FULL`; this small local workload prefers durable authorization and lifecycle commits over marginal write throughput.

An order-submit timeout is `SUBMIT_UNKNOWN`, never permission to retry. The durable client identity remains reserved and reconciliation by client order ID is required. Every observed fill retains the decision quote, midpoint, submitted limit, actual average fill, adverse fill versus midpoint, spread-paid percentage, quote age, and the pessimistic entry-at-ask or exit-at-bid reference. These are shadow execution-quality measurements and never replace Alpaca competition P&L.

A submitted close remains `EXIT_PENDING_RECONCILIATION`. Only a broker positions read proving the option quantity is zero can set `CLOSED_BROKER_FLAT`; otherwise a persistent critical incident remains open and another entry is blocked.

Terra decisions are canonical per normalized evidence epoch, prompt hash, and model. The journal stores one decision ID, response or abstention, validation state, latency, and attempt record. Replaying materially identical evidence reuses that decision instead of asking again for a preferred answer. The OpenAI client has hidden SDK retries disabled; any future explicit transport retry must retain the same decision ID and be labeled as a retry.

Startup recovery reconciles complete order history, durable client identities, and current positions. Alpaca documents that paper-account option non-trade activities may not appear until the following day even though balances and positions update immediately. Because CAJNMNSTR is flat daily and selects 7–21 DTE contracts, assignment/exercise/expiry activity polling is deferred as audit enrichment rather than treated as a real-time recovery authority. See Alpaca's [Options Trading](https://docs.alpaca.markets/docs/options-trading) and [Account Activities](https://docs.alpaca.markets/docs/account-activities) documentation.

Competition strategy changes follow [STRATEGY_CHANGE_CONTROL.md](STRATEGY_CHANGE_CONTROL.md). Live paper outcomes do not authorize automatic threshold retuning.
