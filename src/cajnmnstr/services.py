from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

from .config import Settings
from .errors import (
    AuthorityDeniedError,
    BrokerLockedError,
    DuplicateOrderIdentityError,
    ExecutionDisabledError,
    InvalidRefereeResultError,
)
from .health import HealthReport, authority_health
from .journal import Journal, write_emergency_incident
from .models import (
    AuthorityGrant,
    BrokerOrderSnapshot,
    EventType,
    HealthState,
    OrderCandidate,
    OrderIntent,
    ReconciliationReport,
    RefereeResult,
    RefereeVerdict,
)
from .ports import BrokerReader, PaperExecutor


def _submission_is_unknown(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()


def _execution_quality(
    *,
    intent: OrderIntent | dict[str, object],
    order: BrokerOrderSnapshot,
) -> dict[str, object] | None:
    if order.filled_quantity <= 0 or order.filled_avg_price is None:
        return None
    if isinstance(intent, OrderIntent):
        side = intent.side
        bid = intent.decision_bid
        ask = intent.decision_ask
        quote_at = intent.quote_at
        limit_price = intent.limit_price
    else:
        try:
            side = str(intent["side"])
            bid = Decimal(str(intent["decision_bid"]))
            ask = Decimal(str(intent["decision_ask"]))
            quote_at = datetime.fromisoformat(str(intent["quote_at"]))
            limit_price = Decimal(str(intent["limit_price"]))
        except (KeyError, TypeError, ValueError):
            return None
    fill = order.filled_avg_price
    midpoint = (bid + ask) / Decimal("2")
    adverse_to_midpoint = fill - midpoint if side == "buy" else midpoint - fill
    spread_paid = max(adverse_to_midpoint, Decimal("0"))
    pessimistic_reference = ask if side == "buy" else bid
    fill_vs_pessimistic = (
        fill - pessimistic_reference
        if side == "buy"
        else pessimistic_reference - fill
    )
    comparison_at = order.submitted_at or order.updated_at
    quote_age = Decimal(str((comparison_at - quote_at).total_seconds()))
    return {
        "decision_time_bid": bid,
        "decision_time_ask": ask,
        "midpoint": midpoint,
        "submitted_limit_price": limit_price,
        "actual_fill_price": fill,
        "filled_quantity": order.filled_quantity,
        "fill_vs_midpoint": adverse_to_midpoint,
        "quoted_spread": ask - bid,
        "quoted_spread_premium_percentage": (
            Decimal("0") if midpoint == 0 else (ask - bid) / midpoint * Decimal("100")
        ),
        "spread_paid": spread_paid,
        "spread_paid_premium_percentage": (
            Decimal("0") if fill == 0 else spread_paid / fill * Decimal("100")
        ),
        "quote_age_seconds": quote_age,
        "pessimistic_reference": "ENTRY_AT_ASK" if side == "buy" else "EXIT_AT_BID",
        "pessimistic_reference_price": pessimistic_reference,
        "fill_vs_pessimistic_reference": fill_vs_pessimistic,
        "official_competition_pnl_replacement": False,
    }


def _authority_denial_code(
    error: ExecutionDisabledError,
    *,
    position_intent: str,
) -> str:
    if isinstance(error, BrokerLockedError):
        return "BROKER_LOCK_ACTIVE"
    if position_intent == "sell_to_close":
        return "POSITION_MANAGEMENT_DISABLED"
    return "ENTRY_AUTHORITY_DISABLED"


def _persist_critical_authority_incident(
    settings: Settings,
    journal: Journal,
    *,
    component: str,
    message: str,
    protective_action: str,
) -> None:
    payload = {
        "component": component,
        "state": HealthState.PAUSED.value,
        "message": message,
        "protective_action": protective_action,
    }
    try:
        journal.open_incident(
            component=component,
            severity="CRITICAL",
            state=HealthState.PAUSED.value,
            message=message,
            protective_action=protective_action,
        )
        journal.append_event(
            EventType.INCIDENT,
            source="broker_authority",
            severity="CRITICAL",
            payload=payload,
            protective_action=protective_action,
        )
    except Exception as exc:
        write_emergency_incident(
            settings.emergency_incident_path,
            {**payload, "journal_error": type(exc).__name__},
        )


class DeterministicReferee:
    """Persists a policy result only after its Evidence Passport is sealed."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def issue(
        self,
        *,
        passport_id: str,
        verdict: RefereeVerdict,
        reason_code: str,
        max_quantity: int | None = None,
        max_limit_price: Decimal | None = None,
    ) -> RefereeResult:
        if not isinstance(verdict, RefereeVerdict):
            raise InvalidRefereeResultError("Referee verdict must be a RefereeVerdict")
        if not reason_code.strip():
            raise InvalidRefereeResultError("Referee reason code is required")
        permits_order = verdict in {
            RefereeVerdict.APPROVE,
            RefereeVerdict.REDUCE,
            RefereeVerdict.EXIT,
        }
        if permits_order and (
            max_quantity is None
            or max_quantity <= 0
            or max_limit_price is None
            or max_limit_price <= 0
        ):
            raise InvalidRefereeResultError(
                f"{verdict.value} requires positive quantity and premium limits"
            )
        if not permits_order and (max_quantity is not None or max_limit_price is not None):
            raise InvalidRefereeResultError(
                f"{verdict.value} cannot carry execution limits"
            )
        result = self.journal.record_referee_result(
            passport_id=passport_id,
            verdict=verdict.value,
            max_quantity=max_quantity,
            max_limit_price=(None if max_limit_price is None else str(max_limit_price)),
            reason_code=reason_code,
            payload={
                "verdict": verdict.value,
                "max_quantity": max_quantity,
                "max_limit_price": max_limit_price,
                "reason_code": reason_code,
            },
        )
        self.journal.append_event(
            EventType.REFEREE_VERDICT,
            source="deterministic_referee",
            passport_id=passport_id,
            payload={
                "result_id": result.result_id,
                "verdict": result.verdict,
                "max_quantity": result.max_quantity,
                "max_limit_price": result.max_limit_price,
                "reason_code": result.reason_code,
            },
        )
        return result


class PaperExecutionCoordinator:
    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        broker: BrokerReader,
        executor: PaperExecutor,
        health_state: Callable[[], HealthReport | HealthState],
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.broker = broker
        self.executor = executor
        self.health_state = health_state

    def submit(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        try:
            self.settings.require_order_authority(intent.position_intent)
        except ExecutionDisabledError as exc:
            if intent.position_intent == "sell_to_close" or isinstance(
                exc, BrokerLockedError
            ):
                component = (
                    "broker_lock"
                    if isinstance(exc, BrokerLockedError)
                    else "position_management_authority"
                )
                _persist_critical_authority_incident(
                    self.settings,
                    self.journal,
                    component=component,
                    message=str(exc),
                    protective_action=(
                        "Do not submit until the owner clears the broker lock."
                        if isinstance(exc, BrokerLockedError)
                        else "Restore explicit position-management authority or close manually."
                    ),
                )
            raise
        current_health = self.health_state()
        health_decision = authority_health(
            current_health,
            position_intent=intent.position_intent,
        )
        if not health_decision.allowed:
            raise ExecutionDisabledError(health_decision.message)
        self._verify_no_broker_uncertainty()
        if intent.position_intent == "sell_to_close":
            self._verify_existing_long_position(intent)
        else:
            self._verify_entry_flat_and_reconciled()
        payload = asdict(intent)
        if not self.journal.claim_authorized_order(
            client_order_id=intent.client_order_id,
            passport_id=intent.passport_id,
        ):
            self.journal.append_event(
                EventType.AUTHORITY_TRANSITION,
                source="paper_execution_coordinator",
                passport_id=(
                    intent.passport_id
                    if self.journal.passport_state(intent.passport_id) is not None
                    else None
                ),
                correlation_id=intent.client_order_id,
                severity="WARNING",
                payload={
                    "passport_id": intent.passport_id,
                    "verdict": None,
                    "authority_granted": AuthorityGrant.NONE.value,
                    "execution_allowed": False,
                    "reason_code": "COORDINATOR_AUTHORIZATION_MISSING",
                    "mock_broker_result": None,
                },
                protective_action="Keep broker submission blocked.",
            )
            raise AuthorityDeniedError(
                "Coordinator requires a fresh durable authorization from the operator path"
            )

        self.journal.append_event(
            EventType.ORDER_ATTEMPT,
            source="paper_execution_coordinator",
            passport_id=intent.passport_id,
            correlation_id=intent.client_order_id,
            payload=payload,
            protective_action="Reject unless paper-only execution gate remains armed.",
        )
        try:
            order = self.executor.submit_limit_order(intent)
        except Exception as exc:
            submission_unknown = _submission_is_unknown(exc)
            local_status = "SUBMIT_UNKNOWN" if submission_unknown else "SUBMISSION_FAILED"
            self.journal.update_broker_order(
                client_order_id=intent.client_order_id,
                broker_order_id=None,
                status=local_status,
                payload={
                    "intent": payload,
                    "submission_error_type": type(exc).__name__,
                    "reconciliation_required": True,
                    "blind_retry_allowed": False,
                },
            )
            self.journal.append_event(
                EventType.INCIDENT,
                source="paper_execution_coordinator",
                passport_id=intent.passport_id,
                correlation_id=intent.client_order_id,
                severity="CRITICAL",
                payload={
                    "reason_code": local_status,
                    "error_type": type(exc).__name__,
                    "reconciliation_required": True,
                    "blind_retry_allowed": False,
                },
                protective_action=(
                    "Treat submission as unknown; reconcile by client order ID before any retry."
                    if submission_unknown
                    else "Pause new orders and reconcile broker state before any new attempt."
                ),
            )
            raise

        quality = _execution_quality(intent=intent, order=order)
        local_status = (
            "EXIT_PENDING_RECONCILIATION"
            if intent.position_intent == "sell_to_close"
            else order.status
        )
        broker_payload: dict[str, object] = {
            "intent": payload,
            "broker_order": asdict(order),
            "reconciliation_required": intent.position_intent == "sell_to_close",
        }
        if quality is not None:
            broker_payload["execution_quality"] = quality
        self.journal.update_broker_order(
            client_order_id=intent.client_order_id,
            broker_order_id=order.broker_order_id,
            status=local_status,
            payload=broker_payload,
        )
        self.journal.append_event(
            EventType.BROKER_LIFECYCLE,
            source="alpaca",
            passport_id=intent.passport_id,
            correlation_id=intent.client_order_id,
            payload={
                "broker_order": asdict(order),
                "local_lifecycle_status": local_status,
                "execution_quality": quality,
            },
            protective_action=(
                "Reconcile broker position quantity to zero before marking the lifecycle closed."
                if intent.position_intent == "sell_to_close"
                else None
            ),
        )
        return order

    def _verify_no_broker_uncertainty(self) -> None:
        if not self.journal.has_broker_uncertainty():
            return
        message = "A prior broker submission is unresolved; reconciliation is required"
        _persist_critical_authority_incident(
            self.settings,
            self.journal,
            component="broker_reconciliation",
            message=message,
            protective_action=(
                "Do not submit another order until the durable client identity is reconciled."
            ),
        )
        raise ExecutionDisabledError(message)

    def _verify_entry_flat_and_reconciled(self) -> None:
        if self.journal.has_unverified_exit():
            message = "An exit lifecycle is not broker-flat verified; new entry is blocked"
            _persist_critical_authority_incident(
                self.settings,
                self.journal,
                component="position_lifecycle",
                message=message,
                protective_action="Reconcile positions and prove broker quantity is zero.",
            )
            raise ExecutionDisabledError(message)
        try:
            positions = self.broker.list_positions()
        except Exception as exc:
            message = "Broker positions are unknown; new entry requires reconciliation"
            _persist_critical_authority_incident(
                self.settings,
                self.journal,
                component="broker_reconciliation",
                message=message,
                protective_action="Reconcile broker positions before opening exposure.",
            )
            raise ExecutionDisabledError(message) from exc
        if any(position.quantity != 0 for position in positions):
            message = "One-position policy blocks a new entry while any broker position exists"
            _persist_critical_authority_incident(
                self.settings,
                self.journal,
                component="position_lifecycle",
                message=message,
                protective_action="Keep new entries paused until broker-flat state is verified.",
            )
            raise AuthorityDeniedError(message)
        self.journal.resolve_incidents("position_lifecycle")

    def _verify_existing_long_position(self, intent: OrderIntent) -> None:
        try:
            positions = self.broker.list_positions()
        except Exception as exc:
            message = (
                "Existing position could not be verified; broker reconciliation is required"
            )
            _persist_critical_authority_incident(
                self.settings,
                self.journal,
                component="broker_reconciliation",
                message=message,
                protective_action="Do not send a blind exit; reconcile broker state first.",
            )
            raise ExecutionDisabledError(message) from exc

        matches = [position for position in positions if position.symbol == intent.symbol]
        verified = (
            len(matches) == 1
            and matches[0].side.lower() == "long"
            and matches[0].quantity >= Decimal(intent.quantity)
        )
        if verified:
            self.journal.resolve_incidents("position_management_position")
            return

        message = (
            "Position-management authority requires a verified existing long position "
            "with sufficient quantity"
        )
        _persist_critical_authority_incident(
            self.settings,
            self.journal,
            component="position_management_position",
            message=message,
            protective_action="Block the exit intent and reconcile the actual broker position.",
        )
        raise AuthorityDeniedError(message)


class OperatorAuthorityPath:
    """The only application path from sealed evidence to broker coordination."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        coordinator: PaperExecutionCoordinator,
        health_state: Callable[[], HealthReport | HealthState],
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.coordinator = coordinator
        self.health_state = health_state

    def execute(self, *, passport_id: str, candidate: OrderCandidate) -> BrokerOrderSnapshot:
        passport_state = self.journal.passport_state(passport_id)
        if passport_state != "SEALED":
            reason = "PASSPORT_MISSING" if passport_state is None else "PASSPORT_UNSEALED"
            self._deny(
                passport_id=passport_id,
                passport_exists=passport_state is not None,
                verdict=None,
                authority=AuthorityGrant.NONE,
                reason_code=reason,
                error=AuthorityDeniedError(
                    "Execution intent requires an existing sealed Evidence Passport"
                ),
            )

        referee = self.journal.get_referee_result(passport_id)
        if referee is None:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=None,
                authority=AuthorityGrant.NONE,
                reason_code="REFEREE_RESULT_MISSING",
                error=InvalidRefereeResultError("A deterministic Referee result is required"),
            )

        try:
            verdict = RefereeVerdict(referee.verdict)
        except ValueError:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=referee.verdict,
                authority=AuthorityGrant.NONE,
                reason_code="REFEREE_VERDICT_INVALID",
                error=InvalidRefereeResultError(
                    f"Malformed Referee verdict: {referee.verdict!r}"
                ),
            )

        authority, quantity = self._resolve_authority(
            passport_id=passport_id,
            candidate=candidate,
            referee=referee,
            verdict=verdict,
        )

        try:
            self.settings.require_order_authority(candidate.position_intent)
        except ExecutionDisabledError as exc:
            reason_code = _authority_denial_code(
                exc,
                position_intent=candidate.position_intent,
            )
            if candidate.position_intent == "sell_to_close" or isinstance(
                exc, BrokerLockedError
            ):
                component = (
                    "broker_lock"
                    if isinstance(exc, BrokerLockedError)
                    else "position_management_authority"
                )
                _persist_critical_authority_incident(
                    self.settings,
                    self.journal,
                    component=component,
                    message=str(exc),
                    protective_action=(
                        "Do not submit until the owner clears the broker lock."
                        if isinstance(exc, BrokerLockedError)
                        else "Restore explicit position-management authority or close manually."
                    ),
                )
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=authority,
                reason_code=reason_code,
                error=exc,
            )

        current_health = self.health_state()
        health_decision = authority_health(
            current_health,
            position_intent=candidate.position_intent,
        )
        if not health_decision.allowed:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=authority,
                reason_code=health_decision.reason_code or "SYSTEM_HEALTH_UNKNOWN",
                error=ExecutionDisabledError(health_decision.message),
            )

        intent = OrderIntent(
            symbol=candidate.symbol,
            quantity=quantity,
            side=candidate.side,
            limit_price=candidate.limit_price,
            client_order_id=candidate.client_order_id,
            position_intent=candidate.position_intent,
            passport_id=passport_id,
            decision_bid=candidate.decision_bid,
            decision_ask=candidate.decision_ask,
            quote_at=candidate.quote_at,
        )
        authorization_payload = {
            "passport_id": passport_id,
            "referee_result_id": referee.result_id,
            "verdict": verdict.value,
            "authority_granted": authority.value,
            "intent": asdict(intent),
        }
        if not self.journal.authorize_order_attempt(
            client_order_id=intent.client_order_id,
            passport_id=passport_id,
            payload=authorization_payload,
        ):
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=AuthorityGrant.NONE,
                reason_code="DUPLICATE_CLIENT_ORDER_ID",
                error=DuplicateOrderIdentityError(
                    f"Client order identity already exists: {intent.client_order_id}"
                ),
            )

        try:
            result = self.coordinator.submit(intent)
        except Exception as exc:
            self._record_transition(
                passport_id=passport_id,
                database_passport_id=passport_id,
                verdict=verdict.value,
                authority=authority,
                execution_allowed=False,
                reason_code="COORDINATOR_OR_BROKER_REJECTED",
                mock_broker_result={"error": type(exc).__name__},
            )
            raise

        self._record_transition(
            passport_id=passport_id,
            database_passport_id=passport_id,
            verdict=verdict.value,
            authority=authority,
            execution_allowed=True,
            reason_code="AUTHORIZED_SUBMISSION",
            mock_broker_result=asdict(result),
        )
        return result

    def _resolve_authority(
        self,
        *,
        passport_id: str,
        candidate: OrderCandidate,
        referee: RefereeResult,
        verdict: RefereeVerdict,
    ) -> tuple[AuthorityGrant, int]:
        if verdict in {RefereeVerdict.ABSTAIN, RefereeVerdict.BLOCK}:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=AuthorityGrant.NONE,
                reason_code=f"VERDICT_{verdict.value}",
                error=AuthorityDeniedError(f"{verdict.value} grants no order authority"),
            )

        if referee.max_quantity is None or referee.max_limit_price is None:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=AuthorityGrant.NONE,
                reason_code="REFEREE_LIMITS_MISSING",
                error=InvalidRefereeResultError(
                    f"{verdict.value} requires quantity and premium limits"
                ),
            )
        if referee.max_quantity <= 0 or referee.max_limit_price <= 0:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=AuthorityGrant.NONE,
                reason_code="REFEREE_LIMITS_INVALID",
                error=InvalidRefereeResultError("Referee limits must be positive"),
            )
        if candidate.limit_price > referee.max_limit_price:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=AuthorityGrant.NONE,
                reason_code="PREMIUM_LIMIT_EXCEEDED",
                error=AuthorityDeniedError("Candidate premium exceeds Referee authority"),
            )

        if verdict is RefereeVerdict.APPROVE:
            if candidate.quantity > referee.max_quantity:
                self._deny(
                    passport_id=passport_id,
                    passport_exists=True,
                    verdict=verdict.value,
                    authority=AuthorityGrant.NONE,
                    reason_code="QUANTITY_LIMIT_EXCEEDED",
                    error=AuthorityDeniedError("Candidate quantity exceeds APPROVE authority"),
                )
            return AuthorityGrant.ENTRY_FULL, candidate.quantity

        if verdict is RefereeVerdict.REDUCE:
            if referee.max_quantity >= candidate.quantity:
                self._deny(
                    passport_id=passport_id,
                    passport_exists=True,
                    verdict=verdict.value,
                    authority=AuthorityGrant.NONE,
                    reason_code="REDUCTION_NOT_MATERIAL",
                    error=InvalidRefereeResultError(
                        "REDUCE must lower the requested candidate quantity"
                    ),
                )
            return AuthorityGrant.ENTRY_REDUCED, referee.max_quantity

        if verdict is RefereeVerdict.EXIT:
            if candidate.position_intent != "sell_to_close" or candidate.side != "sell":
                self._deny(
                    passport_id=passport_id,
                    passport_exists=True,
                    verdict=verdict.value,
                    authority=AuthorityGrant.NONE,
                    reason_code="EXIT_ENTRY_FORBIDDEN",
                    error=AuthorityDeniedError("EXIT permits position management only"),
                )
            if candidate.quantity > referee.max_quantity:
                self._deny(
                    passport_id=passport_id,
                    passport_exists=True,
                    verdict=verdict.value,
                    authority=AuthorityGrant.NONE,
                    reason_code="EXIT_QUANTITY_EXCEEDED",
                    error=AuthorityDeniedError("EXIT quantity exceeds Referee authority"),
                )
            return AuthorityGrant.POSITION_MANAGEMENT, candidate.quantity

        raise InvalidRefereeResultError(f"Unhandled Referee verdict: {verdict.value}")

    def _deny(
        self,
        *,
        passport_id: str,
        passport_exists: bool,
        verdict: str | None,
        authority: AuthorityGrant,
        reason_code: str,
        error: Exception,
    ) -> None:
        self._record_transition(
            passport_id=passport_id,
            database_passport_id=passport_id if passport_exists else None,
            verdict=verdict,
            authority=authority,
            execution_allowed=False,
            reason_code=reason_code,
            mock_broker_result=None,
        )
        raise error

    def _record_transition(
        self,
        *,
        passport_id: str,
        database_passport_id: str | None,
        verdict: str | None,
        authority: AuthorityGrant,
        execution_allowed: bool,
        reason_code: str,
        mock_broker_result: dict[str, object] | None,
    ) -> None:
        self.journal.append_event(
            EventType.AUTHORITY_TRANSITION,
            source="operator_authority_path",
            passport_id=database_passport_id,
            payload={
                "passport_id": passport_id,
                "verdict": verdict,
                "authority_granted": authority.value,
                "execution_allowed": execution_allowed,
                "reason_code": reason_code,
                "mock_broker_result": mock_broker_result,
            },
            severity="INFO" if execution_allowed else "WARNING",
            protective_action=(
                None if execution_allowed else "Keep broker submission blocked."
            ),
        )


