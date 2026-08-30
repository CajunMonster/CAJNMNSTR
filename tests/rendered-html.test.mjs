import assert from "node:assert/strict";
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
  assert.match(html, /REPLAY/);
  assert.match(html, /EXECUTION(?:<!-- -->|\s)*DISABLED/);
  assert.match(html, /STOP BEFORE BROKER/);
  assert.doesNotMatch(html, /AI MAKES THE CASE|EVIDENCE DECIDES/);
  assert.doesNotMatch(html, /GOLD \/ XAU|SILVER \/ XAG|BITCOIN \/ BTC/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});
