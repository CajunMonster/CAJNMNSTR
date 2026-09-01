# Verified Tier-1 competition calendar

CAJNMNSTR uses a bounded, checked-in calendar for the September 1–4, 2026 competition window.
It is deterministic evidence, not a live scraping dependency, and it expires at the end of the
declared coverage period.

## Owner-approved policy

- Tier-1 blackout: 15 minutes before through 30 minutes after the scheduled release.
- Authority affected: new entry only.
- Existing-position management and deterministic exits remain available subject to their normal
  exit-critical health and reconciliation requirements.
- Unverified, malformed, not-yet-verified, or expired calendar evidence fails new entry closed.

## Verified events

| Date | Event | Time | Blackout | Source |
|---|---|---:|---:|---|
| 2026-09-01 | Job Openings and Labor Turnover Survey (JOLTS) | 10:00 AM ET | 9:45–10:30 AM ET | BLS |
| 2026-09-01 | ISM Manufacturing PMI | 10:00 AM ET | 9:45–10:30 AM ET | ISM |
| 2026-09-03 | ISM Services PMI | 10:00 AM ET | 9:45–10:30 AM ET | ISM |
| 2026-09-04 | Employment Situation | 8:30 AM ET | 8:15–9:00 AM ET | BLS |

Official provenance:

- [BLS September 2026 release calendar](https://www.bls.gov/schedule/2026/09_sched_list.htm)
- [ISM 2026 report release calendar](https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/)

The runtime normalizes ET through `America/New_York` and retains ISO-8601 timestamps with offsets
in every live Evidence Snapshot. Coverage ends at 4:00 PM ET on September 4, 2026. The calendar
must be replaced and re-verified for any later session; it never silently rolls forward.

## Deterministic states

- `BEFORE_BLACKOUT`: a verified Tier-1 blackout is later in the current session.
- `DURING_BLACKOUT`: new-entry authority is blocked.
- `AFTER_BLACKOUT`: the current session's verified Tier-1 blackout has expired.
- `VERIFIED_NO_NEARBY_EVENT`: coverage is current and no Tier-1 event is scheduled that session.
- `UNAVAILABLE`: verification, schema, or coverage failed; new entry is blocked.

These states are included in the Terra evidence contract. Terra may still return `NO_TRADE` for
other evidence reasons. Calendar availability does not relax the Referee, selector, risk,
position-plan, execution, or reconciliation gates.