class BrokerReconciler:
    def __init__(self, journal: Journal, broker: BrokerReader) -> None:
        self.journal = journal
        self.broker = broker

    def reconcile(self) -> ReconciliationReport:
        records = self.journal.broker_order_records()
        records_by_id = {str(record["client_order_id"]): record for record in records}
        local_ids = {
            client_order_id
            for client_order_id, record in records_by_id.items()
            if record["status"]
            not in {"ATTEMPT_RESERVED", "AUTHORITY_GRANTED", "SUBMISSION_FAILED"}
        }
        broker_orders = self.broker.list_orders()
        positions = self.broker.list_positions()
        broker_by_id = {
            order.client_order_id: order
            for order in broker_orders
            if order.client_order_id.startswith("cajnmnstr-")
        }
        broker_ids = {
            order.client_order_id for order in broker_by_id.values()
        }
        unverified_flat: list[str] = []
        for client_order_id, record in records_by_id.items():
            payload = record["payload"]
            intent = payload.get("intent", {})
            broker_order = broker_by_id.get(client_order_id)
            if broker_order is not None:
                quality = _execution_quality(intent=intent, order=broker_order)
                update: dict[str, object] = {"broker_order": asdict(broker_order)}
                if quality is not None:
                    update["execution_quality"] = quality
                is_exit = intent.get("position_intent") == "sell_to_close"
                reconciled_status = broker_order.status
                if is_exit:
                    reconciled_status = (
                        "CLOSED_BROKER_FLAT"
                        if record["status"] == "CLOSED_BROKER_FLAT"
                        else "EXIT_PENDING_RECONCILIATION"
                    )
                self.journal.update_broker_order(
                    client_order_id=client_order_id,
                    broker_order_id=broker_order.broker_order_id,
                    status=reconciled_status,
                    payload=update,
                )
                if quality is not None and json.dumps(
                    payload.get("execution_quality"), sort_keys=True, default=str
                ) != json.dumps(quality, sort_keys=True, default=str):
                    self.journal.append_event(
                        EventType.BROKER_LIFECYCLE,
                        source="broker_reconciler",
                        passport_id=str(record["passport_id"]),
                        correlation_id=client_order_id,
                        payload={
                            "broker_order": asdict(broker_order),
                            "execution_quality": quality,
                        },
                    )

            if (
                intent.get("position_intent") != "sell_to_close"
                or record["status"]
                not in {"EXIT_PENDING_RECONCILIATION", "SUBMIT_UNKNOWN"}
            ):
                continue
            symbol = str(intent.get("symbol", ""))
            remaining = sum(
                (
                    position.quantity
                    for position in positions
                    if position.symbol == symbol
                ),
                Decimal("0"),
            )
            component = f"position_lifecycle:{symbol}"
            if remaining == 0:
                self.journal.update_broker_order(
                    client_order_id=client_order_id,
                    broker_order_id=(
                        None if broker_order is None else broker_order.broker_order_id
                    ),
                    status="CLOSED_BROKER_FLAT",
                    payload={
                        "broker_flat_verified": True,
                        "verified_position_quantity": Decimal("0"),
                        "reconciliation_required": False,
                    },
                )
                self.journal.resolve_incidents(component)
                self.journal.append_event(
                    EventType.RECONCILIATION,
                    source="broker_reconciler",
                    passport_id=str(record["passport_id"]),
                    correlation_id=client_order_id,
                    payload={
                        "reason_code": "BROKER_FLAT_VERIFIED",
                        "symbol": symbol,
                        "position_quantity": Decimal("0"),
                        "lifecycle_state": "CLOSED_BROKER_FLAT",
                    },
                )
            else:
                unverified_flat.append(client_order_id)
                message = (
                    f"Exit lifecycle remains pending; broker position {symbol} quantity "
                    f"is {remaining}"
                )
                self.journal.open_incident(
                    component=component,
                    severity="CRITICAL",
                    state=HealthState.PAUSED.value,
                    message=message,
                    protective_action=(
                        "Keep new entries blocked and reconcile the pending close."
                    ),
                )
                self.journal.update_broker_order(
                    client_order_id=client_order_id,
                    broker_order_id=(
                        None if broker_order is None else broker_order.broker_order_id
                    ),
                    status="EXIT_PENDING_RECONCILIATION",
                    payload={
                        "broker_flat_verified": False,
                        "verified_position_quantity": remaining,
                        "reconciliation_required": True,
                    },
                )
        report = ReconciliationReport(
            checked_at=datetime.now(UTC),
            broker_order_count=len(broker_orders),
            broker_position_count=len(positions),
            unknown_broker_client_ids=tuple(sorted(broker_ids - local_ids)),
            missing_broker_client_ids=tuple(sorted(local_ids - broker_ids)),
            unverified_flat_client_ids=tuple(sorted(unverified_flat)),
        )
        self.journal.append_event(
            EventType.RECONCILIATION,
            source="broker_reconciler",
            severity="INFO" if report.matched else "CRITICAL",
            payload=asdict(report),
            protective_action=(
                None
                if report.matched
                else "Pause new orders until every local and broker identity is accounted for."
            ),
        )
        return report
