import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from cajnmnstr.ai import fail_closed_analysis
from cajnmnstr.decision_cycle import EvidenceCalculator, ReplayRefereePolicy
from cajnmnstr.event_calendar import (
    APPROVED_BLACKOUT_AFTER_MINUTES,
    APPROVED_BLACKOUT_BEFORE_MINUTES,
    DEFAULT_CALENDAR_RESOURCE,
    StaticTierOneCalendar,
)
from cajnmnstr.health import ENTRY_CRITICAL_COMPONENTS, NONCRITICAL_FOR_EXIT_COMPONENTS
from cajnmnstr.models import RefereeVerdict

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "replay" / "spy-decision-cycle.json"


def calendar_document() -> dict:
    resource = files("cajnmnstr.data").joinpath(DEFAULT_CALENDAR_RESOURCE)
    document = json.loads(resource.read_text(encoding="utf-8"))
    document["verified_at"] = "2026-09-01T00:00:00-04:00"
    return document


def at(hour: int, minute: int, *, day: int = 1, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


def test_verified_event_before_during_and_after_blackout() -> None:
    calendar = StaticTierOneCalendar.from_document(calendar_document())

    before = calendar.context_at(at(13, 44))
    active = calendar.context_at(at(13, 45))
    active_through_endpoint = calendar.context_at(at(14, 30))
    after = calendar.context_at(at(14, 30, second=1))

    assert before.state == "BEFORE_BLACKOUT"
    assert before.entry_blocked is False
    assert {event.name for event in before.events} == {
        "Job Openings and Labor Turnover Survey (JOLTS)",
        "ISM Manufacturing PMI",
    }
    assert active.state == "DURING_BLACKOUT"
    assert active.entry_blocked is True
    assert active.hard_failure == "TIER1_EVENT_BLACKOUT_ACTIVE"
    assert active_through_endpoint.state == "DURING_BLACKOUT"
    assert after.state == "AFTER_BLACKOUT"
    assert after.entry_blocked is False
    assert after.verification_state == "VERIFIED"
    assert after.freshness_state == "CURRENT"


def test_verified_no_nearby_event_is_explicit_clear_evidence() -> None:
    context = StaticTierOneCalendar.from_document(calendar_document()).context_at(
        at(15, 0, day=2)
    )

    assert context.state == "VERIFIED_NO_NEARBY_EVENT"
    assert context.available is True
    assert context.entry_blocked is False
    assert context.events == ()
    assert context.reason_code == "EVENT_CALENDAR_VERIFIED_CLEAR"


def test_unverified_calendar_and_future_verification_fail_closed() -> None:
    unverified = calendar_document()
    unverified["verification_state"] = "UNVERIFIED"
    unverified_context = StaticTierOneCalendar.from_document(unverified).context_at(at(16, 0))

    future = calendar_document()
    future["verified_at"] = "2026-09-01T13:00:00-04:00"
    future_context = StaticTierOneCalendar.from_document(future).context_at(at(16, 0))

    assert unverified_context.entry_blocked is True
    assert unverified_context.reason_code == "EVENT_CALENDAR_UNVERIFIED"
    assert future_context.entry_blocked is True
    assert future_context.freshness_state == "NOT_YET_VERIFIED"


def test_calendar_coverage_expiration_fails_closed() -> None:
    context = StaticTierOneCalendar.from_document(calendar_document()).context_at(
        at(20, 0, day=4, second=1)
    )

    assert context.state == "UNAVAILABLE"
    assert context.freshness_state == "EXPIRED"
    assert context.entry_blocked is True
    assert context.hard_failure == "EVENT_CALENDAR_COVERAGE_EXPIRED"


def test_malformed_policy_fails_closed_without_changing_approved_values() -> None:
    malformed = calendar_document()
    malformed["blackout_policy"]["after_minutes"] = 31
    context = StaticTierOneCalendar.from_document(malformed).context_at(at(16, 0))

    assert APPROVED_BLACKOUT_BEFORE_MINUTES == 15
    assert APPROVED_BLACKOUT_AFTER_MINUTES == 30
    assert context.state == "UNAVAILABLE"
    assert context.entry_blocked is True
    assert context.reason_code == "EVENT_CALENDAR_UNAVAILABLE"


def test_blackout_is_entry_critical_but_noncritical_for_exit() -> None:
    context = StaticTierOneCalendar.from_document(calendar_document()).context_at(at(14, 0))

    assert context.state == "DURING_BLACKOUT"
    assert context.entry_blocked is True
    assert "event_calendar" in ENTRY_CRITICAL_COMPONENTS
    assert "event_calendar" in NONCRITICAL_FOR_EXIT_COMPONENTS


def test_referee_still_blocks_any_calendar_hard_failure() -> None:
    replay = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    replay["market_sets"]["bullish"]["event_calendar"] = {
        "state": "DURING_BLACKOUT",
        "verification_state": "VERIFIED",
        "freshness_state": "CURRENT",
        "entry_blocked": True,
        "reason_code": "TIER1_EVENT_BLACKOUT_ACTIVE",
    }
    snapshot = EvidenceCalculator().build(replay, replay["scenarios"][0])
    analysis = fail_closed_analysis(
        model="gpt-5.6-terra",
        failure_code="TEST_ONLY",
        failure_detail="Referee must inspect hard failures before AI disposition.",
    )
    decision = ReplayRefereePolicy().evaluate(snapshot, analysis)

    assert "TIER1_EVENT_BLACKOUT_ACTIVE" in snapshot.hard_failures
    assert decision.verdict is RefereeVerdict.BLOCK
    assert decision.reason_code == "HARD_DATA_INVALID"
