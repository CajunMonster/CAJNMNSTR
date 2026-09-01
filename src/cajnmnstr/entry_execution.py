from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .errors import AuthorityDeniedError
from .health import HealthReport
from .journal import Journal
from .live_snapshot import LiveDecisionOutcome, LiveEvidenceCollection, write_dashboard_state
from .models import EventType, HealthState, OrderCandidate, RefereeVerdict
from .ports import BrokerReader, PaperExecutor
from .position_policy import INITIAL_POLICY_VERSION, build_initial_position_plan
from .services import OperatorAuthorityPath, PaperExecutionCoordinator

AUTONOMOUS_ENTRY_RATIONALE = (
    "Owner-approved autonomous PAPER competition operation under the frozen initial position "
    "policy and deterministic Referee authority."
)

NEW_YORK = ZoneInfo("America/New_York")
TERMINAL_UNFILLED_STATUSES = frozenset({"CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "REPLACED"})
PENDING_ENTRY_STATUSES = frozenset(
    {
        "ACCEPTED",
        "NEW",
        "PENDING_NEW",
        "PARTIALLY_FILLED",
        "PENDING_CANCEL",
        "PENDING_REPLACE",
        "SUBMISSION_PENDING",
        "SUBMIT_UNKNOWN",
    }
)


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _normalize_status(value: object) -> str:
    return str(value or "UNKNOWN").strip().upper()


@dataclass(frozen=True, slots=True)
class AutonomousEntryResult:
    state: str
    submission_attempted: bool
    broker_status: str | None = None
    detail: str | None = None


class AutonomousPaperEntryHandler:
    """Register the frozen exit plan, then use the sole deterministic entry authority path."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        broker: BrokerReader,
        executor: PaperExecutor,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.broker = broker
        self.executor = executor
        self._health: HealthReport | HealthState = HealthState.PAUSED
        self.coordinator = PaperExecutionCoordinator(
            settings,
            journal,
            broker,
            executor,
            lambda: self._health,
        )
        self.authority = OperatorAuthorityPath(
            settings,
            journal,
            self.coordinator,
            lambda: self._health,
        )

    def reconcile(self, collection: LiveEvidenceCollection) -> str:
        """Classify an in-flight entry without ever retrying a broker mutation blindly."""
        active = self.journal.active_position_lifecycles()
        if collection.positions:
            self._cancel_partial_entry_remainder(collection, active)
            return "POSITION_OPEN"
        if not active:
            return "ENTRY_READY"
        if len(active) != 1:
            return self._critical(
                "Multiple active position lifecycles violate the one-position invariant."
            )

        lifecycle = active[0]
        plan = lifecycle["plan"]
        entry_records = self._entry_records(plan.entry_passport_id)
        if not entry_records:
            self._abort_plan(
                lifecycle,
                reason_code="ENTRY_PLAN_WITHOUT_ORDER_RECOVERED",
                detail=(
                    "A durable plan had no order authorization or broker write; it was retired "
                    "without submitting after restart."
                ),
            )
            return "ENTRY_ABORTED_RECOVERED"
        if len(entry_records) != 1:
            return self._critical(
                "An entry Passport has more than one durable buy-to-open identity."
            )

        record = entry_records[0]
        status = _normalize_status(record["status"])
        if status in TERMINAL_UNFILLED_STATUSES:
            self._abort_plan(
                lifecycle,
                reason_code=f"ENTRY_ORDER_{status}",
                detail="The unfilled entry order reached a terminal broker state.",
            )
            return "ENTRY_ABORTED_RECOVERED"
        if status in {"ATTEMPT_RESERVED", "AUTHORITY_GRANTED"}:
            self._abort_plan(
                lifecycle,
                reason_code="ENTRY_AUTHORITY_NOT_SUBMITTED_RECOVERED",
                detail=(
                    "The durable entry identity never reached broker submission and was not "
                    "replayed after recovery."
                ),
            )
            return "ENTRY_ABORTED_RECOVERED"
        if status == "SUBMISSION_FAILED" and collection.reconciliation.matched:
            self._abort_plan(
                lifecycle,
                reason_code="ENTRY_SUBMISSION_FAILED_RECONCILED",
                detail="A definite entry failure reconciled with no broker position or order.",
            )
            return "ENTRY_ABORTED_RECOVERED"
        if status == "FILLED":
            return self._critical(
                "The entry order is filled but the broker position is not yet reconciled."
            )
        if status in PENDING_ENTRY_STATUSES or not collection.reconciliation.matched:
            if status == "SUBMIT_UNKNOWN":
                self._open_incident(
                    "Entry submission remains unknown; reconcile by durable identity and never "
                    "retry blindly."
                )
                return "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
            return "ENTRY_PENDING_RECONCILIATION"
        return self._critical(f"Unrecognized entry lifecycle status: {status}")

    def submit(
        self,
        outcome: LiveDecisionOutcome,
        *,
        dashboard_path: Path | None = None,
    ) -> AutonomousEntryResult:
        """Submit one fresh PAPER candidate only after every deterministic authority exists."""
        decision = outcome.decision
        collection = outcome.collection
        candidate = decision.selection.candidate
        if decision.operator_review.state != "READY_FOR_OPERATOR_REVIEW" or candidate is None:
            return AutonomousEntryResult("ENTRY_NOT_ACTIONABLE", False)
        if decision.referee.verdict not in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}:
            return AutonomousEntryResult("ENTRY_REFEREE_DENIED", False)
        if not self.settings.entry_armed:
            return self._denied("ENTRY_AUTHORITY_NOT_ARMED", decision.passport_id, candidate)
        if not self.settings.position_management_armed:
            return self._denied("POSITION_MANAGEMENT_NOT_ARMED", decision.passport_id, candidate)
        if self.settings.broker_lock:
            return self._denied("BROKER_LOCK_ACTIVE", decision.passport_id, candidate)
        if not outcome.session_risk.entry_allowed:
            return self._denied(outcome.session_risk.reason_code, decision.passport_id, candidate)
        if outcome.health.state is not HealthState.HEALTHY or not outcome.health.entry_armed:
            return self._denied("ENTRY_CRITICAL_HEALTH_BLOCKED", decision.passport_id, candidate)
        if not outcome.health.position_management_armed:
            return self._denied("EXIT_PATH_NOT_HEALTHY", decision.passport_id, candidate)
        if collection.positions or collection.open_orders or not collection.reconciliation.matched:
            return self._denied("BROKER_NOT_FLAT_RECONCILED", decision.passport_id, candidate)
        if self.journal.has_broker_uncertainty() or self.journal.has_unverified_exit():
            return self._denied("DURABLE_BROKER_UNCERTAINTY", decision.passport_id, candidate)
        if self.journal.active_position_lifecycles():
            return self._denied("ONE_POSITION_SLOT_RESERVED", decision.passport_id, candidate)

        passport = self.journal.get_passport(decision.passport_id)
        referee = self.journal.get_referee_result(decision.passport_id)
        if passport is None or passport["state"] != "SEALED" or referee is None:
            return self._denied("SEALED_AUTHORITY_MISSING", decision.passport_id, candidate)
        if not self._candidate_matches_passport(candidate, passport["payload"]):
            return self._denied("SELECTOR_PASSPORT_MISMATCH", decision.passport_id, candidate)

        plan_id = self._plan_id(decision.passport_id)
        try:
            plan = build_initial_position_plan(
                passport["payload"],
                referee,
                plan_id=plan_id,
                entry_passport_id=decision.passport_id,
                symbol=candidate.symbol,
                maximum_quantity=candidate.quantity,
                strategy_version=INITIAL_POLICY_VERSION,
                rationale=AUTONOMOUS_ENTRY_RATIONALE,
            )
            if not self.journal.register_position_plan(plan):
                return self._denied(
                    "POSITION_PLAN_REGISTRATION_CONFLICT",
                    decision.passport_id,
                    candidate,
                )
        except (ValueError, AuthorityDeniedError) as exc:
            return self._denied(
                "POSITION_PLAN_INVALID",
                decision.passport_id,
                candidate,
                detail=str(exc),
            )

        self.journal.append_event(
            EventType.AUTHORITY_TRANSITION,
            source="autonomous_position_plan",
            passport_id=decision.passport_id,
            correlation_id=candidate.client_order_id,
            payload={
                "plan_id": plan.plan_id,
                "policy_version": INITIAL_POLICY_VERSION,
                "maximum_quantity": plan.maximum_quantity,
                "stop_loss_fraction": plan.stop_loss_fraction,
                "profit_target_fraction": plan.profit_target_fraction,
                "time_stop_duration_minutes": plan.time_stop_duration_minutes,
                "forced_eod_at": plan.forced_eod_at,
                "invalidation_formula_version": plan.invalidation_formula_version,
                "owner_approved": True,
                "broker_submission_allowed": False,
                "authority_evaluation_next": True,
            },
            protective_action=(
                "Submit only through sealed Passport, Referee, session-risk, and coordinator "
                "authority."
            ),
        )

        self._health = outcome.health
        try:
            order = self.authority.execute(
                passport_id=decision.passport_id,
                candidate=candidate,
            )
        except Exception as exc:
            status = self.journal.broker_order_status(candidate.client_order_id)
            if status is None:
                lifecycle = self.journal.position_lifecycle(symbol=candidate.symbol)
                if lifecycle is not None:
                    self._abort_plan(
                        lifecycle,
                        reason_code="ENTRY_DENIED_BEFORE_BROKER",
                        detail=str(exc),
                    )
                self.journal.append_event(
                    EventType.AUTHORITY_TRANSITION,
                    source="autonomous_entry_handler",
                    passport_id=decision.passport_id,
                    correlation_id=candidate.client_order_id,
                    severity="WARNING",
                    payload={
                        "execution_allowed": False,
                        "reason_code": "ENTRY_DENIED_BEFORE_BROKER",
                        "error_type": type(exc).__name__,
                        "broker_submission_attempted": False,
                    },
                    protective_action="Continue monitoring after durable slot recovery.",
                )
                return AutonomousEntryResult(
                    "ENTRY_BLOCKED_BEFORE_BROKER",
                    False,
                    detail=str(exc),
                )
            else:
                self.journal.update_position_lifecycle(
                    plan_id=plan.plan_id,
                    state=(
                        "SUBMIT_UNKNOWN"
                        if status == "SUBMIT_UNKNOWN"
                        else "ENTRY_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
                    ),
                    broker_quantity=None,
                    payload={
                        "entry_client_order_id": candidate.client_order_id,
                        "entry_submission_status": status,
                        "reconciliation_required": True,
                        "blind_retry_allowed": False,
                    },
                )
            state = (
                "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
                if status == "SUBMIT_UNKNOWN"
                else "ENTRY_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
            )
            self._open_incident(
                f"Autonomous entry did not complete deterministically: {type(exc).__name__}."
            )
            return AutonomousEntryResult(state, status is not None, status, str(exc))

        broker_status = _normalize_status(order.status)
        state = (
            "ENTRY_FILLED_PENDING_POSITION"
            if order.filled_quantity >= Decimal(candidate.quantity)
            else "ENTRY_PARTIALLY_FILLED"
            if order.filled_quantity > 0
            else "ENTRY_PENDING_FILL"
        )
        self.journal.update_position_lifecycle(
            plan_id=plan.plan_id,
            state=state,
            broker_quantity=(None if order.filled_quantity <= 0 else order.filled_quantity),
            payload={
                "entry_client_order_id": candidate.client_order_id,
                "entry_broker_status": broker_status,
                "entry_filled_quantity": str(order.filled_quantity),
                "broker_flat_verified": False,
                "reconciliation_required": True,
            },
        )
        result = AutonomousEntryResult(state, True, broker_status)
        self._publish_dashboard(outcome, result, dashboard_path)
        return result

    def _entry_records(self, passport_id: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.journal.broker_order_records()
            if str(record["passport_id"]) == passport_id
            and record["payload"].get("intent", {}).get("position_intent") == "buy_to_open"
        ]

    def _candidate_matches_passport(
        self,
        candidate: OrderCandidate,
        payload: dict[str, Any],
    ) -> bool:
        try:
            sealed = payload["option_selection"]["candidate"]
            return (
                isinstance(sealed, dict)
                and str(sealed["symbol"]) == candidate.symbol
                and int(sealed["quantity"]) == candidate.quantity
                and str(sealed["side"]) == "buy"
                and str(sealed["position_intent"]) == "buy_to_open"
                and _decimal(sealed["limit_price"]) == candidate.limit_price
                and _decimal(sealed["decision_bid"]) == candidate.decision_bid
                and _decimal(sealed["decision_ask"]) == candidate.decision_ask
                and str(sealed["client_order_id"]) == candidate.client_order_id
                and str(sealed["quote_at"]) == candidate.quote_at.isoformat()
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _cancel_partial_entry_remainder(
        self,
        collection: LiveEvidenceCollection,
        active: list[dict[str, Any]],
    ) -> None:
        if len(active) != 1:
            return
        lifecycle = active[0]
        durable = lifecycle["lifecycle"]
        if durable.get("entry_cancel_attempted_at") is not None:
            return
        entry_records = self._entry_records(lifecycle["plan"].entry_passport_id)
        if len(entry_records) != 1:
            return
        client_order_id = str(entry_records[0]["client_order_id"])
        order = next(
            (
                item
                for item in collection.open_orders
                if item.client_order_id == client_order_id
                and item.filled_quantity > 0
                and item.filled_quantity < item.quantity
            ),
            None,
        )
        if order is None:
            return
        attempted_at = datetime.now(UTC)
        try:
            self.executor.cancel_order(order.broker_order_id)
            state = "ENTRY_PARTIAL_FILL_CANCEL_PENDING"
            error_type = None
        except Exception as exc:
            state = "ENTRY_CANCEL_UNKNOWN"
            error_type = type(exc).__name__
            self._open_incident(
                "Partial-entry remainder cancellation is uncertain; reconcile without retry."
            )
        self.journal.update_position_lifecycle(
            plan_id=lifecycle["plan_id"],
            state=state,
            broker_quantity=sum(
                (
                    position.quantity
                    for position in collection.positions
                    if position.symbol == lifecycle["symbol"]
                ),
                Decimal("0"),
            ),
            payload={
                "entry_cancel_attempted_at": attempted_at.isoformat(),
                "entry_cancel_error_type": error_type,
                "entry_cancel_blind_retry_allowed": False,
            },
        )

    def _abort_plan(
        self,
        lifecycle: dict[str, Any],
        *,
        reason_code: str,
        detail: str,
    ) -> None:
        self.journal.update_position_lifecycle(
            plan_id=str(lifecycle["plan_id"]),
            state="ENTRY_ABORTED",
            broker_quantity=Decimal("0"),
            payload={
                "entry_aborted_at": datetime.now(UTC).isoformat(),
                "entry_abort_reason": reason_code,
                "entry_abort_detail": detail,
                "broker_flat_verified": False,
            },
        )
        self.journal.append_event(
            EventType.BROKER_LIFECYCLE,
            source="autonomous_entry_recovery",
            passport_id=str(lifecycle["entry_passport_id"]),
            payload={
                "plan_id": lifecycle["plan_id"],
                "state": "ENTRY_ABORTED",
                "reason_code": reason_code,
                "new_entry_slot_released": True,
                "broker_submission_retried": False,
            },
        )

    def _denied(
        self,
        reason: str,
        passport_id: str,
        candidate: OrderCandidate,
        *,
        detail: str | None = None,
    ) -> AutonomousEntryResult:
        self.journal.append_event(
            EventType.AUTHORITY_TRANSITION,
            source="autonomous_entry_handler",
            passport_id=passport_id,
            correlation_id=candidate.client_order_id,
            severity="WARNING",
            payload={
                "execution_allowed": False,
                "reason_code": reason,
                "detail": detail,
                "broker_submission_attempted": False,
            },
            protective_action="Continue monitoring without opening exposure.",
        )
        return AutonomousEntryResult(f"ENTRY_BLOCKED_{reason}", False, detail=detail)

    def _critical(self, detail: str) -> str:
        self._open_incident(detail)
        return "ENTRY_RECONCILIATION_REQUIRED"

    def _open_incident(self, detail: str) -> None:
        self.journal.open_incident(
            component="autonomous_entry",
            severity="CRITICAL",
            state=HealthState.PAUSED.value,
            message=detail,
            protective_action=(
                "Block new entry, preserve deterministic position management, and reconcile."
            ),
        )

    @staticmethod
    def _plan_id(passport_id: str) -> str:
        digest = hashlib.sha256(passport_id.encode("utf-8")).hexdigest()[:24]
        return f"cajnmnstr-plan-{digest}"

    @staticmethod
    def _publish_dashboard(
        outcome: LiveDecisionOutcome,
        result: AutonomousEntryResult,
        dashboard_path: Path | None,
    ) -> None:
        if dashboard_path is None:
            return
        dashboard = outcome.dashboard
        dashboard["updated_at"] = datetime.now(UTC).isoformat()
        dashboard["truth_label"] = (
            f"PAPER · ENTRY {result.broker_status or 'ATTEMPTED'} · RECONCILIATION REQUIRED"
        )
        dashboard["decision"]["state"] = result.state
        dashboard["controls"]["broker_submission_allowed"] = False
        for stage in dashboard.get("execution", []):
            if stage.get("stage") == "SUBMITTED":
                stage["status"] = result.broker_status or "ATTEMPTED"
                stage["detail"] = "Autonomous PAPER authority path invoked once"
            elif stage.get("stage") == "FILLED":
                stage["status"] = (
                    "PENDING RECONCILIATION"
                    if result.state != "ENTRY_FILLED_PENDING_POSITION"
                    else "BROKER REPORTED FILL"
                )
                stage["detail"] = "Broker position reconciliation remains authoritative"
        dashboard.get("activity", []).insert(
            0,
            {
                "time": datetime.now(UTC).astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "ENTRY",
                "text": f"PAPER entry lifecycle · {result.state}",
                "mode": "PAPER",
            },
        )
        write_dashboard_state(dashboard_path, dashboard)
