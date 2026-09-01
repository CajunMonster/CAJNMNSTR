from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .journal import Journal
from .models import EventType, MarketClockSnapshot
from .position_policy import FORCED_EOD_TIME

NEW_YORK = ZoneInfo("America/New_York")


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def lifecycle_session_date(lifecycle: dict[str, Any]) -> date | None:
    filled_at = _datetime(lifecycle["lifecycle"].get("fill_confirmed_at"))
    return None if filled_at is None else filled_at.astimezone(NEW_YORK).date()


def reconciled_realized_pnl(
    journal: Journal,
    lifecycle: dict[str, Any],
) -> Decimal | None:
    """Return broker-reconciled PAPER P&L for one verified-flat lifecycle."""

    if lifecycle.get("state") != "CLOSED_BROKER_FLAT":
        return None
    details = lifecycle["lifecycle"]
    if details.get("broker_flat_verified") is not True:
        return None
    exit_id = lifecycle.get("exit_client_order_id")
    if not exit_id:
        return None
    order = journal.broker_order_record(str(exit_id))
    if order is None or order.get("status") != "CLOSED_BROKER_FLAT":
        return None
    quality = order["payload"].get("execution_quality")
    if not isinstance(quality, dict):
        return None
    entry_price = _decimal(details.get("initial_confirmed_average_entry_price"))
    entry_quantity = _decimal(details.get("initial_confirmed_quantity"))
    exit_price = _decimal(quality.get("actual_fill_price"))
    exit_quantity = _decimal(quality.get("filled_quantity"))
    if (
        entry_price is None
        or entry_quantity is None
        or exit_price is None
        or exit_quantity is None
        or entry_price <= 0
        or entry_quantity <= 0
        or exit_price < 0
        or exit_quantity < entry_quantity
    ):
        return None
    return (exit_price - entry_price) * entry_quantity * Decimal("100")


