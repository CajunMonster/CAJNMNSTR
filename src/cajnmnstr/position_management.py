from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .config import PAPER_API_URL, Settings
from .health import ComponentHealth, HealthReport
from .journal import Journal
from .live_snapshot import LIVE_OPTION_MAX_AGE, LiveEvidenceCollection
from .models import (
    EventType,
    ExitReason,
    HealthState,
    OrderCandidate,
    PositionManagementPlan,
    PositionSnapshot,
    RefereeVerdict,
)
from .services import DeterministicReferee, OperatorAuthorityPath

POSITION_PLAN_CONFIRMATION = "OWNER_APPROVED_EXIT_PLAN"


@dataclass(frozen=True, slots=True)
class ExitEvaluation:
    reason: ExitReason | None
    option_bid: Decimal | None
    stop_bid: Decimal
    target_bid: Decimal | None
    invalidation_value: Decimal | None
    fill_confirmed_at: datetime
    time_stop_at: datetime
    evaluated_at: datetime


def _component(
    name: str,
    state: HealthState,
    message: str,
    checked_at: datetime,
) -> ComponentHealth:
    return ComponentHealth(
        component=name,
        state=state,
        message=message,
        protective_action=(
            "No protective action required."
            if state is HealthState.HEALTHY
            else "Preserve the position state and do not send a blind broker write."
        ),
        checked_at=checked_at,
    )


def _aggregate(states: list[HealthState]) -> HealthState:
    if HealthState.PAUSED in states:
        return HealthState.PAUSED
    if HealthState.DEGRADED in states:
        return HealthState.DEGRADED
    return HealthState.HEALTHY


def _compare(value: Decimal, comparison: str, threshold: Decimal) -> bool:
    return {
        "lt": value < threshold,
        "lte": value <= threshold,
        "gt": value > threshold,
        "gte": value >= threshold,
    }[comparison]


