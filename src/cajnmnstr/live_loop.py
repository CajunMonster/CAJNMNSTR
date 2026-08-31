from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import Settings
from .journal import Journal
from .live_snapshot import FIVE_MINUTES, LiveDecisionOutcome, LiveEvidenceCollection
from .models import EventType

NEW_YORK = ZoneInfo("America/New_York")
READ_ONLY_LOOP_CONFIRMATION = "PAPER_READ_ONLY_LOOP"
DEFAULT_MONITOR_CADENCE_SECONDS = 60
MINIMUM_MONITOR_CADENCE_SECONDS = 30


class EvidenceCollector(Protocol):
    def collect(self) -> LiveEvidenceCollection: ...


class ContinuousLiveRunner(Protocol):
    collector: EvidenceCollector

    def run_collection(
        self,
        collection: LiveEvidenceCollection,
        *,
        dashboard_path: Path | None = None,
        health_path: Path | None = None,
    ) -> LiveDecisionOutcome: ...


class PositionManagementHandler(Protocol):
    def run_cycle(self, collection: LiveEvidenceCollection) -> str: ...


@dataclass(frozen=True, slots=True)
class ContinuousLoopResult:
    cycles: int
    canonical_decisions: int
    cached_decisions: int
    terminal_state: str
    last_epoch: str | None
    last_passport_id: str | None
    broker_submission_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _decision_epoch(collection: LiveEvidenceCollection) -> str | None:
    if not collection.completed_bars:
        return None
    return (collection.completed_bars[-1].timestamp + FIVE_MINUTES).astimezone(UTC).isoformat()


def _after_regular_session(collection: LiveEvidenceCollection) -> bool:
    if collection.clock.is_open:
        return False
    checked = collection.clock.timestamp.astimezone(NEW_YORK)
    next_open = collection.clock.next_open.astimezone(NEW_YORK)
    return next_open.date() > checked.date()


