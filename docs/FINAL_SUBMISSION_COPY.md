# CAJNMNSTR final submission copy — owner review

This is review copy, not a submission record. It does not authorize trading, public demo access,
lablab submission, or social posting. Bracketed items must be replaced with verified competition
evidence or an owner-approved link before use.

## 1. Project title

**CAJNMNSTR**

## 2. Short description

CAJNMNSTR is a SPY options PAPER-trading agent that turns Alpaca SIP/OPRA data into a cited Terra proposal, then uses a deterministic Referee, contract selector, risk gates, and reconciliation to decide what may proceed.

_Character count: 220 of 255 maximum._

## 3. Long description

CAJNMNSTR is an evidence-governed agent for single-leg SPY options in a dedicated Alpaca PAPER
environment. It reads authenticated account state, SIP equity data, and OPRA option snapshots, then
normalizes them into one Evidence Snapshot. Deterministic code calculates the numeric features,
checks provenance and freshness, and opens an Evidence Passport.

OpenAI GPT-5.6 Terra receives the normalized evidence—not broker tools—and returns `LONG_CALL`,
`LONG_PUT`, or `NO_TRADE` with a thesis, strongest counterargument, uncertainty, citations, and an
invalidation condition. A deterministic Referee independently returns `APPROVE`, `REDUCE`,
`ABSTAIN`, or `BLOCK`. Only permitted proposals reach deterministic contract and risk selection,
which enforces SPY-only direction, expiration, delta, Greeks, quote freshness, spread, premium, and
quantity limits.

The complete Passport is sealed before durable order authority can exist. When the owner has
explicitly enabled the PAPER path and every health gate passes, the execution coordinator uses a
pre-recorded client-order identity and Alpaca's Trading API. Broker orders and positions are then
reconciled with the local journal. Unknown submission outcomes are never blindly retried, and an
exit is not complete until Alpaca proves the position is flat. Stale data, malformed evidence, AI
failure, broker uncertainty, or failed reconciliation causes the system to abstain, pause, or block
rather than quietly continue.

_Word count: 198._

## 4. The problem

AI can produce a plausible trading opinion, but an opinion is not sufficient authority to place an
order. Autonomous trading also requires trustworthy evidence, explicit risk limits, executable
quotes, durable order identity, broker-state recovery, and a safe response when any component
fails. CAJNMNSTR separates model reasoning from market authority so those controls remain
inspectable and deterministic.

## 5. How CAJNMNSTR works

```text
Alpaca market and PAPER account evidence
→ normalized Evidence Snapshot
→ Terra structured analysis
→ deterministic Referee
→ deterministic SPY option and risk selection
→ sealed Evidence Passport
→ durable operator/order authority
→ Alpaca PAPER execution, when explicitly armed
→ broker reconciliation and verified outcome
```

The AI supplies analysis only. It cannot call the broker, select quantity, relax a risk rule, enable
an authority switch, or bypass reconciliation. The execution coordinator accepts only durable
authority derived from a sealed Passport and a permitted Referee verdict.

## 6. AI logic

Terra is the normal analyst tier. It receives deterministic, cited evidence and must return:

- `LONG_CALL`, `LONG_PUT`, or `NO_TRADE`;
- an intraday time horizon;
- a thesis and strongest counterargument;
- `LOW`, `MEDIUM`, or `HIGH` uncertainty;
- Evidence Passport citations; and
- a structured invalidation condition with supporting evidence IDs.

Malformed output, unknown citations, refusal, timeout, provider failure, or an unexpected tool call
fails to `ABSTAIN`. Each normalized evidence epoch has one canonical Terra decision. Materially
identical evidence reuses that decision instead of querying repeatedly until a preferred trade
appears. CAJNMNSTR makes no unproven prediction-accuracy claim.

## 7. Risk gates

- Alpaca PAPER environment only; no live capital.
- SPY only; long calls and long puts only.
- At most one verified position; no averaging down, reversal, naked writing, or exposure increase
  through position management.
- Full replay authority is bounded at two contracts; `REDUCE` is bounded at one contract. Actual
  submission still requires owner arming and every runtime gate.
- Contracts must be 7–21 DTE, preferably 10–14 DTE, with absolute delta 0.40–0.55 and a target near
  0.50.
- Delta, gamma, theta, vega, rho, quote validity, freshness, positive bid/ask, spread, premium, and
  feed provenance must pass deterministic validation. Missing or invalid required data fails closed.