class DeterministicPositionManager:
    """Evaluate a durable plan and drive only the existing EXIT authority path."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        authority: OperatorAuthorityPath,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.authority = authority

    def run_cycle(self, collection: LiveEvidenceCollection) -> str:
        self.journal.initialize()
        active = self.journal.active_position_lifecycles()
        positions = tuple(position for position in collection.positions if position.quantity != 0)

        if not positions:
            return self._handle_flat(active)
        if len(positions) != 1:
            return self._critical(
                component="position_management_position",
                message="Position management requires exactly one verified broker position.",
                state="BROKER_POSITION_MISMATCH",
            )

        position = positions[0]
        lifecycle = self.journal.position_lifecycle(symbol=position.symbol)
        if lifecycle is None:
            return self._critical(
                component="position_management_plan",
                message=(
                    "A broker position exists without its immutable position-management plan."
                ),
                state="POSITION_PLAN_MISSING",
            )
        plan: PositionManagementPlan = lifecycle["plan"]
        if not self._valid_position(position, plan):
            return self._critical(
                component="position_management_position",
                message="Broker position does not match the durable long-option plan.",
                state="BROKER_POSITION_MISMATCH",
            )

        timing = self._fill_timing(
            plan,
            lifecycle,
            position,
            observed_at=collection.snapshot.decision_at,
        )
        if timing is None:
            return self._critical(
                component="position_fill_anchor",
                message=(
                    "A broker position exists but its entry fill timestamp is not durably "
                    "confirmed."
                ),
                state="POSITION_FILL_UNCONFIRMED",
            )
        fill_confirmed_at, time_stop_at = timing
        self.journal.resolve_incidents("position_fill_anchor")

        self.journal.update_position_lifecycle(
            plan_id=plan.plan_id,
            state=(
                "OPEN"
                if lifecycle["state"] in {"PLANNED", "OPEN"}
                else str(lifecycle["state"])
            ),
            broker_quantity=position.quantity,
            payload={
                "broker_position_verified": True,
                "confirmed_average_entry_price": str(position.average_entry_price),
                "confirmed_broker_quantity": str(position.quantity),
            },
        )
        self.journal.resolve_incidents("position_management_plan")
        self.journal.resolve_incidents("position_management_position")

        _, expected_exit_client_order_id = self._exit_identity(plan)
        recovered_order = self.journal.broker_order_record(expected_exit_client_order_id)
        exit_client_order_id = lifecycle["exit_client_order_id"]
        if exit_client_order_id is None and recovered_order is not None:
            recovered_status = str(recovered_order["status"])
            if recovered_status != "AUTHORITY_GRANTED":
                self.journal.update_position_lifecycle(
                    plan_id=plan.plan_id,
                    state=recovered_status,
                    broker_quantity=position.quantity,
                    exit_client_order_id=expected_exit_client_order_id,
                    payload={
                        "recovered_after_restart": True,
                        "broker_flat_verified": False,
                    },
                )
                return self._pending_exit(plan, position, recovered_status)
        if exit_client_order_id:
            order_status = self.journal.broker_order_status(str(exit_client_order_id))
            if order_status == "CLOSED_BROKER_FLAT":
                return self._critical(
                    component="position_lifecycle",
                    message=(
                        "Local exit is marked broker-flat while the broker still reports quantity."
                    ),
                    state="BROKER_POSITION_MISMATCH",
                )
            return self._pending_exit(plan, position, str(order_status or "UNKNOWN"))

        if collection.open_orders or not collection.reconciliation.matched:
            return self._critical(
                component="broker_reconciliation",
                message=(
                    "Open orders or unmatched broker state prevent a new deterministic exit write."
                ),
                state="BROKER_RECONCILIATION_REQUIRED",
            )
        if self.journal.has_broker_uncertainty():
            return self._critical(
                component="broker_reconciliation",
                message="An earlier broker write remains unresolved.",
                state="BROKER_RECONCILIATION_REQUIRED",
            )

        evaluation = self._evaluate(
            plan,
            position,
            collection,
            fill_confirmed_at=fill_confirmed_at,
            time_stop_at=time_stop_at,
        )
        invalidation_degraded = evaluation.invalidation_value is None
        if invalidation_degraded:
            self.journal.open_incident(
                component="thesis_invalidation_evidence",
                severity="WARNING",
                state=HealthState.DEGRADED.value,
                message=(
                    "The plan's deterministic thesis-invalidation feature is unavailable."
                ),
                protective_action=(
                    "Continue premium/time/EOD protection; do not claim thesis evaluation."
                ),
            )
        else:
            self.journal.resolve_incidents("thesis_invalidation_evidence")
        if evaluation.reason is None:
            self.journal.append_event(
                EventType.CONNECTION,
                source="position_management",
                severity="INFO",
                payload={
                    "plan_id": plan.plan_id,
                    "symbol": plan.symbol,
                    "state": "POSITION_MONITORING",
                    "broker_quantity": position.quantity,
                    "option_bid": evaluation.option_bid,
                    "stop_bid": evaluation.stop_bid,
                    "target_bid": evaluation.target_bid,
                    "invalidation_value": evaluation.invalidation_value,
                    "fill_confirmed_at": evaluation.fill_confirmed_at,
                    "time_stop_at": evaluation.time_stop_at,
                    "ai_dependency": False,
                    "thesis_invalidation_available": not invalidation_degraded,
                },
            )
            return (
                "POSITION_MONITORING_DEGRADED"
                if invalidation_degraded
                else "POSITION_MONITORING"
            )

        quote = next(
            (item for item in collection.option_chain if item.symbol == position.symbol),
            None,
        )
        quote_valid = (
            quote is not None
            and quote.bid_price is not None
            and quote.ask_price is not None
            and quote.bid_price.is_finite()
            and quote.ask_price.is_finite()
            and quote.bid_price > 0
            and quote.ask_price > quote.bid_price
            and quote.quote_at is not None
            and quote.quote_at.tzinfo is not None
            and evaluation.evaluated_at - quote.quote_at >= timedelta(0)
            and evaluation.evaluated_at - quote.quote_at <= LIVE_OPTION_MAX_AGE
        )
        if not quote_valid:
            self.journal.update_position_lifecycle(
                plan_id=plan.plan_id,
                state="EXIT_CONDITION_PENDING_QUOTE",
                broker_quantity=position.quantity,
                payload={"exit_reason": evaluation.reason.value},
            )
            return self._critical(
                component="option_quote",
                message="Exit condition is active but no fresh executable option quote exists.",
                state="EXIT_PENDING_OPTION_QUOTE",
            )
        if not collection.clock.is_open:
            self.journal.update_position_lifecycle(
                plan_id=plan.plan_id,
                state="EXIT_CONDITION_PENDING_MARKET",
                broker_quantity=position.quantity,
                payload={"exit_reason": evaluation.reason.value},
            )
            return self._critical(
                component="market_session",
                message="Exit condition is active while the market is not executable.",
                state="EXIT_PENDING_MARKET_SESSION",
            )

        health = self._exit_health(collection, quote.quote_at)
        passport_id, client_order_id = self._exit_identity(plan)
        self._ensure_exit_authority(
            plan=plan,
            position=position,
            evaluation=evaluation,
            passport_id=passport_id,
            client_order_id=client_order_id,
            bid=quote.bid_price,
            ask=quote.ask_price,
            quote_at=quote.quote_at,
        )
        candidate = OrderCandidate(
            symbol=position.symbol,
            quantity=int(position.quantity),
            side="sell",
            limit_price=quote.bid_price,
            client_order_id=client_order_id,
            position_intent="sell_to_close",
            decision_bid=quote.bid_price,
            decision_ask=quote.ask_price,
            quote_at=quote.quote_at,
        )

        original_health = self.authority.health_state
        coordinator_health = self.authority.coordinator.health_state
        self.authority.health_state = lambda: health
        self.authority.coordinator.health_state = lambda: health
        try:
            order = self.authority.execute(
                passport_id=passport_id,
                candidate=candidate,
                resume_authorized=True,
            )
        except Exception:
            status = self.journal.broker_order_status(client_order_id)
            if status in {"SUBMIT_UNKNOWN", "SUBMISSION_FAILED"}:
                self.journal.update_position_lifecycle(
                    plan_id=plan.plan_id,
                    state=status,
                    broker_quantity=position.quantity,
                    exit_client_order_id=client_order_id,
                    payload={
                        "exit_reason": evaluation.reason.value,
                        "reconciliation_required": True,
                        "blind_retry_allowed": False,
                    },
                )
                return (
                    "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
                    if status == "SUBMIT_UNKNOWN"
                    else "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
                )
            raise
        finally:
            self.authority.health_state = original_health
            self.authority.coordinator.health_state = coordinator_health

        self.journal.update_position_lifecycle(
            plan_id=plan.plan_id,
            state="EXIT_PENDING_RECONCILIATION",
            broker_quantity=position.quantity,
            exit_client_order_id=client_order_id,
            payload={
                "exit_reason": evaluation.reason.value,
                "broker_order_id_present": bool(order.broker_order_id),
                "submission_status": order.status,
                "broker_flat_verified": False,
            },
        )
        return "EXIT_PENDING_RECONCILIATION"

    def _handle_flat(self, active: list[dict[str, object]]) -> str:
        unresolved = False
        for lifecycle in active:
            plan: PositionManagementPlan = lifecycle["plan"]  # type: ignore[assignment]
            exit_id = lifecycle["exit_client_order_id"]
            status = None if not exit_id else self.journal.broker_order_status(str(exit_id))
            if status == "CLOSED_BROKER_FLAT":
                self.journal.update_position_lifecycle(
                    plan_id=plan.plan_id,
                    state="CLOSED_BROKER_FLAT",
                    broker_quantity=Decimal("0"),
                    payload={
                        "broker_flat_verified": True,
                        "closed_at": datetime.now(UTC).isoformat(),
                    },
                )
                self.journal.resolve_incidents(f"position_lifecycle:{plan.symbol}")
            elif lifecycle["state"] not in {"PLANNED"}:
                unresolved = True
                self._critical(
                    component=f"position_lifecycle:{plan.symbol}",
                    message=(
                        "Broker is flat but the durable lifecycle has not yet reconciled closed."
                    ),
                    state="EXIT_PENDING_RECONCILIATION",
                )
        return "EXIT_PENDING_RECONCILIATION" if unresolved else "FLAT"

    @staticmethod
    def _valid_position(position: PositionSnapshot, plan: PositionManagementPlan) -> bool:
        return (
            position.symbol == plan.symbol
            and position.side.lower() == "long"
            and position.quantity > 0
            and position.quantity == position.quantity.to_integral_value()
            and position.quantity <= plan.maximum_quantity
            and position.average_entry_price.is_finite()
            and position.average_entry_price > 0
        )

    def _evaluate(
        self,
        plan: PositionManagementPlan,
        position: PositionSnapshot,
        collection: LiveEvidenceCollection,
        *,
        fill_confirmed_at: datetime,
        time_stop_at: datetime,
    ) -> ExitEvaluation:
        evaluated_at = collection.snapshot.decision_at.astimezone(UTC)
        quote = next(
            (item for item in collection.option_chain if item.symbol == position.symbol),
            None,
        )
        option_bid = None if quote is None else quote.bid_price
        if option_bid is not None and not option_bid.is_finite():
            option_bid = None
        stop_bid = position.average_entry_price * (Decimal("1") - plan.stop_loss_fraction)
        target_bid = (
            None
            if plan.profit_target_fraction is None
            else position.average_entry_price
            * (Decimal("1") + plan.profit_target_fraction)
        )
        raw_invalidation = collection.snapshot.features.get(plan.invalidation.feature_name)
        try:
            invalidation_value = (
                None if raw_invalidation is None else Decimal(str(raw_invalidation))
            )
            if invalidation_value is not None and not invalidation_value.is_finite():
                invalidation_value = None
        except (InvalidOperation, ValueError):
            invalidation_value = None

        reason = None
        if evaluated_at >= plan.forced_eod_at.astimezone(UTC):
            reason = ExitReason.FORCED_EOD
        elif option_bid is not None and option_bid <= stop_bid:
            reason = ExitReason.RISK_STOP
        elif invalidation_value is not None and _compare(
            invalidation_value,
            plan.invalidation.comparison,
            plan.invalidation.threshold,
        ):
            reason = ExitReason.THESIS_INVALIDATION
        elif target_bid is not None and option_bid is not None and option_bid >= target_bid:
            reason = ExitReason.PROFIT_TARGET
        elif evaluated_at >= time_stop_at.astimezone(UTC):
            reason = ExitReason.TIME_STOP
        return ExitEvaluation(
            reason=reason,
            option_bid=option_bid,
            stop_bid=stop_bid,
            target_bid=target_bid,
            invalidation_value=invalidation_value,
            fill_confirmed_at=fill_confirmed_at,
            time_stop_at=time_stop_at,
            evaluated_at=evaluated_at,
        )

    def _fill_timing(
        self,
        plan: PositionManagementPlan,
        lifecycle: dict[str, object],
        position: PositionSnapshot,
        *,
        observed_at: datetime,
    ) -> tuple[datetime, datetime] | None:
        if observed_at.tzinfo is None:
            return None
        checked_at = observed_at.astimezone(UTC)
        durable = lifecycle["lifecycle"]
        if not isinstance(durable, dict):
            return None
        confirmed_raw = durable.get("fill_confirmed_at")
        stop_raw = durable.get("time_stop_at")
        if confirmed_raw is not None or stop_raw is not None:
            if confirmed_raw is None or stop_raw is None:
                return None
            try:
                confirmed_value = datetime.fromisoformat(str(confirmed_raw))
                stop_value = datetime.fromisoformat(str(stop_raw))
            except ValueError:
                return None
            if confirmed_value.tzinfo is None or stop_value.tzinfo is None:
                return None
            confirmed = confirmed_value.astimezone(UTC)
            stop_at = stop_value.astimezone(UTC)
            expected_stop = confirmed + timedelta(minutes=plan.time_stop_duration_minutes)
            if stop_at != expected_stop:
                return None
            return confirmed, stop_at

        fills: list[tuple[datetime, Decimal, str]] = []
        for record in self.journal.broker_order_records():
            if str(record["passport_id"]) != plan.entry_passport_id:
                continue
            payload = record["payload"]
            intent = payload.get("intent", {})
            broker_order = payload.get("broker_order", {})
            if (
                not isinstance(intent, dict)
                or not isinstance(broker_order, dict)
                or intent.get("position_intent") != "buy_to_open"
                or str(intent.get("symbol", "")) != plan.symbol
            ):
                continue
            try:
                filled_quantity = Decimal(str(broker_order["filled_quantity"]))
                observed = broker_order.get("filled_at") or broker_order["updated_at"]
                fill_value = datetime.fromisoformat(str(observed))
            except (KeyError, InvalidOperation, ValueError):
                continue
            if fill_value.tzinfo is None:
                continue
            fill_at = fill_value.astimezone(UTC)
            if fill_at > checked_at:
                continue
            if filled_quantity > 0:
                fills.append((fill_at, filled_quantity, str(record["client_order_id"])))
        if len(fills) != 1:
            return None
        fill_at, filled_quantity, client_order_id = fills[0]
        return self.journal.bind_position_fill(
            plan=plan,
            fill_confirmed_at=fill_at,
            filled_quantity=filled_quantity,
            average_entry_price=position.average_entry_price,
            anchor_source=f"ALPACA_ORDER:{client_order_id}",
        )

    def _exit_health(self, collection: LiveEvidenceCollection, quote_at: datetime) -> HealthReport:
        checked_at = collection.snapshot.decision_at.astimezone(UTC)
        quote_age = checked_at - quote_at
        states = {
            "configuration": (
                HealthState.HEALTHY
                if self.settings.paper_mode
                and self.settings.alpaca_api_base_url == PAPER_API_URL
                and self.settings.position_management_armed
                and not self.settings.broker_lock
                else HealthState.PAUSED
            ),
            "evidence_store": HealthState.HEALTHY,
            "alpaca": HealthState.HEALTHY,
            "broker_state": HealthState.HEALTHY,
            "broker_reconciliation": (
                HealthState.HEALTHY
                if collection.reconciliation.matched
                else HealthState.PAUSED
            ),
            "market_session": (
                HealthState.HEALTHY if collection.clock.is_open else HealthState.PAUSED
            ),
            "option_quote": (
                HealthState.HEALTHY
                if timedelta(0) <= quote_age <= LIVE_OPTION_MAX_AGE
                else HealthState.PAUSED
            ),
            "ai_provider": HealthState.DEGRADED,
            "spy_quote": HealthState.DEGRADED,
            "risk_limits": HealthState.HEALTHY,
            "news": HealthState.DEGRADED,
            "event_calendar": HealthState.DEGRADED,
        }
        components = tuple(
            _component(name, state, f"Position-management {name}: {state.value}.", checked_at)
            for name, state in states.items()
        )
        return HealthReport(
            state=_aggregate(list(states.values())),
            components=components,
            checked_at=checked_at,
            entry_armed=False,
            position_management_armed=(
                self.settings.position_management_armed
                and all(
                    states[name] is HealthState.HEALTHY
                    for name in {
                        "configuration",
                        "evidence_store",
                        "alpaca",
                        "broker_state",
                        "broker_reconciliation",
                        "market_session",
                        "option_quote",
                    }
                )
            ),
            broker_lock_active=self.settings.broker_lock,
        )

    def _ensure_exit_authority(
        self,
        *,
        plan: PositionManagementPlan,
        position: PositionSnapshot,
        evaluation: ExitEvaluation,
        passport_id: str,
        client_order_id: str,
        bid: Decimal,
        ask: Decimal,
        quote_at: datetime,
    ) -> None:
        payload = {
            "passport_type": "DETERMINISTIC_POSITION_EXIT",
            "entry_passport_id": plan.entry_passport_id,
            "position_plan_id": plan.plan_id,
            "symbol": position.symbol,
            "verified_quantity": position.quantity,
            "exit_reason": evaluation.reason.value if evaluation.reason else None,
            "evaluated_at": evaluation.evaluated_at,
            "decision_bid": bid,
            "decision_ask": ask,
            "quote_at": quote_at,
            "stop_bid": evaluation.stop_bid,
            "target_bid": evaluation.target_bid,
            "invalidation_value": evaluation.invalidation_value,
            "fill_confirmed_at": evaluation.fill_confirmed_at,
            "time_stop_at": evaluation.time_stop_at,
            "client_order_id": client_order_id,
            "ai_dependency": False,
            "broker_flat_required": True,
        }
        state = self.journal.passport_state(passport_id)
        if state is None:
            self.journal.create_passport(passport_id, payload)
            self.journal.seal_passport(passport_id, payload)
        elif state != "SEALED":
            raise ValueError("Existing deterministic exit Passport is not sealed")
        if self.journal.get_referee_result(passport_id) is None:
            DeterministicReferee(self.journal).issue(
                passport_id=passport_id,
                verdict=RefereeVerdict.EXIT,
                reason_code=evaluation.reason.value if evaluation.reason else "EXIT",
                max_quantity=int(position.quantity),
                max_limit_price=bid,
            )

    @staticmethod
    def _exit_identity(plan: PositionManagementPlan) -> tuple[str, str]:
        suffix = hashlib.sha256(plan.plan_id.encode("utf-8")).hexdigest()[:20]
        return f"exit-{suffix}", f"cajnmnstr-exit-{suffix}"

    def _pending_exit(
        self,
        plan: PositionManagementPlan,
        position: PositionSnapshot,
        order_status: str,
    ) -> str:
        self.journal.update_position_lifecycle(
            plan_id=plan.plan_id,
            state=(
                "SUBMIT_UNKNOWN"
                if order_status == "SUBMIT_UNKNOWN"
                else "EXIT_PENDING_RECONCILIATION"
            ),
            broker_quantity=position.quantity,
            payload={
                "last_order_status": order_status,
                "broker_flat_verified": False,
                "blind_retry_allowed": False,
            },
        )
        return (
            "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
            if order_status == "SUBMIT_UNKNOWN"
            else (
                "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
                if order_status == "SUBMISSION_FAILED"
                else "EXIT_PENDING_RECONCILIATION"
            )
        )

    def _critical(self, *, component: str, message: str, state: str) -> str:
        self.journal.open_incident(
            component=component,
            severity="CRITICAL",
            state=HealthState.PAUSED.value,
            message=message,
            protective_action="Block new entries and require reconciled deterministic recovery.",
        )
        self.journal.append_event(
            EventType.INCIDENT,
            source="position_management",
            severity="CRITICAL",
            payload={
                "component": component,
                "state": state,
                "message": message,
                "entry_allowed": False,
            },
            protective_action="Do not send a blind broker write.",
        )
        return state
