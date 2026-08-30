# Tonight owner checklist

Keep credentials on this computer. Do not paste credentials into chat, screenshots, commits, issues, or the dashboard.

## A. Alpaca

- [ ] In Alpaca, create a **brand-new dedicated judging paper account**. Do not reuse or reset an existing account.
- [ ] Confirm its displayed paper equity is exactly **$100,000.00** with no positions and no open orders.
- [ ] Confirm options approval/trading level is enabled for the required paper test.
- [ ] Record the account ID locally for your own reference.
- [ ] Generate paper API credentials.
- [ ] Copy `.env.example` to `.env.local`.
- [ ] Open `.env.local` in a local text editor. Fill only `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
- [ ] Leave `CAJNMNSTR_EXECUTION_ENABLED=false` and leave the confirmation blank.
- [ ] Set the actual stock/options feeds and `ALPACA_DATA_ENTITLEMENT` only after verification.
- [ ] Set `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in the owner's local environment. Do not add their values to any checked-in file.
- [ ] Copy the single server table from `config/codex-mcp.example.toml` into the owner's local Codex `~/.codex/config.toml` (or add it through **Settings → MCP servers**). Keep its exact four-toolset allowlist.
- [ ] Run `.venv\Scripts\cajnmnstr.exe mcp-config-check`; confirm the secret-free Codex example reports `ok`.
- [ ] Change only that local server's `enabled` value to `true`, restart Codex, and verify the server exposes only permitted read-oriented market tools. If any account/trading/watchlist/locate tool appears, disable it immediately.

## B. Read-only verification

- [ ] From the project folder, run `.venv\Scripts\cajnmnstr.exe config-check`. Confirm it prints `paper_mode: true`, the paper URL, credentials present, and execution not armed. It never prints key values.
- [ ] Run `.venv\Scripts\cajnmnstr.exe verify-alpaca`.
- [ ] Confirm the command reports `PASS`.
- [ ] Confirm the returned account equity is exactly `$100,000.00` and trading is not blocked.
- [ ] Confirm options approval/trading levels are populated.
- [ ] Confirm the market clock is readable.
- [ ] Confirm a current SPY quote is readable and its timestamp is acceptable.
- [ ] Confirm SPY option-contract count is greater than zero.
- [ ] Confirm SPY option-chain count is greater than zero.
- [ ] Identify the actual stock feed and options feed/entitlement in Alpaca, then update the local values (`iex`/`sip`/`delayed_sip` and `indicative`/`opra`). Re-run verification.
- [ ] Open the System screen and keep it `PAUSED` until this read-only verification is complete.

## C. First controlled paper-order test

Do this only after the owner explicitly authorizes the test while present.

- [ ] Confirm the market session, chosen SPY option contract, current quote, and expiration manually.
- [ ] Confirm there are still no unexpected broker orders or positions.
- [ ] Create and seal a test Evidence Passport, then record an APPROVE verdict and limits through the deterministic Referee.
- [ ] Choose one SPY option contract and one limit price that cannot exceed the approved premium.
- [ ] Generate a unique `cajnmnstr-` client order ID and record it in the Passport.
- [ ] Set `CAJNMNSTR_EXECUTION_ENABLED=true`.
- [ ] Set `CAJNMNSTR_EXECUTION_CONFIRMATION=PAPER_ONLY_I_ACCEPT`.
- [ ] Re-run `config-check`; confirm `execution_armed: true` only after every paper invariant passes.
- [ ] Run `.venv\Scripts\cajnmnstr.exe health --live` and require overall `HEALTHY`; `DEGRADED` or `PAUSED` must block submission.
- [ ] Submit exactly one paper limit order through the operator authority path, which alone may invoke the deterministic execution coordinator. Do not invoke the coordinator directly and do not use MCP.
- [ ] Verify Alpaca returned a broker order ID and that its client order ID exactly matches the local ID.
- [ ] Verify the authority transition, order attempt, and broker lifecycle event are present in the journal/Evidence Passport.
- [ ] Cancel the order if it remains open. If it fills, close the paper position as explicitly approved.
- [ ] Reconcile broker orders and positions against local IDs. Require a matched reconciliation report.
- [ ] Immediately reset `CAJNMNSTR_EXECUTION_ENABLED=false` and clear the confirmation value.
- [ ] Re-run `config-check` and confirm `execution_armed: false`.
- [ ] Preserve the Passport, lifecycle events, final broker state, and reconciliation record for the demo audit trail.
