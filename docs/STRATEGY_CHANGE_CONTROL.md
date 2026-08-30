# Competition strategy change control

CAJNMNSTR's competition strategy and risk parameters are frozen when live competition
trading begins. Paper results are observations, not permission to retune the strategy.

After the competition start marker is recorded, any strategy or risk-parameter change
requires all of the following before implementation:

- explicit owner approval;
- an approval timestamp in UTC;
- a written rationale that identifies the evidence and expected safety impact;
- a strategy version change; and
- replay and fail-closed regression tests before the changed version is eligible to run.

Bug fixes and safety corrections remain allowed. A safety correction must not quietly
relax a trading threshold or increase authority; if it does either, it is a strategy change
and follows the approval process above.

Do not repeatedly optimize thresholds from Monday-through-Thursday paper outcomes. Any
post-competition research belongs in a separately versioned evaluation and must not rewrite
the contemporaneous competition record.

## Competition marker

- Live competition trading started: `NO`
- Active strategy version: `competition-baseline-1`
- Start timestamp (UTC): `NOT_SET`
- Owner approval record: `NOT_SET`

## Approved change log

| UTC timestamp | From version | To version | Owner approval | Rationale |
| --- | --- | --- | --- | --- |
| Not started | — | `competition-baseline-1` | Baseline only | Initial frozen competition baseline |
