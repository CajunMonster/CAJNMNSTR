from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Settings
from .journal import Journal, write_emergency_incident
from .models import EventType, HealthState

ENTRY_CRITICAL_COMPONENTS = frozenset(
    {
        "configuration",
        "evidence_store",
        "alpaca",
        "broker_state",
        "broker_reconciliation",
        "market_session",
        "spy_quote",
        "option_quote",
        "risk_limits",
        "ai_provider",
        "news",
        "event_calendar",
    }
)

EXIT_CRITICAL_COMPONENTS = frozenset(
    {
        "configuration",
        "evidence_store",
        "alpaca",
        "broker_state",
        "broker_reconciliation",
        "market_session",
        "option_quote",
    }
)

NONCRITICAL_FOR_EXIT_COMPONENTS = frozenset(
    {
        "ai_provider",
        "spy_quote",
        "risk_limits",
        "news",
        "event_calendar",
    }
)


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component: str
    state: HealthState
    message: str
    protective_action: str
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["checked_at"] = self.checked_at.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class HealthReport:
    state: HealthState
    components: tuple[ComponentHealth, ...]
    checked_at: datetime
    entry_armed: bool
    position_management_armed: bool
    broker_lock_active: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": "CAJNMNSTR",
            "state": self.state.value,
            "checked_at": self.checked_at.isoformat(),
            "entry_armed": self.entry_armed,
            "position_management_armed": self.position_management_armed,
            "broker_lock_active": self.broker_lock_active,
            "components": [component.to_dict() for component in self.components],
        }


@dataclass(frozen=True, slots=True)
class AuthorityHealthDecision:
    allowed: bool
    state: HealthState
    blockers: tuple[str, ...]
    reason_code: str | None
    message: str


def authority_health(
    health: HealthReport | HealthState,
    *,
    position_intent: str,
) -> AuthorityHealthDecision:
    """Apply component-specific health without granting a generic exit bypass."""

    position_management = position_intent == "sell_to_close"
    if isinstance(health, HealthState):
        if health is HealthState.HEALTHY:
            return AuthorityHealthDecision(
                allowed=False,
                state=HealthState.PAUSED,
                blockers=("component_health_unavailable",),
                reason_code=(
                    "EXIT_HEALTH_DETAIL_REQUIRED"
                    if position_management
                    else "ENTRY_HEALTH_DETAIL_REQUIRED"
                ),
                message=(
                    "Component-level position-management health is required"
                    if position_management
                    else "Component-level entry health is required"
                ),
            )
        return AuthorityHealthDecision(
            allowed=False,
            state=health,
            blockers=("aggregate_health",),
            reason_code=f"SYSTEM_{health.value}",
            message=f"Execution requires HEALTHY authority; current state is {health.value}",
        )

    blockers = _component_blockers(
        health.components,
        position_management=position_management,
    )
    if not blockers:
        return AuthorityHealthDecision(
            allowed=True,
            state=HealthState.HEALTHY,
            blockers=(),
            reason_code=None,
            message=(
                "Position-management-critical health is available."
                if position_management
                else "Entry-critical health is available."
            ),
        )

    blocker_names = tuple(component.component for component in blockers)
    blocker_state = _aggregate(component.state for component in blockers)
    if not position_management:
        reason_code = f"SYSTEM_{blocker_state.value}"
        message = (
            "New entry requires all entry-critical health; blocked by "
            + ", ".join(blocker_names)
        )
    elif {"alpaca", "broker_state", "broker_reconciliation"} & set(blocker_names):
        reason_code = "EXIT_RECONCILIATION_REQUIRED"
        message = "Position exit requires known, reconciled Alpaca broker state"
    elif "market_session" in blocker_names:
        reason_code = "EXIT_PENDING_MARKET_SESSION"
        message = "Position exit remains pending until the market session is executable"
    elif "option_quote" in blocker_names:
        reason_code = "EXIT_PENDING_OPTION_QUOTE"
        message = "Position exit remains pending until an executable option quote is available"
    elif "evidence_store" in blocker_names:
        reason_code = "EXIT_EVIDENCE_STORE_UNAVAILABLE"
        message = "Position exit requires durable authority and emergency incident persistence"
    else:
        reason_code = "EXIT_HEALTH_BLOCKED"
        message = "Position exit is blocked by an unclassified critical health component"
    return AuthorityHealthDecision(
        allowed=False,
        state=blocker_state,
        blockers=blocker_names,
        reason_code=reason_code,
        message=message,
    )