- The Referee returns `APPROVE`, `REDUCE`, `ABSTAIN`, or `BLOCK`; AI cannot override the result.
- New-entry authority and existing-position management are independent. Entries can remain disabled
  while verified deterministic `sell_to_close` actions remain available.
- The broker lock is the highest-level owner freeze and blocks every submission when active.
- Unknown broker state or failed reconciliation blocks new exposure.
- The competition policy requires same-day flattening. A forced-EOD exit does not depend on Terra,
  and a submitted close remains pending until Alpaca verifies zero position quantity.
- Closed, stale, malformed, insufficiently entitled, or unreconciled states remain non-actionable.

## 8. Alpaca infrastructure

- **Alpaca Trading API / `alpaca-py`:** authenticated PAPER account, market clock, SIP and OPRA
  reads, deterministic order handling, positions, order lookup, and reconciliation.
- **Alpaca MCP Server v2.3.0:** locally registered and authenticated in PAPER mode, with a
  successful market-clock proof. It is restricted to read-oriented asset, stock-data,
  options-data, and news toolsets; account, order, position, and broker-write tools are absent.
- **Dedicated judging account:** a verified $100,000 Alpaca PAPER account. No account ID or
  credential belongs in submission material.
- **Market data:** SIP equities and OPRA options through the verified Algo Trader Plus entitlement.
- **Order identity:** a unique durable `cajnmnstr-` client-order ID is authorized and stored before
  any broker submission.
- **Reconciliation:** local order identities, Alpaca order history, open orders, and positions must
  agree before new exposure is permitted.

## 9. Evidence Passport

Each Passport preserves the evidence and authority chain for one decision:

- normalized evidence snapshot and deterministic features;
- source, SIP/OPRA feed, observation timestamps, and freshness;
- Terra proposal, counterargument, uncertainty, citations, and invalidation;
- deterministic Referee verdict and reason codes;
- contract-selection checks and rejection reasons;
- quantity, premium, and risk authority;
- client-order identity and execution lifecycle, when applicable; and
- broker reconciliation, execution-quality evidence, and final outcome.

The Passport must be complete and sealed before execution authority can be created.

## 10. Reliability and failure handling

CAJNMNSTR reports `HEALTHY`, `DEGRADED`, or `PAUSED` with component-level details and protective
actions. Stale SIP/OPRA data blocks actionable authority. AI failure blocks new analysis-dependent
entries but cannot trap an existing position that has a valid deterministic exit condition. Broker
or account uncertainty requires reconciliation before another submission.

An order timeout becomes `SUBMIT_UNKNOWN`; the client-order identity remains reserved and no blind
retry occurs. A close order becomes `EXIT_PENDING_RECONCILIATION` until a broker positions read
proves quantity is zero. Critical incidents persist in the journal, with an emergency incident
fallback if journal storage itself fails. Authoritative SQLite state uses WAL mode and
`synchronous=FULL` so committed authorization and lifecycle records survive local failures as
strongly as practical for this workload.

## 11. Technology list

- Alpaca Trading API and `alpaca-py`
- Alpaca MCP Server v2.3.0, locally verified and restricted to read-oriented market toolsets
- SIP equities market data
- OPRA options market data
- Algo Trader Plus
- OpenAI Responses API with GPT-5.6 Terra
- Python
- TypeScript and React
- SQLite with WAL and `synchronous=FULL`
- Git and GitHub

## 12. Limitations

- PAPER trading only; CAJNMNSTR does not trade live capital.
- The competition observation window is short and cannot establish long-term performance.
- No claim of profitability, predictive accuracy, uptime, or live-capital safety is made.
- Alpaca paper fills can differ materially from executable live-market fills.
- The strategy is deliberately narrow: intraday, single-leg SPY long calls and puts.
- Replay results and PAPER outcomes do not establish expected future results.
- This is a hackathon prototype, not production investment software or financial advice.

## 13. GitHub

Public repository: <https://github.com/CajunMonster/CAJNMNSTR>

## 14. Demo

Hosted demo: <https://cajnmnstr.warrensemble.chatgpt.site>

**Access state: PRIVATE / OWNER-ONLY.** Do not place this URL in the final public submission until
the owner separately approves public sharing and verifies the published data remains sanitized and
read-only.

## 15. Video story

**Target length: 4 minutes 30 seconds.** Use real PAPER evidence only; do not recreate or imply a
result that did not occur.

### 0:00–0:25 — Introduction and problem

- Introduce CAJNMNSTR as a SPY options PAPER agent.
- State the central problem: model reasoning is not permission to trade.