class ContinuousDecisionLoop:
    """Read-only loop: monitor each minute, evaluate once per completed five-minute bar."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        runner: ContinuousLiveRunner,
        *,
        position_manager: PositionManagementHandler | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.runner = runner
        self.position_manager = position_manager
        self._sleep = sleep

    def run(
        self,
        *,
        confirmation: str,
        cadence_seconds: int = DEFAULT_MONITOR_CADENCE_SECONDS,
        max_cycles: int | None = None,
        dashboard_path: Path | None = None,
        health_path: Path | None = None,
    ) -> ContinuousLoopResult:
        self.settings.require_credentials()
        if confirmation != READ_ONLY_LOOP_CONFIRMATION:
            raise ValueError(
                f"Continuous monitoring requires --confirm {READ_ONLY_LOOP_CONFIRMATION}"
            )
        if self.settings.entry_enabled or self.settings.entry_armed:
            raise ValueError("Read-only continuous monitoring requires entry authority disabled")
        if cadence_seconds < MINIMUM_MONITOR_CADENCE_SECONDS:
            raise ValueError(
                f"Monitoring cadence must be at least {MINIMUM_MONITOR_CADENCE_SECONDS} seconds"
            )
        if max_cycles is not None and max_cycles <= 0:
            raise ValueError("max_cycles must be positive when supplied")

        self.journal.initialize()
        evaluated_epochs: set[str] = set()
        cycles = 0
        canonical_decisions = 0
        cached_decisions = 0
        terminal_state = "MONITORING"
        last_epoch: str | None = None
        last_passport_id: str | None = None

        while True:
            cycles += 1
            try:
                terminal_state = "MONITORING"
                collection = self.runner.collector.collect()
                epoch = _decision_epoch(collection)
                last_epoch = epoch
                state = "MONITORING"

                if collection.positions:
                    if not self.settings.position_management_enabled:
                        state = "POSITION_MANAGEMENT_DISABLED"
                        self.journal.open_incident(
                            component="position_management",
                            severity="CRITICAL",
                            state="PAUSED",
                            message=(
                                "A verified open position exists while position management "
                                "is disabled."
                            ),
                            protective_action=(
                                "Keep new entries blocked and require explicit owner recovery."
                            ),
                        )
                    elif self.position_manager is None:
                        state = "POSITION_MANAGEMENT_HANDLER_REQUIRED"
                        self.journal.open_incident(
                            component="position_management",
                            severity="CRITICAL",
                            state="PAUSED",
                            message=(
                                "A verified open position requires the deterministic position-"
                                "management runtime; the read-only loop cannot submit exits."
                            ),
                            protective_action=(
                                "Keep new entries blocked and attach the approved deterministic "
                                "position-management handler."
                            ),
                        )
                    else:
                        state = self.position_manager.run_cycle(collection)
                elif collection.open_orders or not collection.reconciliation.matched:
                    state = "BROKER_RECONCILIATION_REQUIRED"
                elif collection.snapshot.hard_failures or collection.snapshot.stale_sources:
                    state = "NON_ACTIONABLE_EVIDENCE"
                    self.journal.append_event(
                        EventType.DATA_HEALTH_FAILURE,
                        source="continuous_live_loop",
                        severity="CRITICAL",
                        payload={
                            "decision_epoch": epoch,
                            "hard_failures": collection.snapshot.hard_failures,
                            "stale_sources": collection.snapshot.stale_sources,
                            "broker_submission_allowed": False,
                        },
                        protective_action=(
                            "Skip Terra for this epoch and keep new entries blocked."
                        ),
                    )
                elif epoch is None:
                    state = "WAITING_FOR_COMPLETED_BAR"
                elif epoch in evaluated_epochs:
                    state = "UNCHANGED_EVIDENCE_EPOCH"
                else:
                    evaluated_epochs.add(epoch)
                    outcome = self.runner.run_collection(
                        collection,
                        dashboard_path=dashboard_path,
                        health_path=health_path,
                    )
                    canonical_decisions += 1
                    cached_decisions += int(outcome.decision.ai_cached)
                    last_passport_id = outcome.decision.passport_id
                    state = outcome.decision.operator_review.state
                    if state == "READY_FOR_OPERATOR_REVIEW":
                        terminal_state = "OPERATOR_REVIEW_PENDING"

                self.journal.append_event(
                    EventType.CONNECTION,
                    source="continuous_live_loop",
                    severity=(
                        "WARNING"
                        if state
                        in {
                            "BROKER_RECONCILIATION_REQUIRED",
                            "NON_ACTIONABLE_EVIDENCE",
                            "POSITION_MANAGEMENT_DISABLED",
                            "POSITION_MANAGEMENT_HANDLER_REQUIRED",
                        }
                        else "INFO"
                    ),
                    payload={
                        "cycle": cycles,
                        "decision_epoch": epoch,
                        "state": state,
                        "entry_enabled": False,
                        "broker_submission_allowed": False,
                    },
                    protective_action=(
                        None
                        if state
                        not in {
                            "BROKER_RECONCILIATION_REQUIRED",
                            "NON_ACTIONABLE_EVIDENCE",
                            "POSITION_MANAGEMENT_DISABLED",
                            "POSITION_MANAGEMENT_HANDLER_REQUIRED",
                        }
                        else "Keep new entries blocked and preserve broker state."
                    ),
                )

                if terminal_state == "OPERATOR_REVIEW_PENDING":
                    break
                if state in {
                    "POSITION_MANAGEMENT_DISABLED",
                    "POSITION_MANAGEMENT_HANDLER_REQUIRED",
                }:
                    terminal_state = state
                    break
                if _after_regular_session(collection):
                    terminal_state = "REGULAR_SESSION_COMPLETE"
                    break
            except Exception as exc:
                terminal_state = "DEGRADED_RETRY_NEXT_CADENCE"
                self.journal.append_event(
                    EventType.INCIDENT,
                    source="continuous_live_loop",
                    severity="CRITICAL",
                    payload={
                        "cycle": cycles,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "occurred_at": datetime.now(UTC).isoformat(),
                        "broker_submission_allowed": False,
                    },
                    protective_action=(
                        "Treat broker/data state as uncertain; do not submit and reconcile on "
                        "the next scheduled cycle."
                    ),
                )

            if max_cycles is not None and cycles >= max_cycles:
                if terminal_state == "MONITORING":
                    terminal_state = "BOUNDED_RUN_COMPLETE"
                break
            self._sleep(cadence_seconds)

        return ContinuousLoopResult(
            cycles=cycles,
            canonical_decisions=canonical_decisions,
            cached_decisions=cached_decisions,
            terminal_state=terminal_state,
            last_epoch=last_epoch,
            last_passport_id=last_passport_id,
        )
