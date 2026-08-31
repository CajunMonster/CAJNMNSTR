import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

test("all dashboard navigation and evidence controls have real view handlers", () => {
  for (const label of ["COMMAND", "EVIDENCE", "JOURNAL", "SYSTEM"]) {
    assert.match(page, new RegExp(`"${label}"`));
  }
  assert.match(page, /OPEN EVIDENCE PASSPORT/);
  assert.match(page, /onClick=\{openEvidence\}/);
  assert.match(page, /onClick=\{\(\) => setActiveView\(view\)\}/);
  assert.match(page, /activeView === "dashboard"/);
  assert.match(page, /activeView === "evidence"/);
  assert.match(page, /activeView === "journal"/);
  assert.match(page, /activeView === "system"/);
});

test("operator clock is explicitly Central Time, 12-hour, and retained on narrow screens", () => {
  assert.match(page, /hour12: true/);
  assert.match(page, /timeZone: "America\/Chicago"/);
  assert.match(page, /OPERATOR CLOCK/);
  assert.match(page, /\{clock\} CT/);
  assert.match(css, /\.operator-clock \{ grid-column: 1 \/ -1;/);
  assert.doesNotMatch(css, /\.truth-banner b \{ display: none;/);
});

test("detail views expose provenance, lifecycle, health, and truthful empty states", () => {
  for (const label of [
    "SPY PROVENANCE",
    "OPTIONS PROVENANCE",
    "MODE / TRUTH",
    "NOT SUBMITTED",
    "NO DURABLE JOURNAL EVENTS AVAILABLE",
    "NO COMPONENT HEALTH REPORT AVAILABLE",
    "NO VERIFIED CONNECTION STATE AVAILABLE",
    "NO SANITIZED OPTION SURFACE AVAILABLE",
  ]) {
    assert.match(page, new RegExp(label.replaceAll("/", "\\/")));
  }
  assert.match(page, /journal-view"><ExecutionPanel state=\{state\} \/><ActivityPanel/);
  assert.match(page, /showAuthority showConnections/);
  assert.match(page, /\{item\.time\} ET/);
});

test("the presentation source has no broker mutation or credential controls", () => {
  assert.doesNotMatch(page, /submitOrder|cancelOrder|replaceOrder|closePosition/i);
  assert.doesNotMatch(page, /api[_-]?key|secret[_-]?key|credential/i);
  assert.doesNotMatch(page, /ENTRY ENABLE|ARM EXECUTION|BROKER UNLOCK/i);
});