@dataclass(frozen=True, slots=True)
class SessionRiskSnapshot:
    session_date: str | None
    status: str
    realized_pnl: Decimal | None
    loss_limit: Decimal | None
    loss_remaining: Decimal | None
    completed_lifecycles: int
    lifecycle_ids: tuple[str, ...]
    entry_allowed: bool
    reason_code: str
    detail: str
    evaluated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "status": self.status,
            "realized_pnl": None if self.realized_pnl is None else str(self.realized_pnl),
            "loss_limit": None if self.loss_limit is None else str(self.loss_limit),
            "loss_remaining": (
                None if self.loss_remaining is None else str(self.loss_remaining)
            ),
            "completed_lifecycles": self.completed_lifecycles,
            "lifecycle_ids": list(self.lifecycle_ids),
            "entry_allowed": self.entry_allowed,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class SessionRiskAuthority:
    """Durable, session-scoped entry authority derived only from verified-flat PAPER trades."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self._now = now or (lambda: datetime.now(UTC))

    def evaluate(self, clock: MarketClockSnapshot) -> SessionRiskSnapshot:
        now = self._now().astimezone(UTC)
        self.journal.initialize()
        latest = self.journal.latest_session_risk_state()
        session_date = self._session_date(clock, latest)
        limit = self.settings.session_loss_limit_usd
        if session_date is None:
            return SessionRiskSnapshot(
                session_date=None,
                status="NO_ACTIVE_SESSION",
                realized_pnl=None,
                loss_limit=limit,
                loss_remaining=None,
                completed_lifecycles=0,
                lifecycle_ids=(),
                entry_allowed=False,
                reason_code="SESSION_NOT_ESTABLISHED",
                detail="No Alpaca regular trading session is active or durably established.",
                evaluated_at=now,
            )
        if limit is None:
            return self._persist(
                SessionRiskSnapshot(
                    session_date=session_date,
                    status="UNCONFIGURED",
                    realized_pnl=None,
                    loss_limit=None,
                    loss_remaining=None,
                    completed_lifecycles=0,
                    lifecycle_ids=(),
                    entry_allowed=False,
                    reason_code="SESSION_LOSS_LIMIT_UNCONFIGURED",
                    detail="Owner session-loss threshold is not configured.",
                    evaluated_at=now,
                )
            )

        existing = self.journal.session_risk_state(session_date)
        existing_limit = None if existing is None else _decimal(existing.get("loss_limit"))
        if existing_limit is not None and existing_limit != limit:
            return self._persist(
                SessionRiskSnapshot(
                    session_date=session_date,
                    status="UNKNOWN",
                    realized_pnl=None,
                    loss_limit=existing_limit,
                    loss_remaining=None,
                    completed_lifecycles=0,
                    lifecycle_ids=(),
                    entry_allowed=False,
                    reason_code="SESSION_LOSS_LIMIT_CHANGED_MID_SESSION",
                    detail="The configured threshold changed after this session was established.",
                    evaluated_at=now,
                )
            )

        try:
            realized, lifecycle_ids = self._realized_for_session(date.fromisoformat(session_date))
        except ValueError as exc:
            return self._persist(
                SessionRiskSnapshot(
                    session_date=session_date,
                    status="UNKNOWN",
                    realized_pnl=None,
                    loss_limit=limit,
                    loss_remaining=None,
                    completed_lifecycles=0,
                    lifecycle_ids=(),
                    entry_allowed=False,
                    reason_code="SESSION_REALIZED_PNL_UNESTABLISHED",
                    detail=str(exc),
                    evaluated_at=now,
                )
            )

        locked = realized <= -limit
        entry_window_closed = (
            clock.is_open
            and clock.timestamp.astimezone(NEW_YORK).timetz().replace(tzinfo=None)
            >= FORCED_EOD_TIME
        )
        remaining = max(Decimal("0"), limit + realized)
        status = "LOCKED" if locked else "ENTRY_WINDOW_CLOSED" if entry_window_closed else "READY"
        reason_code = (
            "SESSION_LOSS_LIMIT_REACHED"
            if locked
            else "SESSION_ENTRY_WINDOW_CLOSED"
            if entry_window_closed
            else "SESSION_RISK_READY"
        )
        detail = (
            "The reconciled realized session loss reached the owner threshold."
            if locked
            else "The approved 3:35 PM ET forced-flatten boundary blocks new entries."
            if entry_window_closed
            else "Reconciled realized session P&L remains within owner authority."
        )
        return self._persist(
            SessionRiskSnapshot(
                session_date=session_date,
                status=status,
                realized_pnl=realized,
                loss_limit=limit,
                loss_remaining=remaining,
                completed_lifecycles=len(lifecycle_ids),
                lifecycle_ids=tuple(lifecycle_ids),
                entry_allowed=not locked and not entry_window_closed,
                reason_code=reason_code,
                detail=detail,
                evaluated_at=now,
            )
        )

    @staticmethod
    def _session_date(
        clock: MarketClockSnapshot,
        latest: dict[str, Any] | None,
    ) -> str | None:
        if clock.is_open:
            return clock.timestamp.astimezone(NEW_YORK).date().isoformat()
        if latest is not None:
            return str(latest["session_date"])
        return None

    def _realized_for_session(self, session_date: date) -> tuple[Decimal, list[str]]:
        total = Decimal("0")
        identifiers: list[str] = []
        for lifecycle in self.journal.all_position_lifecycles():
            if lifecycle["state"] != "CLOSED_BROKER_FLAT":
                continue
            lifecycle_date = lifecycle_session_date(lifecycle)
            if lifecycle_date is None:
                created_at = _datetime(lifecycle.get("created_at"))
                if (
                    created_at is not None
                    and created_at.astimezone(NEW_YORK).date() == session_date
                ):
                    raise ValueError(
                        "A completed current-session lifecycle lacks its confirmed fill timestamp."
                    )
                continue
            if lifecycle_date != session_date:
                continue
            pnl = reconciled_realized_pnl(self.journal, lifecycle)
            if pnl is None:
                raise ValueError(
                    "A completed current-session lifecycle lacks reconciled entry/exit "
                    "fill evidence."
                )
            total += pnl
            identifiers.append(str(lifecycle["plan_id"]))
        return total, sorted(identifiers)

    def _persist(self, snapshot: SessionRiskSnapshot) -> SessionRiskSnapshot:
        payload = snapshot.to_dict()
        changed = self.journal.save_session_risk_state(payload)
        component = "session_risk"
        incident_required = snapshot.status in {"LOCKED", "UNKNOWN", "UNCONFIGURED"}
        if not incident_required:
            self.journal.resolve_incidents(component)
        else:
            self.journal.open_incident(
                component=component,
                severity="CRITICAL",
                state="PAUSED",
                message=snapshot.detail,
                protective_action=(
                    "Block new entry only; preserve deterministic management of any "
                    "existing position."
                ),
            )
        if changed:
            self.journal.append_event(
                EventType.AUTHORITY_TRANSITION,
                source="session_risk_authority",
                severity="CRITICAL" if incident_required else "INFO",
                payload={
                    **payload,
                    "entry_submission_allowed": snapshot.entry_allowed,
                    "exit_authority_preserved": True,
                },
                protective_action=(
                    None
                    if snapshot.status == "READY"
                    else "Block new entry; do not block deterministic position exits."
                ),
            )
        return snapshot
