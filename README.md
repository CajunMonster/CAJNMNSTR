# CAJNMNSTR

CAJNMNSTR is an evidence-governed SPY options paper-trading agent for the Alpaca AI Trading Agents Hackathon.

The narrow competition path is:

```text
market evidence → deterministic features → Evidence Passport → AI proposal
→ deterministic Referee → APPROVE / REDUCE / ABSTAIN / BLOCK / EXIT
→ deterministic option selection → operator authority → Alpaca paper broker → reconciliation
```

The AI may make a case. It never receives trading authority.

## Final competition result

- 101 canonical decision epochs produced 13 actionable candidates.
- CAJNMNSTR autonomously submitted 9 entries and 9 exits, completing 9 SPY options PAPER trades:
  3 wins and 6 losses.
- Session lifecycle P&L was -$92 Tuesday, -$150 Wednesday, and +$332 Thursday, for a cumulative
  +$90.
- The dedicated account finished at $100,088.55, an $88.55 broker-account gain from the $100,000
  start. The $1.45 difference from lifecycle-derived P&L remains an explicit unattributed
  reconciliation residual.
- Competition maximum drawdown was 0.259%.
- All 9 broker-backed lifecycles reached `CLOSED_BROKER_FLAT`; final state was 0 positions and 0
  open orders.
- No manual orders were placed and no submission entered `SUBMIT_UNKNOWN`.
- Final operating verdict: **PROFITABLE AND HEALTHY**.

This is a small PAPER-only sample with modest profitability, not evidence of a statistically
proven edge or expected future returns. The strongest demonstrated result is autonomous operation
with evidence provenance, deterministic risk authority, durable lifecycle records, and verified
broker-flat reconciliation.

## Competition checkpoint

- Local dashboard: `http://127.0.0.1:8841/`
- Dedicated Alpaca paper account: started at $100,000 and finished broker-flat at $100,088.55
- Market/account values in the UI: authenticated PAPER/SIP/OPRA state; closed-market data remains
  `PAUSED` and non-actionable
- Market-data entitlement: Algo Trader Plus verified read-only with SIP equities and OPRA options
- Alpaca and OpenAI credentials: local only and intentionally absent from the repository
- New-entry authority: disabled by default and independent from deterministic position management
- Position management: enabled by default but cannot act without the paper confirmation, a verified existing position, exit-critical health, and durable EXIT authority
- Broker lock: explicit highest-level freeze, clear by default, and authoritative over both paths
- SPY decision cycle: nine checked-in replay cases reach a sealed Passport and operator-review
  boundary, then stop before broker submission
- Terra: the configured adapter completed the nine-case replay with strict structured output; it
  has no broker or execution interface
- Sol escalation: intentionally not implemented in this preparation slice

## Public demo boundary

The public dashboard is a read-only presentation surface. It consumes sanitized exported JSON
such as `public/dashboard-state.json` and `public/health.json`; it does not receive Alpaca or
OpenAI credentials, connect directly to the broker, or expose an execution endpoint. A public
deployment should show either an explicitly labeled replay or a sanitized recorded PAPER
Passport. Broker-connected collection, AI calls, the SQLite journal, credentials, account and
order identifiers, execution confirmation, and operator controls remain local.

Visitors cannot enable entry authority, change risk settings, invoke MCP, or submit orders from
the deployed dashboard. Public JSON must be regenerated through the local sanitization boundary
and reviewed before a new deployment.

## Architecture

- **Alpaca MCP Server v2.3.1** is locally registered as a read-oriented AI/developer
  integration with only `assets`, `stock-data`, `options-data`, and `news`. It is not the broker
  execution path.
- **alpaca-py / Trading API** supplies deterministic paper account access, SPY/options reads, order submission, lifecycle lookup, positions, and reconciliation.
- **SQLite Evidence Journal** stores Passports, health incidents, order identities, broker lifecycle events, and reconciliation under `CAJNMNSTR_DATA_ROOT`.
- **Operator Authority Path** is the sole bridge from a sealed Passport and deterministic Referee result to the execution coordinator; every allow or denial is journaled.
- **Health Supervisor** reports `HEALTHY`, `DEGRADED`, or `PAUSED` and attaches a protective action to critical failures.
- **Replay Decision Cycle** calculates the approved small feature set, validates Terra citations,
  distinguishes hard and soft Referee gates, and selects only eligible SPY options.
- **Live Evidence Snapshot** normalizes authenticated PAPER account, SIP bars/quotes, and OPRA
  option snapshots through that same decision contract, then stops before broker submission.
- **Verified Tier-1 Calendar** supplies a checked-in, source-stamped September 1–4 competition
  calendar. The owner-approved 15-minute-before/30-minute-after blackout blocks new entries only;
  coverage expiration or failed verification also fails new-entry authority closed.
