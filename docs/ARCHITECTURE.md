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
8. credentials are present locally and both explicit execution controls are armed;
9. operational health is exactly `HEALTHY`;
10. a unique `cajnmnstr-` client identity is durably authorized before submission.

The coordinator independently requires the durable authorization row, so a direct call from AI, MCP, or another bypass fails closed. The model-neutral AI port returns analysis only, and the MCP configuration exposes no trading toolset. The deterministic Referee, operator authority path, execution coordinator, and reconciliation remain separate layers.

## Terra proposal boundary

The initial AI baseline uses the OpenAI Responses API with `gpt-5.6-terra`, no tools, no response storage, and a strict JSON schema. Terra returns only `LONG_CALL`, `LONG_PUT`, or `NO_TRADE`, plus thesis, counterargument, uncertainty, cited Evidence IDs, and invalidation. `NO_TRADE` maps to `ABSTAIN`. Timeouts, refusals, incomplete responses, unexpected tool calls, malformed JSON, and schema failures also map to `ABSTAIN`. The adapter has no broker, MCP, sizing, Referee, or execution method.

Weekend verification accepts only checked-in fixture/replay evidence explicitly marked non-actionable. Basic indicative options data remains informational and cannot relax the deterministic authority path.

## Fail-loud health

The health supervisor emits `HEALTHY`, `DEGRADED`, or `PAUSED`. A critical configuration, stale-data, Alpaca, or evidence-store failure carries a protective action. A non-healthy critical component blocks execution. Journal failures fall back to an ignored local emergency incident log so loss of the evidence store is still visible and persistent.

## Durable evidence

The SQLite journal under `CAJNMNSTR_DATA_ROOT` records connection checks, data-health failures, proposals, Referee verdicts, every authority transition, order attempts, broker lifecycle events, reconciliation, and incidents. Each authority-transition record includes Passport ID, verdict, authority, allow/deny result, reason code, and broker result when applicable. Evidence Passports are opened and sealed explicitly. Broker client-order IDs are unique and stored before any submission.
