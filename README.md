# CAJNMNSTR

CAJNMNSTR is an evidence-governed SPY options paper-trading agent for the Alpaca AI Trading Agents Hackathon.

The narrow competition path is:

```text
market evidence → AI proposal → Evidence Passport → deterministic Referee
→ APPROVE / REDUCE / ABSTAIN / BLOCK / EXIT → Alpaca paper broker → reconciliation
```

The AI may make a case. It never receives trading authority.

## Current weekend checkpoint

- Local dashboard: `http://127.0.0.1:8841/`
- Dedicated Alpaca paper account: authenticated read-only at exactly $100,000, with no positions or open orders at the checkpoint
- Market/account values in the UI: representative; closed-market data remains `PAUSED` and non-actionable
- Alpaca and OpenAI credentials: local only and intentionally absent from the repository
- Execution: disabled by default and guarded by two explicit paper-only settings
- Terra: fixture/replay-only structured proposal baseline; it has no broker or execution interface
- Strategy and Sol escalation: intentionally not implemented or tuned in this preparation slice

## Architecture

- **Alpaca MCP Server v2** supplies AI-facing market context with only `assets`, `stock-data`, `options-data`, and `news`.
- **alpaca-py / Trading API** supplies deterministic paper account access, SPY/options reads, order submission, lifecycle lookup, positions, and reconciliation.
- **SQLite Evidence Journal** stores Passports, health incidents, order identities, broker lifecycle events, and reconciliation under `CAJNMNSTR_DATA_ROOT`.
- **Operator Authority Path** is the sole bridge from a sealed Passport and deterministic Referee result to the execution coordinator; every allow or denial is journaled.
- **Health Supervisor** reports `HEALTHY`, `DEGRADED`, or `PAUSED` and attaches a protective action to critical failures.
- **Model-neutral AI port** supports provider adapters without giving any provider broker access.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the authority boundary, [docs/OPRA_ENTITLEMENT.md](docs/OPRA_ENTITLEMENT.md) for the verified Basic/OPRA limitation, and [docs/TONIGHT_OWNER_CHECKLIST.md](docs/TONIGHT_OWNER_CHECKLIST.md) for the credential and first-test sequence.

## Local setup

Requirements:

- Node.js 22.13 or newer for the dashboard
- Python 3.12–3.14
- uv/uvx

```powershell
uv sync --dev
Copy-Item .env.example .env.local
uv run cajnmnstr config-check
uv run cajnmnstr fixture-check
uv run cajnmnstr verify-terra
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
```

These commands read account, clock, SPY quote, option contracts, option chain, and provider health; they cannot submit an order. `verify-terra` sends only the checked-in replay fixture and accepts analysis output only.