### 0:25–0:55 — Architecture

- Show the evidence-to-reconciliation flow.
- Explain that AI has no broker interface and deterministic code owns market authority.

### 0:55–1:30 — Command dashboard

- Show PAPER account state, SIP, OPRA, Terra, Referee, health, freshness, and authority controls.
- Keep actual `LIVE`, `PAPER`, `REPLAY`, `PAUSED`, stale, and broker-status labels visible.

### 1:30–2:10 — Reviewed PAPER decision

- **[WAITING FOR REAL COMPETITION RESULT]** Show one genuine reviewed PAPER decision from the
  competition window.
- State the verified timestamp, evidence epoch, Terra direction, and whether the system proceeded or
  refused. Do not imply a trade occurred if the result was `ABSTAIN` or `BLOCK`.

### 2:10–2:55 — Evidence Passport and Terra

- Open the Passport and show provenance, freshness, deterministic features, thesis,
  counterargument, uncertainty, citations, and invalidation.
- Explain one canonical Terra decision per evidence epoch.

### 2:55–3:35 — Referee and deterministic risk

- Show the actual `APPROVE`, `REDUCE`, `ABSTAIN`, or `BLOCK` result.
- Explain SPY-only contract selection, DTE, delta, Greeks, quote, spread, premium, and quantity gates.

### 3:35–4:10 — Alpaca execution and reconciliation

- **[WAITING FOR REAL COMPETITION RESULT]** If an authorized PAPER order occurred, show its
  sanitized lifecycle, durable client identity, fill/cancel outcome, reconciliation, and verified
  flat state.
- If no order occurred, show the truthful refusal or safe-stop record instead.

### 4:10–4:30 — Conclusion

- Return to Command.
- Close with the demonstrated result: an attributable AI analysis governed by deterministic
  evidence, risk, execution authority, and broker reconciliation.
- Make no profitability claim.

## 16. Seven-slide deck copy

### Slide 1 — CAJNMNSTR

- Evidence-governed SPY options agent
- Alpaca PAPER · SIP equities · OPRA options
- AI analyzes; deterministic software controls market authority

### Slide 2 — The problem

- A plausible model answer is not sufficient trading authority
- Autonomous execution also needs evidence quality, risk limits, health, idempotency, and recovery
- Failures must be visible and protective, not silent

### Slide 3 — Architecture

- Alpaca evidence → normalized Evidence Snapshot → Terra
- Deterministic Referee → deterministic option/risk selection
- Sealed Passport → durable authority → PAPER execution → reconciliation
- AI and MCP have no broker-writing route

### Slide 4 — Evidence Passport

- Provenance, feed, timestamp, and freshness
- Deterministic features and cited Terra proposal
- Counterargument, uncertainty, and invalidation
- Referee, selector, risk authority, lifecycle, and outcome in one audit trail

### Slide 5 — Risk / Referee

- `APPROVE` · `REDUCE` · `ABSTAIN` · `BLOCK`
- SPY-only long options, one position, bounded quantity and premium
- Required DTE, delta, Greeks, quote freshness, and spread
- Separate entry, position-management, and broker-lock authority
- Reconciliation and verified-flat invariants

### Slide 6 — Alpaca + demonstrated result

- Dedicated $100,000 PAPER account
- Trading API plus read-oriented Alpaca MCP
- Verified SIP and OPRA through Algo Trader Plus
- **[WAITING FOR REAL COMPETITION RESULT]** Decision(s): [verified count and verdicts]
- **[WAITING FOR REAL COMPETITION RESULT]** Order/reconciliation result: [verified facts only]
- **[OPTIONAL]** Official Alpaca PAPER P&L: [value and timestamp, if useful and contextualized]

### Slide 7 — What we learned / closing

- Reasoning quality and trading authority are different systems
- A refusal or reduction can be a correct agent outcome
- Recovery and reconciliation belong in the decision architecture
- **[WAITING FOR REAL COMPETITION RESULT]** Add one evidence-backed competition lesson
- CAJNMNSTR preserves the full path from market evidence to verified broker state

## 17. Social post drafts — do not post

No event hashtags or account tags are included because none are confirmed in the approved project
documentation. Add only the officially confirmed tags immediately before an owner-approved post.

### A. Introduction / build story

We built CAJNMNSTR for the Alpaca AI Trading Agents Hackathon: a narrow SPY options PAPER agent
designed around one boundary—AI can make a cited market case, but it cannot control the broker.
Deterministic evidence, risk, authority, and reconciliation decide what may proceed.

