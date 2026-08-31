import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the CAJNMNSTR dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>CAJNMNSTR — SPY Options Agent<\/title>/i);
  assert.match(html, /SPY OPTIONS AGENT/);
  assert.match(html, /MARKET REGIME/);
  assert.match(html, /OPTIONS \/ OPRA/);
  assert.match(html, /TERRA PROPOSAL/);
  assert.match(html, /CURRENT DECISION/);
  assert.match(html, /REFEREE VERDICT/);
  assert.match(html, /PAPER ACCOUNT/);
  assert.match(html, /EXECUTION STATUS/);
  assert.match(html, /RECENT ACTIVITY/);
  assert.match(html, /SYSTEM HEALTH/);
  assert.match(html, /PAPER/);
  assert.match(html, /MARKET CLOSED/);
  assert.match(html, /MONITORING(?:<!-- -->|\s)*·(?:<!-- -->|\s)*NO DECISION/);
  assert.match(html, /ENTRY(?:<!-- -->|\s)*DISABLED/);
  assert.match(html, /POSITION MANAGEMENT/);
  assert.match(html, /BROKER LOCK/);
  assert.match(html, /CLEAR/);
  assert.match(html, /STOP BEFORE BROKER/);
  assert.doesNotMatch(html, /AI MAKES THE CASE|EVIDENCE DECIDES/);
  assert.doesNotMatch(html, /GOLD \/ XAU|SILVER \/ XAG|BITCOIN \/ BTC/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});

test("dashboard state exposes separate broker authorities without the legacy switch", async () => {
  const state = JSON.parse(
    await readFile(new URL("../public/dashboard-state.json", import.meta.url), "utf8"),
  );
  assert.equal(state.controls.entry_enabled, false);
  assert.equal(state.controls.entry_armed, false);
  assert.equal(state.controls.position_management_enabled, true);
  assert.equal(state.controls.position_management_armed, false);
  assert.equal(state.controls.broker_lock_active, false);
  assert.equal(state.controls.broker_submission_allowed, false);
  assert.equal("execution_enabled" in state.controls, false);
  assert.equal("execution_armed" in state.controls, false);
  assert.equal(state.mode, "PAPER");
  assert.equal(state.operational_state, "PAUSED");
  assert.equal(state.market.session, "MARKET CLOSED");
  assert.equal(state.market.data_state, "STALE");
  assert.equal(state.market.feed, "ALPACA SIP");
  assert.equal(state.options.feed, "OPRA");
  assert.equal(state.decision.state, "MONITORING_PAUSED");
  assert.equal(state.proposal.direction, "NOT_EVALUATED");
  assert.equal(state.passport.sealed, false);
});