- **Deterministic Position Manager** requires an immutable owner-approved plan before entry,
  evaluates stop, target, structured thesis invalidation, time stop, and forced-EOD conditions,
  and can produce only a reconciled `sell_to_close` lifecycle.
- **Model-neutral AI port** supports provider adapters without giving any provider broker access.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authority boundary,
[docs/REPLAY_DECISION_CYCLE.md](docs/REPLAY_DECISION_CYCLE.md) for the replay pipeline and
measured verdicts, [docs/OPRA_ENTITLEMENT.md](docs/OPRA_ENTITLEMENT.md) for the verified SIP/OPRA
entitlement, and [docs/TONIGHT_OWNER_CHECKLIST.md](docs/TONIGHT_OWNER_CHECKLIST.md) for the
credential and first-test sequence. A concise competition narrative is available in
[docs/SUBMISSION_WRITEUP.md](docs/SUBMISSION_WRITEUP.md). The sanitized MCP runtime proof is in
[docs/ALPACA_MCP_VERIFICATION.md](docs/ALPACA_MCP_VERIFICATION.md).
The bounded calendar and source record are documented in
[docs/EVENT_CALENDAR_2026-09-01_04.md](docs/EVENT_CALENDAR_2026-09-01_04.md).

## Scope and limitations

- CAJNMNSTR is a hackathon PAPER-trading prototype, not a production trading system or financial
  advice.
- Its competition scope is SPY long calls and long puts. Other asset cards and strategies are not
  part of the system.
- The observed PAPER result does not establish a statistically proven edge, expected profitability,
  fill quality, uptime, or live-capital safety.
- Alpaca paper fills and replay outcomes do not establish expected real-market performance.
- Closed, stale, incomplete, or unreconciled states remain non-actionable.
- Entry authority is disabled by default. AI output never overrides deterministic risk,
  authority, execution, or reconciliation code.

## Local setup

Requirements:

- Node.js 22.13 or newer for the dashboard
- Python 3.12–3.14
- uv/uvx

```powershell
pnpm install --frozen-lockfile
uv sync --dev
Copy-Item .env.example .env.local
uv run cajnmnstr config-check
uv run cajnmnstr fixture-check
uv run cajnmnstr verify-terra
uv run cajnmnstr replay-cycle
uv run cajnmnstr mcp-config-check
uv run cajnmnstr mcp-config-check --path config/alpaca-mcp.example.json
uv run python scripts/verify_alpaca_mcp_readonly.py
```

Never commit `.env.local`, MCP client credentials, runtime logs, broker IDs, screenshots containing secrets, or the external evidence store.

The local Codex registration starts `launcher/Start-Alpaca-Mcp-Readonly.ps1`. That launcher reads
only the two Alpaca credential settings from the ignored `.env.local`, pins PAPER mode and the
four read-oriented toolsets, and passes them to the official STDIO server without placing secret
values in Codex configuration. Restart Codex after adding or changing an MCP registration.

## Validation

```powershell
uv run ruff check src tests_py
uv run pytest
pnpm run build
pnpm test
```

Authenticated read-only verification commands are:

```powershell
uv run cajnmnstr verify-alpaca
uv run cajnmnstr health --live
uv run cajnmnstr live-decision --dashboard-path public/dashboard-state.json
```

The checked-in public dashboard works from sanitized JSON without credentials. Authenticated
commands are optional local operator steps and must use the dedicated Alpaca PAPER endpoint.

These commands read account, clock, SPY quote, option contracts, option chain, and provider
health; they cannot submit an order. `live-decision` also reads completed five-minute SIP bars,
normalizes the shared Evidence Snapshot, invokes Terra, runs the Referee and selector, seals the
Passport, updates the dashboard, and stops at operator review. `verify-terra` and
`replay-cycle --live-terra` send only checked-in replay evidence to OpenAI.

Live snapshots also carry deterministic Tier-1 event context. A verified session with no event
uses `VERIFIED_NO_NEARBY_EVENT`; an event session distinguishes `BEFORE_BLACKOUT`,
`DURING_BLACKOUT`, and `AFTER_BLACKOUT`. `DURING_BLACKOUT`, unverified input, or expired coverage
blocks new-entry authority without disabling deterministic management of an existing position.

The continuous loop has three explicit modes. Read-only monitoring can be started with:

```powershell
.venv\Scripts\cajnmnstr.exe live-loop --confirm PAPER_READ_ONLY_LOOP
```

It monitors once per minute, invokes Terra at most once for each new completed five-minute bar,
continues after `NO_TRADE` / `ABSTAIN` / `BLOCK`, and records actionable
`READY_FOR_OPERATOR_REVIEW` candidates without reserving the one-position slot or stopping later
epochs. It has no execution-coordinator dependency and always reports
`broker_submission_allowed=false`.