Source: <https://github.com/CajunMonster/CAJNMNSTR>

### B. First live decision or trade milestone

CAJNMNSTR completed its first competition-window PAPER decision. **[ADD VERIFIED FACTS: timestamp,
LONG_CALL / LONG_PUT / NO_TRADE, Referee verdict, selected contract if any, and reconciled broker
outcome.]** The important part is the complete evidence trail from SIP/OPRA inputs through Terra,
the Referee, contract selection, and Alpaca reconciliation.

### C. Safety / refusal / failure-handling milestone

One of CAJNMNSTR's most useful outputs is a documented refusal. **[ADD VERIFIED EVENT: stale feed,
AI failure, broker uncertainty, invalid contract, ABSTAIN, or BLOCK.]** The system preserved the
evidence and reason code, denied new exposure, and kept the operator-facing health state truthful.
For an autonomous agent, knowing when it cannot act is part of the result.

### D. Final project / demo

CAJNMNSTR is our evidence-governed SPY options PAPER agent for the Alpaca AI Trading Agents
Hackathon. Alpaca SIP/OPRA evidence becomes a sealed Passport; Terra provides a cited thesis and
counterargument; deterministic code controls the Referee, option selection, risk, execution
authority, and reconciliation. PAPER prototype only, with no profitability claim.

Source: <https://github.com/CajunMonster/CAJNMNSTR>

Demo: **[OWNER ACTION — add only after public access is approved and rechecked]**

## 18. Final form checklist

### READY

- [x] Project title: CAJNMNSTR
- [x] Short description within 255 characters
- [x] Long description within the requested 150–250 words
- [x] Problem statement
- [x] Architecture / how-it-works copy
- [x] AI logic
- [x] Risk and authority summary
- [x] Alpaca infrastructure summary
- [x] Evidence Passport explanation
- [x] Reliability and failure-handling summary
- [x] Technology list
- [x] Limitations and safety disclaimer
- [x] Public GitHub repository URL
- [x] Video sequence and narration plan
- [x] Seven-slide deck copy
- [x] Four unposted social drafts
- [x] Project identity and existing brand assets in the public repository

### WAITING FOR REAL COMPETITION RESULT

- [ ] Final count of genuine competition-window decisions by Referee verdict
- [ ] One sanitized, reviewable PAPER decision and Evidence Passport for the demo
- [ ] Actual selected contract, quantity authority, and decision timestamp, if a contract qualifies
- [ ] Actual Alpaca order lifecycle and reconciliation evidence, if an order is authorized
- [ ] Verified broker-flat outcome after any filled position
- [ ] Official Alpaca PAPER P&L and account timestamp, only if useful and accurately contextualized
- [ ] One evidence-backed competition learning for Slide 7
- [ ] Final dashboard screenshots or video capture using real, correctly labeled state

### OWNER ACTION

- [ ] Review and approve every section of this document
- [ ] Replace or remove every bracketed placeholder
- [ ] Confirm the final lablab form fields and any word/character limits against the live form
- [ ] Decide whether to make the hosted demo public; if approved, re-run the public leakage and
  read-only-boundary checks before sharing its URL
- [ ] Record, edit, caption, and upload the final 4–4.5 minute video
- [ ] Add the final video URL and public demo URL to the form
- [ ] Select and upload the approved logo, cover image, dashboard screenshots, and seven-slide deck
- [ ] Confirm team/member, category, challenge, and contact fields required by the live form
- [ ] Confirm official event hashtags/account tags before any social post
- [ ] Approve each social draft separately; posting is not required by this document
- [ ] Paste/upload the final submission fields and perform the final preview
- [ ] Submit to lablab only after a last accuracy, access, link, and leakage review

### OPTIONAL

- [ ] Include sanitized replay-distribution or test-count evidence as supporting validation
- [ ] Include execution-quality shadow metrics if a PAPER fill occurs and they are easy to explain
- [ ] Add a short technical appendix for judges who want implementation detail
- [ ] Publish owner-approved social updates after the relevant milestone occurs
- [ ] Add captions or a transcript to improve video accessibility

## Owner review questions

1. Is the short description concrete enough, or should it lead with the Evidence Passport?
2. Should the final narrative emphasize a genuine trade, a refusal, or both after competition data
   exists?
3. Which real Passport best demonstrates the architecture without exposing broker identifiers?
4. Will the hosted demo remain private, or should a sanitized public version be approved?
5. Which official event tags, if any, are required for the final form or social posts?
