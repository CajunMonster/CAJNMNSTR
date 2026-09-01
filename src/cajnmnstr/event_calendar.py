from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
APPROVED_BLACKOUT_BEFORE_MINUTES = 15
APPROVED_BLACKOUT_AFTER_MINUTES = 30
APPROVED_AUTHORITY_SCOPE = "NEW_ENTRY_ONLY"
DEFAULT_CALENDAR_RESOURCE = "competition-tier1-calendar-2026-09-01_04.json"


def _aware_datetime(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TierOneEvent:
    event_id: str
    name: str
    scheduled_at: datetime
    timezone: str
    importance: str
    source_id: str

    @property
    def blackout_start(self) -> datetime:
        return self.scheduled_at - timedelta(minutes=APPROVED_BLACKOUT_BEFORE_MINUTES)

    @property
    def blackout_end(self) -> datetime:
        return self.scheduled_at + timedelta(minutes=APPROVED_BLACKOUT_AFTER_MINUTES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "scheduled_at": self.scheduled_at.isoformat(),
            "timezone": self.timezone,
            "importance": self.importance,
            "source_id": self.source_id,
            "blackout_start": self.blackout_start.isoformat(),
            "blackout_end": self.blackout_end.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EventCalendarContext:
    calendar_id: str
    state: str
    verification_state: str
    freshness_state: str
    checked_at: datetime
    verified_at: datetime | None
    coverage_start: datetime | None
    coverage_end: datetime | None
    events: tuple[TierOneEvent, ...]
    sources: tuple[dict[str, str], ...]
    entry_blocked: bool
    reason_code: str
    detail: str

    @property
    def available(self) -> bool:
        return self.verification_state == "VERIFIED" and self.freshness_state == "CURRENT"

    @property
    def hard_failure(self) -> str | None:
        return self.reason_code if self.entry_blocked else None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "calendar_id": self.calendar_id,
            "state": self.state,
            "verification_state": self.verification_state,
            "freshness_state": self.freshness_state,
            "checked_at": self.checked_at.isoformat(),
            "verified_at": None if self.verified_at is None else self.verified_at.isoformat(),
            "coverage_start": (
                None if self.coverage_start is None else self.coverage_start.isoformat()
            ),
            "coverage_end": None if self.coverage_end is None else self.coverage_end.isoformat(),
            "blackout_policy": {
                "before_minutes": APPROVED_BLACKOUT_BEFORE_MINUTES,
                "after_minutes": APPROVED_BLACKOUT_AFTER_MINUTES,
                "authority_scope": APPROVED_AUTHORITY_SCOPE,
            },
            "events": [event.to_dict() for event in self.events],
            "sources": list(self.sources),
            "entry_blocked": self.entry_blocked,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }

    def to_provenance(self) -> dict[str, Any]:
        payload = self.to_evidence()
        return {
            key: payload[key]
            for key in (
                "calendar_id",
                "state",
                "verification_state",
                "freshness_state",
                "verified_at",
                "coverage_start",
                "coverage_end",
                "blackout_policy",
                "events",
                "sources",
                "reason_code",
            )
        }


class StaticTierOneCalendar:
    """Deterministic, source-stamped Tier-1 calendar for the competition window."""

    def __init__(self, document: dict[str, Any] | None, *, load_error: str | None = None) -> None:
        self._load_error = load_error
        self._calendar_id = "UNAVAILABLE"
        self._verification_state = "UNVERIFIED"
        self._verified_at: datetime | None = None
        self._coverage_start: datetime | None = None
        self._coverage_end: datetime | None = None
        self._events: tuple[TierOneEvent, ...] = ()
        self._sources: tuple[dict[str, str], ...] = ()
        if document is not None and load_error is None:
            try:
                self._load(document)
            except (KeyError, TypeError, ValueError) as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"

    @classmethod
    def load_default(cls) -> StaticTierOneCalendar:
        try:
            resource = files("cajnmnstr.data").joinpath(DEFAULT_CALENDAR_RESOURCE)
            document = json.loads(resource.read_text(encoding="utf-8"))
        except (FileNotFoundError, ModuleNotFoundError, json.JSONDecodeError, OSError) as exc:
            return cls(None, load_error=f"{type(exc).__name__}: {exc}")
        return cls(document)

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> StaticTierOneCalendar:
        return cls(document)

    def _load(self, document: dict[str, Any]) -> None:
        if int(document["schema_version"]) != 1:
            raise ValueError("calendar schema_version must be 1")
        if str(document["timezone"]) != "America/New_York":
            raise ValueError("calendar timezone must be America/New_York")
        policy = document["blackout_policy"]
        if int(policy["before_minutes"]) != APPROVED_BLACKOUT_BEFORE_MINUTES:
            raise ValueError("calendar before_minutes does not match owner-approved policy")
        if int(policy["after_minutes"]) != APPROVED_BLACKOUT_AFTER_MINUTES:
            raise ValueError("calendar after_minutes does not match owner-approved policy")
        if str(policy["authority_scope"]) != APPROVED_AUTHORITY_SCOPE:
            raise ValueError("calendar blackout must apply to NEW_ENTRY_ONLY")

        sources: list[dict[str, str]] = []
        for source in document["sources"]:
            normalized = {
                "source_id": str(source["source_id"]),
                "publisher": str(source["publisher"]),
                "url": str(source["url"]),
                "accessed_date": str(source["accessed_date"]),
            }
            if not normalized["url"].startswith("https://"):
                raise ValueError("calendar sources must use HTTPS")
            sources.append(normalized)
        if not sources:
            raise ValueError("calendar requires at least one source")
        source_ids = {source["source_id"] for source in sources}

        events: list[TierOneEvent] = []
        event_ids: set[str] = set()
        for item in document["events"]:
            event = TierOneEvent(
                event_id=str(item["event_id"]),
                name=str(item["name"]),
                scheduled_at=_aware_datetime(item["scheduled_at"], field="scheduled_at"),
                timezone=str(item["timezone"]),
                importance=str(item["importance"]),
                source_id=str(item["source_id"]),
            )
            if event.event_id in event_ids:
                raise ValueError("calendar event_id values must be unique")
            if event.timezone != "America/New_York" or event.importance != "TIER_1":
                raise ValueError("calendar events must be America/New_York Tier-1 events")
            if event.source_id not in source_ids:
                raise ValueError("calendar event references an unknown source_id")
            event_ids.add(event.event_id)
            events.append(event)
        if not events:
            raise ValueError("calendar requires at least one Tier-1 event")

        coverage_start = _aware_datetime(document["coverage_start"], field="coverage_start")
        coverage_end = _aware_datetime(document["coverage_end"], field="coverage_end")
        verified_at = _aware_datetime(document["verified_at"], field="verified_at")
        if coverage_start >= coverage_end:
            raise ValueError("calendar coverage_start must precede coverage_end")
        if any(not coverage_start <= event.scheduled_at <= coverage_end for event in events):
            raise ValueError("calendar event falls outside declared coverage")

        self._calendar_id = str(document["calendar_id"])
        self._verification_state = str(document["verification_state"])
        self._verified_at = verified_at
        self._coverage_start = coverage_start
        self._coverage_end = coverage_end
        self._events = tuple(sorted(events, key=lambda item: (item.scheduled_at, item.event_id)))
        self._sources = tuple(sources)

    def context_at(self, decision_at: datetime) -> EventCalendarContext:
        checked_at = decision_at.astimezone(UTC)
        base = {
            "calendar_id": self._calendar_id,
            "checked_at": checked_at,
            "verified_at": self._verified_at,
            "coverage_start": self._coverage_start,
            "coverage_end": self._coverage_end,
            "sources": self._sources,
        }
        if self._load_error is not None:
            return EventCalendarContext(
                **base,
                state="UNAVAILABLE",
                verification_state="UNVERIFIED",
                freshness_state="UNAVAILABLE",
                events=(),
                entry_blocked=True,
                reason_code="EVENT_CALENDAR_UNAVAILABLE",
                detail="The checked-in Tier-1 calendar could not be loaded or validated.",
            )
        if self._verification_state != "VERIFIED":
            return EventCalendarContext(
                **base,
                state="UNAVAILABLE",
                verification_state=self._verification_state,
                freshness_state="UNVERIFIED",
                events=(),
                entry_blocked=True,
                reason_code="EVENT_CALENDAR_UNVERIFIED",
                detail="The Tier-1 calendar is not marked VERIFIED.",
            )
        if self._verified_at is None or checked_at < self._verified_at:
            return EventCalendarContext(
                **base,
                state="UNAVAILABLE",
                verification_state="UNVERIFIED",
                freshness_state="NOT_YET_VERIFIED",
                events=(),
                entry_blocked=True,
                reason_code="EVENT_CALENDAR_UNVERIFIED",
                detail="The calendar had not yet been verified at the decision timestamp.",
            )
        if self._coverage_start is None or self._coverage_end is None:
            raise AssertionError("validated calendar coverage is missing")
        if checked_at < self._coverage_start:
            return EventCalendarContext(
                **base,
                state="UNAVAILABLE",
                verification_state="VERIFIED",
                freshness_state="NOT_YET_CURRENT",
                events=(),
                entry_blocked=True,
                reason_code="EVENT_CALENDAR_COVERAGE_NOT_STARTED",
                detail="The verified competition calendar coverage has not started.",
            )
        if checked_at > self._coverage_end:
            return EventCalendarContext(
                **base,
                state="UNAVAILABLE",
                verification_state="VERIFIED",
                freshness_state="EXPIRED",
                events=(),
                entry_blocked=True,
                reason_code="EVENT_CALENDAR_COVERAGE_EXPIRED",
                detail="The verified competition calendar coverage has expired.",
            )

        session_date = checked_at.astimezone(NEW_YORK).date()
        events = tuple(
            event
            for event in self._events
            if event.scheduled_at.astimezone(NEW_YORK).date() == session_date
        )
        if not events:
            return EventCalendarContext(
                **base,
                state="VERIFIED_NO_NEARBY_EVENT",
                verification_state="VERIFIED",
                freshness_state="CURRENT",
                events=(),
                entry_blocked=False,
                reason_code="EVENT_CALENDAR_VERIFIED_CLEAR",
                detail="Calendar verified; no Tier-1 event is scheduled for this session.",
            )

        active = tuple(
            event for event in events if event.blackout_start <= checked_at <= event.blackout_end
        )
        if active:
            names = ", ".join(event.name for event in active)
            return EventCalendarContext(
                **base,
                state="DURING_BLACKOUT",
                verification_state="VERIFIED",
                freshness_state="CURRENT",
                events=active,
                entry_blocked=True,
                reason_code="TIER1_EVENT_BLACKOUT_ACTIVE",
                detail=f"Tier-1 new-entry blackout is active for: {names}.",
            )
        if checked_at < min(event.blackout_start for event in events):
            return EventCalendarContext(
                **base,
                state="BEFORE_BLACKOUT",
                verification_state="VERIFIED",
                freshness_state="CURRENT",
                events=events,
                entry_blocked=False,
                reason_code="TIER1_EVENT_BLACKOUT_AHEAD",
                detail="Verified Tier-1 event blackout is scheduled later in this session.",
            )
        return EventCalendarContext(
            **base,
            state="AFTER_BLACKOUT",
            verification_state="VERIFIED",
            freshness_state="CURRENT",
            events=events,
            entry_blocked=False,
            reason_code="TIER1_EVENT_BLACKOUT_EXPIRED",
            detail="The session's verified Tier-1 event blackout has expired.",
        )
