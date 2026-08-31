import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

async function jsonFile(path) {
  return JSON.parse(await readFile(new URL(path, projectRoot), "utf8"));
}

function visit(value, path = "$") {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => visit(item, `${path}[${index}]`));
  }
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) => [
      { key, value: item, path: `${path}.${key}` },
      ...visit(item, `${path}.${key}`),
    ]);
  }
  return [];
}

test("sanitized public JSON exposes no credential or broker identity fields", async () => {
  const documents = await Promise.all([
    jsonFile("public/dashboard-state.json"),
    jsonFile("public/health.json"),
  ]);
  const forbiddenKeys = new Set([
    "account_id",
    "account_number",
    "api_key",
    "broker_order_id",
    "client_order_id",
    "credential",
    "openai_api_key",
    "password",
    "secret",
    "secret_key",
    "token",
  ]);
  const findings = documents.flatMap((document) =>
    visit(document).filter(({ key }) => forbiddenKeys.has(key.toLowerCase())),
  );
  assert.deepEqual(findings, []);

  const serialized = JSON.stringify(documents);
  assert.doesNotMatch(serialized, /sk-(?:proj-)?[A-Za-z0-9_-]{20,}/i);
  assert.doesNotMatch(serialized, /APCA-API-(?:KEY-ID|SECRET-KEY)/i);
});

test("public checkpoint remains read-only and non-actionable", async () => {
  const dashboard = await jsonFile("public/dashboard-state.json");
  const health = await jsonFile("public/health.json");

  assert.equal(dashboard.controls.entry_enabled, false);
  assert.equal(dashboard.controls.entry_armed, false);
  assert.equal(dashboard.controls.position_management_armed, false);
  assert.equal(dashboard.controls.broker_submission_allowed, false);
  assert.equal(dashboard.operational_state, "PAUSED");
  assert.equal(dashboard.market.session, "MARKET CLOSED");
  assert.equal(dashboard.market.data_state, "STALE");
  assert.equal(dashboard.decision.state, "MONITORING_PAUSED");
  assert.equal(dashboard.proposal.direction, "NOT_EVALUATED");
  assert.equal(dashboard.passport.sealed, false);
  assert.equal(health.broker_submission_allowed, false);
});

test("dashboard application has no mutation API route", async () => {
  const appEntries = await readdir(new URL("app/", projectRoot), { recursive: true });
  assert.equal(appEntries.some((entry) => /(^|[\\/])api([\\/]|$)/i.test(entry)), false);

  await assert.rejects(access(new URL("app/api/", projectRoot)));
});

test("local launcher serves continuously updated runtime JSON", async () => {
  const launcher = await readFile(
    new URL("launcher/Start-CAJNMNSTR.ps1", projectRoot),
    "utf8",
  );
  assert.match(launcher, /-ArgumentList\s+@\('dev'/);
  assert.doesNotMatch(launcher, /-ArgumentList\s+@\('start'/);
  assert.match(launcher, /activeListener\.OwningProcess/);
});
