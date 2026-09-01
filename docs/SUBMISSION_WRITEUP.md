# CAJNMNSTR submission write-up

CAJNMNSTR is an evidence-governed SPY options PAPER-trading agent built for the Alpaca AI Trading
Agents Hackathon. Its central idea is simple: AI may make the case, but deterministic software
decides whether that case deserves market authority.

## AI logic

Authenticated Alpaca SIP and OPRA inputs are normalized into the same Evidence Snapshot contract
used by checked-in replay cases. Deterministic preprocessing calculates short-horizon returns,
the previous-close gap, VWAP relationship, opening-range state, day-range location, realized
volatility, and available option-state features. Missing fields are omitted rather than invented.

Terra receives that normalized evidence through a strict structured-output adapter. It can return
only `LONG_CALL`, `LONG_PUT`, or `NO_TRADE`, plus an intraday horizon, thesis, strongest
counterargument, uncertainty, cited Evidence IDs, and structured invalidation. It has no tools,
MCP access, sizing authority, broker interface, or order authority. Provider errors, timeouts,
refusals, malformed output, and invalid citations fail to `ABSTAIN`. Materially identical evidence
reuses one canonical AI decision rather than repeatedly querying for a preferred answer.

## Risk and authority gates

The deterministic Referee runs after Terra and returns `APPROVE`, `REDUCE`, `ABSTAIN`, or `BLOCK`.
Ordinary uncertainty does not become a hard block, while stale data, malformed evidence, unsafe
account state, and failed reconciliation remain fail-closed. If authority permits, a deterministic
selector considers only SPY long calls or puts with 7–21 days to expiration, valid OPRA quotes and
Greeks, acceptable spread and freshness, target delta, and premium within the Referee's grant.

Computational authority and trading authority remain independent. Deeper AI reasoning cannot
increase quantity or bypass risk limits. New-entry authority, deterministic position-management
authority, and the broker lock are separate controls. AI and MCP cannot enable them. Every
actionable transition requires a sealed Evidence Passport, durable Referee result, component-level
health, and a unique client-order identity recorded before broker submission.

## Alpaca infrastructure

Alpaca's Trading API and `alpaca-py` provide PAPER account state, clock, SIP equity data, OPRA
option snapshots, deterministic order handling, positions, and broker reconciliation. Separately,
the locally registered official Alpaca MCP Server v2.3.0 provides a read-oriented AI/developer
integration restricted to asset, stock-data, options-data, and news toolsets. An authenticated
PAPER market-clock call proved the MCP runtime path; its exposed tool inventory contained no
account, order, position, or broker-write tools. MCP is not CAJNMNSTR's execution path.
Order-submit uncertainty becomes `SUBMIT_UNKNOWN` and requires reconciliation rather than a blind
retry. A submitted exit is not considered complete until Alpaca positions prove the account is
flat.

The Evidence Passport and SQLite journal preserve data provenance, deterministic features, Terra's
proposal, Referee authority, selected contract, health, lifecycle events, and reconciliation. The
system reports `HEALTHY`, `DEGRADED`, or `PAUSED` and attaches a protective action to failures so it
does not fail silently.

## Demo safety and limitations

The public dashboard is a read-only visualization of sanitized replay or recorded PAPER evidence.
It contains no Alpaca or AI credentials, broker-writing route, operator arming control, private
journal, or sensitive account/order identifiers. Broker-connected runtime components remain local.

CAJNMNSTR is a seven-day hackathon prototype, not financial advice or a production trading system.
It makes no profitability claim, does not trade live capital, and does not claim that paper fills or
replay outcomes predict real-market performance.