The position-management-only mode is separately armed and confirmed:

```powershell
.venv\Scripts\cajnmnstr.exe live-loop --manage-position --confirm PAPER_POSITION_MANAGEMENT_LOOP
```

This mode rejects enabled new-entry authority. It may submit only a deterministic PAPER
`sell_to_close` for a verified existing position whose immutable exit plan was registered before
entry. Use `cajnmnstr register-position-plan --help` to review the required owner-supplied fields.
The owner-approved initial competition policy is locked at a 25% executable-bid premium stop, a
35% executable-bid profit target, a 75-minute timer from the first confirmed broker fill, and a
3:35 PM ET forced exit. Thesis invalidation freezes the nearest decision-time VWAP/opening-range
boundary strictly beyond the SPY decision price on the invalidating side. Missing valid structure
blocks registration. Registration also requires a sealed `APPROVE`/`REDUCE` Passport, the selected
contract, explicit confirmation, strategy version, and rationale. Submission is not closure: the
lifecycle remains pending until reconciliation proves broker quantity is zero.

The owner-authorized autonomous PAPER mode requires entry authority, position management, the
paper-only confirmation, a clear broker lock, and a configured session-loss limit before it can
start:

```powershell
.venv\Scripts\cajnmnstr.exe live-loop --manage-position --autonomous --confirm PAPER_AUTONOMOUS_COMPETITION
```

For an actionable sealed Passport, this mode deterministically registers the frozen immutable
position plan and invokes the existing authority path once. The coordinator still rechecks the
Passport, Referee limits, selector candidate, session risk, component health, paper endpoint,
broker reconciliation, durable client-order identity, and one-position invariant. An ambiguous
submission becomes `SUBMIT_UNKNOWN` and is reconciled without blind retry. Accepted/unfilled
entries reserve the position slot; terminally rejected or expired unfilled entries are retired so
a later evidence epoch can be considered. A verified position transfers control to deterministic
position management until `CLOSED_BROKER_FLAT`.

The deprecated `CAJNMNSTR_EXECUTION_ENABLED` variable is accepted temporarily as an entry-only
migration alias. New local configuration must use `CAJNMNSTR_ENTRY_ENABLED`,
`CAJNMNSTR_POSITION_MANAGEMENT_ENABLED`, and `CAJNMNSTR_BROKER_LOCK`; conflicting legacy and
explicit entry values fail closed.

### Competition Supervisor

The deterministic Competition Supervisor observes the existing loop, broker reconciliation,
SIP/OPRA freshness, Terra availability, durable order/exit uncertainty, journal progress, and
dashboard freshness. It persists startup, hourly, candidate, entry, exit, incident, recovery, and
end-of-session checkpoints plus descriptive capital/P&L telemetry. A durable owner-configured
`CAJNMNSTR_SESSION_LOSS_LIMIT_USD` authority sums only reconciled, verified-flat realized PAPER
P&L for the current New York trading session. Missing/unknown state, reaching the limit, or the
approved 3:35 PM ET cutoff blocks new entries only; deterministic exits remain available. There is
no daily trade-count allowance. Its warnings never change a strategy or risk parameter and it has
no AI or broker-mutation interface.

For the next regular session, start the bounded Windows watchdog from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File launcher\Start-Competition-Supervisor.ps1
```

It selects read-only, position-management-only, or explicitly armed autonomous PAPER mode from
the redacted local configuration. Autonomous startup fails closed unless deterministic position
management is armed and the owner-approved $2,000 session-loss limit is present. The watchdog
starts/restarts the dashboard independently and restarts a crashed or three-cadence-stalled loop
at most three times. Every loop restart recovers durable decision epochs and reconciles before
analysis, so it cannot fish for a new Terra answer or blindly retry an uncertain broker write.
End of session is not restarted.

## Demo and submission material

The judge-facing descriptions, technology list, architecture summary, limitations, demo narrative,
video script, pitch outline, and unposted social drafts are collected in
[docs/DEMO_AND_SUBMISSION_PLAN.md](docs/DEMO_AND_SUBMISSION_PLAN.md). All demo material must preserve
the visible `PAPER`, `REPLAY`, `PAUSED`, freshness, and broker-submission labels shown by the source
snapshot.

The sanitized first-session evidence record is
[docs/LIVE_EVIDENCE_2026-08-31.md](docs/LIVE_EVIDENCE_2026-08-31.md). It records one freshness
safe-stop and one corrected actionable abstention; it does not claim that a trade occurred.

## License

Released under the [MIT License](LICENSE).
