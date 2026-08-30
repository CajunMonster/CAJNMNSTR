from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

from .config import Settings
from .errors import (
    AuthorityDeniedError,
    DuplicateOrderIdentityError,
    ExecutionDisabledError,
    InvalidRefereeResultError,
)
from .journal import Journal
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
        health_state: Callable[[], HealthState],
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.broker = broker
        self.executor = executor
        self.health_state = health_state

    def submit(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        self.settings.require_execution_armed()
        current_health = self.health_state()
        if current_health is not HealthState.HEALTHY:
            raise ExecutionDisabledError(
                "Paper execution requires HEALTHY authority; "
                f"current state is {current_health.value}"
            )
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
            self.journal.update_broker_order(
                client_order_id=intent.client_order_id,
                broker_order_id=None,
                status="SUBMISSION_FAILED",
                payload={"error": str(exc)},
            )
            self.journal.append_event(
                EventType.INCIDENT,
                source="paper_execution_coordinator",
                passport_id=intent.passport_id,
                correlation_id=intent.client_order_id,
                severity="CRITICAL",
                payload={"message": str(exc)},
                protective_action="Pause new orders and reconcile broker state before retrying.",
            )
            raise

        self.journal.update_broker_order(
            client_order_id=intent.client_order_id,
            broker_order_id=order.broker_order_id,
            status=order.status,
            payload=asdict(order),
        )
        self.journal.append_event(
            EventType.BROKER_LIFECYCLE,
            source="alpaca",
            passport_id=intent.passport_id,
            correlation_id=intent.client_order_id,
            payload=asdict(order),
        )
        return order


class OperatorAuthorityPath:
    """The only application path from sealed evidence to broker coordination."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        coordinator: PaperExecutionCoordinator,
        health_state: Callable[[], HealthState],
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
            self.settings.require_execution_armed()
        except ExecutionDisabledError as exc:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=authority,
                reason_code="EXECUTION_DISABLED",
                error=exc,
            )

        current_health = self.health_state()
        if current_health is not HealthState.HEALTHY:
            self._deny(
                passport_id=passport_id,
                passport_exists=True,
                verdict=verdict.value,
                authority=authority,
                reason_code=f"SYSTEM_{current_health.value}",
                error=ExecutionDisabledError(
                    f"Authority path requires HEALTHY; current state is {current_health.value}"
                ),
            )

        intent = OrderIntent(
            symbol=candidate.symbol,
            quantity=quantity,
            side=candidate.side,
            limit_price=candidate.limit_price,
            client_order_id=candidate.client_order_id,
            position_intent=candidate.position_intent,
            passport_id=passport_id,
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
        local_ids = self.journal.local_client_order_ids()
        broker_orders = self.broker.list_orders()
        positions = self.broker.list_positions()
        broker_ids = {
            order.client_order_id
            for order in broker_orders
            if order.client_order_id.startswith("cajnmnstr-")
        }
        report = ReconciliationReport(
            checked_at=datetime.now(UTC),
            broker_order_count=len(broker_orders),
            broker_position_count=len(positions),
            unknown_broker_client_ids=tuple(sorted(broker_ids - local_ids)),
            missing_broker_client_ids=tuple(sorted(local_ids - broker_ids)),
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
