# CAJNMNSTR Local Prototype

This is the local CAJNMNSTR dashboard and paper-integration preparation workspace.

- The displayed market and account values are representative demo data, not a live account rendering.
- The dedicated paper account has passed authenticated read-only verification; credentials remain local and Git-ignored.
- Closed-market stale data keeps operational health `PAUSED` and cannot produce execution authority.
- Terra accepts checked-in fixture/replay evidence through a strict structured-output adapter only.
- Alpaca adapters, the durable journal, health supervision, and reconciliation boundaries are prepared.
- Order submission remains disabled behind an explicit two-part paper-only gate.
- The desktop shortcut starts the page on `127.0.0.1:8841` and opens it in the default browser.
- If another application owns port 8841, the launcher reports the conflict and does not stop that process.

Use the navigation rail to inspect the command dashboard, Evidence Passport, journal, and system-isolation view.

Follow `docs/TONIGHT_OWNER_CHECKLIST.md` when adding the dedicated paper credentials locally.
