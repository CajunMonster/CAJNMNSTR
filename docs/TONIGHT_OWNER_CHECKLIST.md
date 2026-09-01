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
- [ ] Set `CAJNMNSTR_ENTRY_ENABLED=false`, `CAJNMNSTR_POSITION_MANAGEMENT_ENABLED=true`, and `CAJNMNSTR_BROKER_LOCK=false`; remove the deprecated `CAJNMNSTR_EXECUTION_ENABLED` alias after migration.
- [ ] Leave `CAJNMNSTR_EXECUTION_CONFIRMATION` blank until the controlled order window.
- [ ] Set the actual stock/options feeds and `ALPACA_DATA_ENTITLEMENT` only after verification.
- [x] Register the secret-free `alpaca_market_readonly` STDIO launcher in the owner's local Codex
  configuration. Credentials remain in the ignored `.env.local` and are supplied only to the MCP
  child process.
- [x] Validate both checked-in MCP templates and the exact four-toolset allowlist.
- [x] Prove the registered official v2.3.0 runtime with one authenticated PAPER `get_clock` call;
  verify that account, order, position, and broker-write tools are absent.
- [ ] Restart Codex after MCP registration so a fresh client session discovers the v2 tool list.

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
- [ ] Set `CAJNMNSTR_ENTRY_ENABLED=true`; keep position management enabled and the broker lock clear.
- [ ] Set `CAJNMNSTR_EXECUTION_CONFIRMATION=PAPER_ONLY_I_ACCEPT`.
- [ ] Re-run `config-check`; confirm `entry_armed: true`, `position_management_armed: true`, and `broker_lock_active: false` only after every paper invariant passes.
- [ ] Run `.venv\Scripts\cajnmnstr.exe health --live` and require overall `HEALTHY`; `DEGRADED` or `PAUSED` must block submission.
- [ ] Submit exactly one paper limit order through the operator authority path, which alone may invoke the deterministic execution coordinator. Do not invoke the coordinator directly and do not use MCP.
- [ ] Verify Alpaca returned a broker order ID and that its client order ID exactly matches the local ID.
- [ ] Verify the authority transition, order attempt, and broker lifecycle event are present in the journal/Evidence Passport.
- [ ] Cancel the order if it remains open. If it fills, close the paper position as explicitly approved.
- [ ] Reconcile broker orders and positions against local IDs. Require a matched reconciliation report.
- [ ] Immediately reset `CAJNMNSTR_ENTRY_ENABLED=false` after the entry is accepted. Do not clear the confirmation while a position or unresolved broker order exists; position-management authority must remain available.
- [ ] After the account is flat and reconciliation is matched, clear the confirmation value.
- [ ] Re-run `config-check` and confirm `entry_armed: false`; verify the displayed position-management and broker-lock states match the intended flat-account posture.
- [ ] Preserve the Passport, lifecycle events, final broker state, and reconciliation record for the demo audit trail.
