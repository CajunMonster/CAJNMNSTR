# CAJNMNSTR paper integration boundary

CAJNMNSTR has two Alpaca paths with different authority.

## AI-facing market context

Alpaca MCP Server v2 runs locally through
`uvx --with fastmcp==3.1.0 alpaca-mcp-server==2.3.1` with only:

- `assets` — asset lookup, option contracts, calendar, and clock
- `stock-data` — stock bars, quotes, trades, and snapshots
- `options-data` — option bars, quotes, trades, snapshots, and chains
- `news` — read-only news retrieval

The configuration deliberately excludes `account`, `trading`, `watchlists`, and every other
toolset. The local Codex registration invokes a secret-free launcher that reads the two required
credentials from the ignored `.env.local` and supplies them only to the child MCP process. MCP
credentials never appear in the repository or the Codex registration. Alpaca MCP Server v2.3.1
requires FastMCP 3.x; the explicit 3.1.0 pin prevents an incompatible 4.x dependency from being
selected. The authenticated `get_clock` proof and exposed-tool audit are recorded in
[ALPACA_MCP_VERIFICATION.md](ALPACA_MCP_VERIFICATION.md).

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

For September 1–4, 2026, the live collector also reads a checked-in, verified Tier-1 event
calendar whose BLS/ISM source URLs, access date, coverage, timezone, importance, and release
timestamps travel with the Evidence Snapshot. The owner-approved blackout is 15 minutes before
through 30 minutes after a release. The deterministic states are `BEFORE_BLACKOUT`,
`DURING_BLACKOUT`, `AFTER_BLACKOUT`, and `VERIFIED_NO_NEARBY_EVENT`. An active blackout,
unverified calendar, malformed policy, or expired coverage becomes a new-entry hard failure.
Event-calendar health remains explicitly noncritical for deterministic management and exit of an
already verified position.

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

The optional continuous PAPER controller polls component and broker health every 60 seconds and
defines a decision epoch by the newest completed regular-session five-minute bar. It invokes Terra
once per new actionable epoch and journals non-actionable evidence and `NO_TRADE` / `ABSTAIN` /
`BLOCK`. `PAPER_READ_ONLY_LOOP` has no broker-submission path.
`PAPER_POSITION_MANAGEMENT_LOOP` attaches the deterministic manager and requires the separate
PAPER confirmation and armed position authority. `PAPER_AUTONOMOUS_COMPETITION` additionally
requires armed entry authority, armed position management, a clear broker lock, and a configured
session-loss limit. On `READY_FOR_OPERATOR_REVIEW`, that mode reproduces the frozen position plan
from sealed evidence and may invoke the existing authority/coordinator path exactly once. New-entry
analysis stops while an entry order or position is unresolved, and management continues
independently of Terra. The manager can submit only `sell_to_close`; it never improvises an exit or
treats missing position management as safe.

The autonomous handler does not select a symbol, contract, direction, size, premium, or exit
threshold. It validates that the candidate exactly matches the sealed selector output, registers
the owner-approved immutable plan, and delegates to the existing operator authority path. Durable
client-order state is reserved before the broker write. `SUBMIT_UNKNOWN` and other uncertainty
require reconciliation and cannot be retried blindly. Terminally rejected or expired unfilled
entries transition to `ENTRY_ABORTED`, which releases the one-position slot without claiming a
completed trade or broker-flat position lifecycle.

The Competition Supervisor is an observational wrapper, not another decision agent. It consumes
the loop's existing health, reconciliation, journal, lifecycle, and dashboard telemetry; persists
hourly/event checkpoints and rolling descriptive performance; and raises explainable operational
or behavioral flags. Safe recovery is bounded to recollection/reconciliation, dashboard restart,
durable epoch recovery, and a maximum of three watchdog loop restarts. It cannot call Terra,
change thresholds, grant authority, or submit an order. Closed-session stale data is an expected
`PAUSED` state; the same stale data during the regular session is a critical entry block.

Before any entry, a durable position plan must be linked to its sealed `APPROVE`/`REDUCE`
Passport and its deterministic selected contract. The initial owner-approved PAPER policy fixes a
25% executable-bid premium stop, a 35% executable-bid target, a 75-minute fill-anchored time stop,
and a 3:35 PM ET forced exit. The structural invalidation formula is
`nearest-sealed-vwap-opening-boundary-v1`: for `LONG_CALL`, freeze the maximum decision-time VWAP,
opening-range low, or opening-range high that is strictly below the decision SPY price; for
`LONG_PUT`, freeze the minimum of those levels strictly above it. An empty candidate set blocks
plan authority. The formula version, all numeric inputs, direction, Referee verdict, symbol,
strategy version, and rationale are immutable in durable plan state. A plan cannot exceed Referee
quantity authority.

The 75-minute clock does not exist before a broker fill. On the first reconciled entry-order
snapshot with nonzero filled quantity, the lifecycle binds one immutable `fill_confirmed_at` using
Alpaca `filled_at`, or that first snapshot's broker `updated_at` for a partial fill, and persists
`time_stop_at = fill_confirmed_at + 75 minutes`. Later partial fills cannot move the anchor. Stop and
target calculations use the current confirmed broker average entry premium and executable option
bid. Structured thesis invalidation uses normalized deterministic features and never parses Terra
prose.

An active condition creates and seals a deterministic exit Passport, records `EXIT` Referee
authority, and reserves a stable client-order identity before the existing coordinator submits a
DAY limit at the executable bid. A timeout becomes `SUBMIT_UNKNOWN`; partial fills, open orders,
restarts, closed markets, and unmatched broker state remain pending and cannot create another
close. The manager never calls cancel automatically, so a cancellation or replacement ambiguity
requires reconciled recovery rather than a blind write. Only broker quantity zero advances the
position lifecycle to `CLOSED_BROKER_FLAT`.

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
