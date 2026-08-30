from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Settings
from .journal import Journal, write_emergency_incident
from .models import EventType, HealthState


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
    execution_armed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": "CAJNMNSTR",
            "state": self.state.value,
            "checked_at": self.checked_at.isoformat(),
            "execution_armed": self.execution_armed,
            "components": [component.to_dict() for component in self.components],
        }


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
        report = HealthReport(
            state=state,
            components=tuple(components),
            checked_at=checked_at,
            execution_armed=self.settings.execution_armed and state is HealthState.HEALTHY,
        )

        if journal is not None:
            try:
                journal.append_event(
                    EventType.CONNECTION,
                    source="health_supervisor",
                    severity="INFO" if state is HealthState.HEALTHY else "WARNING",
                    payload=report.to_dict(),
                    protective_action=(
                        "Block paper execution while health is not HEALTHY."
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