def freshness_health(
    *,
    component: str,
    observed_at: datetime | None,
    maximum_age: timedelta,
    now: datetime | None = None,
) -> ComponentHealth:
    checked_at = now or datetime.now(UTC)
    protective_action = "Pause proposals and block order submission until fresh data is verified."
    if observed_at is None:
        return ComponentHealth(
            component,
            HealthState.PAUSED,
            "No observation timestamp is available.",
            protective_action,
            checked_at,
        )
    observed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    age = checked_at - observed.astimezone(UTC)
    if age < timedelta(0) or age > maximum_age:
        return ComponentHealth(
            component,
            HealthState.PAUSED,
            f"Data is stale or time-inconsistent (age {age.total_seconds():.1f}s).",
            protective_action,
            checked_at,
        )
    return ComponentHealth(
        component,
        HealthState.HEALTHY,
        f"Data age {age.total_seconds():.1f}s is within policy.",
        "No protective action required.",
        checked_at,
    )


class HealthSupervisor:
    def __init__(
        self,
        settings: Settings,
        *,
        alpaca_probe: Callable[[], None] | None = None,
        ai_probe: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.alpaca_probe = alpaca_probe
        self.ai_probe = ai_probe

    def evaluate(self) -> HealthReport:
        checked_at = datetime.now(UTC)
        components: list[ComponentHealth] = []
        components.append(
            ComponentHealth(
                "configuration",
                HealthState.HEALTHY,
                "Paper endpoint and environment invariants are valid.",
                "Execution remains gated unless every explicit paper-only condition passes.",
                checked_at,
            )
        )

        journal: Journal | None = None
        try:
            journal = Journal(self.settings.journal_path)
            journal.initialize()
            journal.probe()
            components.append(
                ComponentHealth(
                    "evidence_store",
                    HealthState.HEALTHY,
                    "Evidence store is writable and queryable.",
                    "No protective action required.",
                    checked_at,
                )
            )
        except Exception as exc:  # converted into a visible PAUSED state below
            component = ComponentHealth(
                "evidence_store",
                HealthState.PAUSED,
                str(exc),
                "Block proposals and orders; write an emergency local incident record.",
                checked_at,
            )
            components.append(component)
            write_emergency_incident(
                self.settings.emergency_incident_path,
                {
                    "component": component.component,
                    "state": component.state.value,
                    "message": component.message,
                    "protective_action": component.protective_action,
                },
            )

        components.append(self._alpaca_health(checked_at))
        components.append(self._ai_health(checked_at))
        state = _aggregate(component.state for component in components)
        entry_blockers = _component_blockers(components, position_management=False)
        exit_blockers = _component_blockers(components, position_management=True)
        report = HealthReport(
            state=state,
            components=tuple(components),
            checked_at=checked_at,
            entry_armed=self.settings.entry_armed and not entry_blockers,
            position_management_armed=(
                self.settings.position_management_armed and not exit_blockers
            ),
            broker_lock_active=self.settings.broker_lock,
        )

        if journal is not None:
            try:
                journal.append_event(
                    EventType.CONNECTION,
                    source="health_supervisor",
                    severity="INFO" if state is HealthState.HEALTHY else "WARNING",
                    payload=report.to_dict(),
                    protective_action=(
                        "Block new entries; position management follows its narrower "
                        "component-specific health policy."
                        if state is not HealthState.HEALTHY
                        else None
                    ),
                )
                for component in components:
                    if component.state is HealthState.HEALTHY:
                        journal.resolve_incidents(component.component)
                    else:
                        journal.open_incident(
                            component=component.component,
                            severity=(
                                "CRITICAL"
                                if component.state is HealthState.PAUSED
                                else "WARNING"
                            ),
                            state=component.state.value,
                            message=component.message,
                            protective_action=component.protective_action,
                        )
            except Exception as exc:
                write_emergency_incident(
                    self.settings.emergency_incident_path,
                    {
                        "component": "journal",
                        "state": HealthState.PAUSED.value,
                        "message": str(exc),
                        "protective_action": "Block proposals and orders.",
                    },
                )
        return report

    def _alpaca_health(self, checked_at: datetime) -> ComponentHealth:
        protective_action = "Keep execution disabled and do not treat broker state as known."
        if not self.settings.credentials_present:
            return ComponentHealth(
                "alpaca",
                HealthState.PAUSED,
                "Paper credentials are absent; Alpaca connectivity is not established.",
                protective_action,
                checked_at,
            )
        if self.alpaca_probe is None:
            return ComponentHealth(
                "alpaca",
                HealthState.DEGRADED,
                "Credentials are present but connectivity was not probed in this run.",
                protective_action,
                checked_at,
            )
        try:
            self.alpaca_probe()
        except Exception as exc:
            return ComponentHealth(
                "alpaca",
                HealthState.PAUSED,
                f"Alpaca connectivity failed: {exc}",
                protective_action,
                checked_at,
            )
        return ComponentHealth(
            "alpaca",
            HealthState.HEALTHY,
            "Paper account connectivity is healthy.",
            "No protective action required.",
            checked_at,
        )

    def _ai_health(self, checked_at: datetime) -> ComponentHealth:
        protective_action = "Do not generate a proposal; deterministic controls remain active."
        if not self.settings.ai_configured:
            return ComponentHealth(
                "ai_provider",
                HealthState.DEGRADED,
                "AI provider key or model is not configured.",
                protective_action,
                checked_at,
            )
        if self.ai_probe is None:
            return ComponentHealth(
                "ai_provider",
                HealthState.DEGRADED,
                "AI provider is configured but was not probed in this run.",
                protective_action,
                checked_at,
            )
        try:
            self.ai_probe()
        except Exception as exc:
            return ComponentHealth(
                "ai_provider",
                HealthState.DEGRADED,
                f"AI provider probe failed: {exc}",
                protective_action,
                checked_at,
            )
        return ComponentHealth(
            "ai_provider",
            HealthState.HEALTHY,
            "AI provider is reachable.",
            "No protective action required.",
            checked_at,
        )


def _aggregate(states: Any) -> HealthState:
    values = tuple(states)
    if HealthState.PAUSED in values:
        return HealthState.PAUSED
    if HealthState.DEGRADED in values:
        return HealthState.DEGRADED
    return HealthState.HEALTHY


def _component_blockers(
    components: list[ComponentHealth] | tuple[ComponentHealth, ...],
    *,
    position_management: bool,
) -> tuple[ComponentHealth, ...]:
    required = (
        EXIT_CRITICAL_COMPONENTS
        if position_management
        else ENTRY_CRITICAL_COMPONENTS
    )
    present = {component.component for component in components}
    checked_at = components[0].checked_at if components else datetime.now(UTC)
    missing = tuple(
        ComponentHealth(
            component=component,
            state=HealthState.PAUSED,
            message="Required health component was not evaluated.",
            protective_action="Fail closed until this health component is evaluated.",
            checked_at=checked_at,
        )
        for component in sorted(required - present)
    )
    nonhealthy = tuple(
        component for component in components if component.state is not HealthState.HEALTHY
    )
    if not position_management:
        return nonhealthy + missing
    return (
        tuple(
            component
            for component in nonhealthy
            if component.component not in NONCRITICAL_FOR_EXIT_COMPONENTS
        )
        + missing
    )
