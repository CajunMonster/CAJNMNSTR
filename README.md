# CAJNMNSTR

CAJNMNSTR is an evidence-governed SPY options paper-trading agent for the Alpaca AI Trading Agents Hackathon.

The narrow competition path is:

```text
market evidence → deterministic features → Evidence Passport → AI proposal
→ deterministic Referee → APPROVE / REDUCE / ABSTAIN / BLOCK / EXIT
→ deterministic option selection → operator authority → Alpaca paper broker → reconciliation
```

The AI may make a case. It never receives trading authority.

## Competition checkpoint

- Local dashboard: `http://127.0.0.1:8841/`
- Dedicated Alpaca paper account: authenticated read-only at exactly $100,000, with no positions or open orders at the checkpoint
- Market/account values in the UI: authenticated read-only PAPER/SIP/OPRA state; closed-market
  data remains `PAUSED` and non-actionable
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

- **Alpaca MCP Server v2** supplies AI-facing market context with only `assets`, `stock-data`, `options-data`, and `news`.
- **alpaca-py / Trading API** supplies deterministic paper account access, SPY/options reads, order submission, lifecycle lookup, positions, and reconciliation.
- **SQLite Evidence Journal** stores Passports, health incidents, order identities, broker lifecycle events, and reconciliation under `CAJNMNSTR_DATA_ROOT`.
- **Operator Authority Path** is the sole bridge from a sealed Passport and deterministic Referee result to the execution coordinator; every allow or denial is journaled.
- **Health Supervisor** reports `HEALTHY`, `DEGRADED`, or `PAUSED` and attaches a protective action to critical failures.
- **Replay Decision Cycle** calculates the approved small feature set, validates Terra citations,
  distinguishes hard and soft Referee gates, and selects only eligible SPY options.
- **Live Evidence Snapshot** normalizes authenticated PAPER account, SIP bars/quotes, and OPRA
  option snapshots through that same decision contract, then stops before broker submission.
- **Model-neutral AI port** supports provider adapters without giving any provider broker access.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authority boundary,
[docs/REPLAY_DECISION_CYCLE.md](docs/REPLAY_DECISION_CYCLE.md) for the replay pipeline and
measured verdicts, [docs/OPRA_ENTITLEMENT.md](docs/OPRA_ENTITLEMENT.md) for the verified SIP/OPRA
entitlement, and [docs/TONIGHT_OWNER_CHECKLIST.md](docs/TONIGHT_OWNER_CHECKLIST.md) for the
credential and first-test sequence. A concise competition narrative is available in
[docs/SUBMISSION_WRITEUP.md](docs/SUBMISSION_WRITEUP.md).

## Scope and limitations

- CAJNMNSTR is a hackathon PAPER-trading prototype, not a production trading system or financial
  advice.
- Its competition scope is SPY long calls and long puts. Other asset cards and strategies are not
  part of the system.
- No profitability, fill quality, uptime, or live-capital safety claim is made.
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
```

Never commit `.env.local`, MCP client credentials, runtime logs, broker IDs, screenshots containing secrets, or the external evidence store.

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

The deprecated `CAJNMNSTR_EXECUTION_ENABLED` variable is accepted temporarily as an entry-only
migration alias. New local configuration must use `CAJNMNSTR_ENTRY_ENABLED`,
`CAJNMNSTR_POSITION_MANAGEMENT_ENABLED`, and `CAJNMNSTR_BROKER_LOCK`; conflicting legacy and
explicit entry values fail closed.

## Demo and submission material

The judge-facing descriptions, technology list, architecture summary, limitations, demo narrative,
video script, pitch outline, and unposted social drafts are collected in
[docs/DEMO_AND_SUBMISSION_PLAN.md](docs/DEMO_AND_SUBMISSION_PLAN.md). All demo material must preserve
the visible `PAPER`, `REPLAY`, `PAUSED`, freshness, and broker-submission labels shown by the source
snapshot.

## License

Released under the [MIT License](LICENSE).
