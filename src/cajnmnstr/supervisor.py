from __future__ import annotations

import json
import subprocess
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .config import Settings
from .decision_cycle import MAX_SPREAD_RATIO
from .journal import Journal
from .live_snapshot import LiveDecisionOutcome, LiveEvidenceCollection, write_dashboard_state
from .models import EventType
from .session_risk import (
    SessionRiskAuthority,
    SessionRiskSnapshot,
    reconciled_realized_pnl,
)

NEW_YORK = ZoneInfo("America/New_York")
CHECKPOINT_INTERVAL = timedelta(hours=1)
SUPERVISOR_STATE_VERSION = 2


class RecoveryActions(Protocol):
    """Narrow operational actions; no broker mutation or strategy authority."""

    def restart_dashboard(self) -> bool: ...

    def dashboard_healthy(self) -> bool: ...


class NoopRecoveryActions:
    def restart_dashboard(self) -> bool:
        return False

    def dashboard_healthy(self) -> bool:
        return True


class LocalDashboardRecoveryActions:
    """Restart only the local presentation process through its existing safe launcher."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.launcher = self.project_root / "launcher" / "Start-CAJNMNSTR.ps1"

    def dashboard_healthy(self) -> bool:
        try:
            with urlopen("http://127.0.0.1:8841/health.json", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError):
            return False
        return (
            payload.get("app") == "CAJNMNSTR"
            and payload.get("broker_submission_allowed") is False
        )

    def restart_dashboard(self) -> bool:
        if not self.launcher.is_file():
            return False
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.launcher),
                    "-NoOpen",
                ],
                cwd=self.project_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and self.dashboard_healthy()


@dataclass(frozen=True, slots=True)
class SupervisorAlert:
    code: str
    severity: str
    detail: str
    protective_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "protective_action": self.protective_action,
        }


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mean(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal("0")) / Decimal(len(values))


def _safe_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


class CompetitionSupervisor:
    """Deterministic checkpoint layer around the existing live runtime.

    It observes authoritative runtime state, persists progress, pauses eligibility through
    existing fail-closed conditions, and never calls an LLM or broker mutation method.
    """

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        *,
        cadence_seconds: int,
        now: Callable[[], datetime] | None = None,
        recovery: RecoveryActions | None = None,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.cadence_seconds = cadence_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self.recovery = recovery or NoopRecoveryActions()
        self.session_risk = SessionRiskAuthority(settings, journal, now=self._now)
        self._state: dict[str, Any] | None = None
        self._last_summary: dict[str, Any] | None = None

    def observe_cycle(
        self,
        collection: LiveEvidenceCollection,
        *,
        decision_epoch: str | None,
        loop_state: str,
        outcome: LiveDecisionOutcome | None,
        dashboard_path: Path | None,
        position_manager_attached: bool,
    ) -> dict[str, Any]:
        now = self._now().astimezone(UTC)
        self.journal.initialize()
        state = self._load_state(now)
        previous_alerts = set(state.get("active_alerts", []))
        alerts = self._detect(
            collection,
            now=now,
            decision_epoch=decision_epoch,
            loop_state=loop_state,
            position_manager_attached=position_manager_attached,
            dashboard_path=dashboard_path,
        )
        active_alerts = {item.code for item in alerts}
        recovered = sorted(previous_alerts - active_alerts)
        for alert in alerts:
            self.journal.open_incident(
                component=f"competition_supervisor:{alert.code.lower()}",
                severity=alert.severity,
                state="PAUSED" if alert.severity == "CRITICAL" else "DEGRADED",
                message=alert.detail,
                protective_action=alert.protective_action,
            )
        for code in recovered:
            component = f"competition_supervisor:{code.lower()}"
            self.journal.resolve_incidents(component)
            self.journal.append_event(
                EventType.SUPERVISOR_RECOVERY,
                source="competition_supervisor",
                payload={
                    "condition": code,
                    "verified_at": now.isoformat(),
                    "entry_submission_allowed": False,
                },
                protective_action="Resume monitoring only after the current cycle verified truth.",
            )
        if recovered:
            self.journal.resolve_incidents("competition_supervisor:runtime_cycle")

        equity = _as_decimal(collection.account.equity)
        peak = _as_decimal(state.get("peak_equity"))
        peak = equity if peak is None else max(peak, equity or peak)
        deployed = sum(
            (abs(_as_decimal(item.market_value) or Decimal("0")) for item in collection.positions),
            Decimal("0"),
        )
        state["equity_observation_count"] = int(state.get("equity_observation_count", 0)) + 1
        state["capital_observation_total"] = str(
            (_as_decimal(state.get("capital_observation_total")) or Decimal("0")) + deployed
        )
        state["peak_equity"] = None if peak is None else str(peak)
        state["last_cycle_at"] = now.isoformat()
        state["active_alerts"] = sorted(active_alerts)
        state["recovery_count"] = int(state.get("recovery_count", 0)) + len(recovered)
        state["last_loop_state"] = loop_state
        if outcome is not None and decision_epoch is not None:
            decided = list(state.get("evaluated_decision_epochs", []))
            if decision_epoch not in decided:
                decided.append(decision_epoch)
            state["evaluated_decision_epochs"] = decided[-256:]

        epoch_advanced = decision_epoch is not None and decision_epoch != state.get("last_epoch")
        if epoch_advanced:
            state["last_epoch"] = decision_epoch
            state["last_epoch_advanced_at"] = now.isoformat()

        session_risk = self.session_risk.evaluate(collection.clock)
        metrics = self._metrics(collection, state, now, session_risk)
        warnings = self._warnings(metrics)
        checkpoint_types = self._checkpoint_types(
            collection,
            state=state,
            now=now,
            outcome=outcome,
            alerts=alerts,
            recovered=recovered,
        )
        summary = self._summary(
            collection,
            now=now,
            decision_epoch=decision_epoch,
            loop_state=loop_state,
            alerts=alerts,
            recovered=recovered,
            warnings=warnings,
            metrics=metrics,
            session_risk=session_risk,
        )
        for checkpoint_type in checkpoint_types:
            self._persist_checkpoint(checkpoint_type, summary, now)
            state["last_checkpoint_at"] = now.isoformat()
            if checkpoint_type == "STARTUP":
                state["startup_checkpoint_written"] = True
            elif checkpoint_type == "FIRST_ACTIONABLE_CANDIDATE":
                state["first_actionable_checkpoint_written"] = True
            elif checkpoint_type == "END_OF_SESSION":
                state["end_of_session_checkpoint_date"] = (
                    now.astimezone(NEW_YORK).date().isoformat()
                )

        state["last_session_open"] = bool(collection.clock.is_open)
        state["last_position_count"] = len(collection.positions)
        state["last_open_order_count"] = len(collection.open_orders)
        self.journal.save_supervisor_state(state)
        self._state = state
        if dashboard_path is not None:
            self.publish_dashboard(summary, dashboard_path)
        self._last_summary = summary
        return summary

    def evaluated_epochs(self) -> set[str]:
        now = self._now().astimezone(UTC)
        self.journal.initialize()
        state = self._load_state(now)
        return {str(item) for item in state.get("evaluated_decision_epochs", [])}

    def observe_terminal(
        self,
        terminal_state: str,
        *,
        dashboard_path: Path | None,
    ) -> None:
        now = self._now().astimezone(UTC)
        self.journal.initialize()
        state = self._load_state(now)
        state["runtime_terminal_state"] = terminal_state
        state["runtime_stopped_at"] = now.isoformat()
        summary = self._last_summary
        if summary is None and dashboard_path is not None:
            try:
                dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
                candidate = dashboard.get("supervisor")
                if isinstance(candidate, dict):
                    summary = candidate
            except (OSError, json.JSONDecodeError):
                summary = None
        if summary is not None:
            summary = {
                **summary,
                "updated_at": now.isoformat(),
                "system_state": "PAUSED",
                "loop_advancing": False,
                "loop_state": terminal_state,
                "next_expected_action": (
                    (
                        "WAIT FOR NEXT REGULAR SESSION; PRESERVE AUTONOMOUS PAPER AUTHORITY"
                        if self.settings.entry_armed
                        else "WAIT FOR NEXT REGULAR SESSION; KEEP ENTRY DISABLED"
                    )
                    if terminal_state == "REGULAR_SESSION_COMPLETE"
                    else (
                        "OWNER REVIEW REQUIRED; DO NOT SUBMIT"
                        if terminal_state == "OPERATOR_REVIEW_PENDING"
                        else "RUNTIME STOPPED; START THE SUPERVISED LOOP WHEN SCHEDULED"
                    )
                ),
                "broker_submission_allowed": False,
            }
            if terminal_state == "REGULAR_SESSION_COMPLETE":
                session_date = now.astimezone(NEW_YORK).date().isoformat()
                if state.get("end_of_session_checkpoint_date") != session_date:
                    self._persist_checkpoint("END_OF_SESSION", summary, now)
                    state["end_of_session_checkpoint_date"] = session_date
            if dashboard_path is not None:
                self.publish_dashboard(summary, dashboard_path)
            self._last_summary = summary
        self.journal.save_supervisor_state(state)

    def observe_failure(
        self,
        error: Exception,
        *,
        cycle: int,
        dashboard_path: Path | None,
    ) -> None:
        now = self._now().astimezone(UTC)
        self.journal.initialize()
        state = self._load_state(now)
        state["last_cycle_at"] = now.isoformat()
        state["last_loop_state"] = "DEGRADED_RETRY_NEXT_CADENCE"
        state["active_alerts"] = sorted(
            set(state.get("active_alerts", [])) | {"BROKER_MISMATCH"}
        )
        self.journal.open_incident(
            component="competition_supervisor:runtime_cycle",
            severity="CRITICAL",
            state="PAUSED",
            message=f"Runtime cycle {cycle} failed: {type(error).__name__}: {error}",
            protective_action=(
                "Keep new exposure paused; recollect and reconcile on the next cadence."
            ),
        )
        self.journal.save_supervisor_state(state)
        self._state = state
        dashboard_service_healthy = self.recovery.dashboard_healthy()
        if dashboard_path is not None and (
            not self._dashboard_fresh(dashboard_path, now) or not dashboard_service_healthy
        ):
            self.recovery.restart_dashboard()

    def _load_state(self, now: datetime) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        stored = self.journal.load_supervisor_state()
        if stored is None or stored.get("version") != SUPERVISOR_STATE_VERSION:
            live_analysis_times = [
                occurred
                for item in self.journal.list_events(EventType.PROPOSAL)
                if str(item["source"]).startswith("terra_live")
                and (occurred := _as_datetime(item["occurred_at"])) is not None
            ]
            competition_started_at = min(live_analysis_times, default=now)
            stored = {
                "version": SUPERVISOR_STATE_VERSION,
                "competition_started_at": competition_started_at.isoformat(),
                "last_cycle_at": None,
                "last_epoch": None,
                "last_epoch_advanced_at": None,
                "last_checkpoint_at": None,
                "active_alerts": [],
                "recovery_count": 0,
                "peak_equity": None,
                "capital_observation_total": "0",
                "equity_observation_count": 0,
            }
        self._state = stored
        return stored

    def _detect(
        self,
        collection: LiveEvidenceCollection,
        *,
        now: datetime,
        decision_epoch: str | None,
        loop_state: str,
        position_manager_attached: bool,
        dashboard_path: Path | None,
    ) -> list[SupervisorAlert]:
        state = self._load_state(now)
        alerts: list[SupervisorAlert] = []
        prior_cycle = _as_datetime(state.get("last_cycle_at"))
        prior_epoch_advance = _as_datetime(state.get("last_epoch_advanced_at"))
        same_session_date = (
            prior_cycle is not None
            and prior_cycle.astimezone(NEW_YORK).date()
            == now.astimezone(NEW_YORK).date()
        )
        if (
            collection.clock.is_open
            and state.get("last_session_open") is True
            and prior_cycle is not None
            and same_session_date
        ):
            if now - prior_cycle > timedelta(seconds=self.cadence_seconds * 3):
                alerts.append(
                    SupervisorAlert(
                        "LOOP_STALLED",
                        "CRITICAL",
                        "The live-loop heartbeat exceeded three configured monitor cadences.",
                        "Keep entry blocked; recover durable state and reconcile before resume.",
                    )
                )
            elif (
                decision_epoch == state.get("last_epoch")
                and prior_epoch_advance is not None
                and now - prior_epoch_advance > timedelta(minutes=12)
            ):
                alerts.append(
                    SupervisorAlert(
                        "LOOP_STALLED",
                        "CRITICAL",
                        "The regular-session completed five-minute epoch did not advance.",
                        "Keep entry blocked; refresh data and verify a new completed epoch.",
                    )
                )
        data_hard_failures = tuple(
            item
            for item in collection.snapshot.hard_failures
            if not str(item).startswith("BROKER_")
        )
        if data_hard_failures or (
            collection.clock.is_open and collection.snapshot.stale_sources
        ):
            alerts.append(
                SupervisorAlert(
                    "DATA_STALE",
                    "CRITICAL",
                    "Actionable SIP/OPRA evidence is stale or incomplete.",
                    "Pause entry eligibility; recollect and require fresh timestamps.",
                )
            )
        if not collection.reconciliation.matched:
            alerts.append(
                SupervisorAlert(
                    "BROKER_MISMATCH",
                    "CRITICAL",
                    "Alpaca positions/orders do not match durable local broker state.",
                    "Block new exposure and reconcile broker truth before any submission.",
                )
            )
        outcome_failure = self._latest_ai_failure(now)
        if loop_state == "AI_UNAVAILABLE" or outcome_failure:
            failure_suffix = f": {outcome_failure}" if outcome_failure else ""
            alerts.append(
                SupervisorAlert(
                    "AI_UNAVAILABLE",
                    "WARNING",
                    f"Terra analysis is unavailable{failure_suffix}.",
                    "Block analysis-dependent entries; preserve deterministic position management.",
                )
            )
        if not self._journal_progress_healthy(now, collection.clock.is_open):
            alerts.append(
                SupervisorAlert(
                    "JOURNAL_STALLED",
                    "CRITICAL",
                    "Authoritative journal progress is missing during an active session.",
                    "Keep new exposure paused and preserve an emergency incident record.",
                )
            )
        orders = self.journal.broker_order_records()
        unresolved_exit = self.journal.has_unverified_exit()
        unresolved_submission = any(
            item["status"] in {"SUBMISSION_PENDING", "SUBMISSION_FAILED", "SUBMIT_UNKNOWN"}
            and item["payload"].get("intent", {}).get("position_intent") != "sell_to_close"
            for item in orders
        )
        if unresolved_submission:
            alerts.append(
                SupervisorAlert(
                    "UNRESOLVED_SUBMISSION",
                    "CRITICAL",
                    "A durable broker submission has uncertain terminal state.",
                    "Never retry blindly; reconcile by durable client-order identity.",
                )
            )
        if unresolved_exit:
            alerts.append(
                SupervisorAlert(
                    "UNRESOLVED_EXIT",
                    "CRITICAL",
                    "A position exit is not yet verified broker-flat.",
                    "Block new entry and continue reconciliation/position management.",
                )
            )
        lifecycles = self.journal.active_position_lifecycles()
        broker_symbols = {item.symbol for item in collection.positions}
        lifecycle_symbols = {item["symbol"] for item in lifecycles}
        if collection.positions and broker_symbols != lifecycle_symbols:
            alerts.append(
                SupervisorAlert(
                    "UNEXPECTED_POSITION",
                    "CRITICAL",
                    "A broker position is not matched by one active durable lifecycle.",
                    "Block entry; reconcile and attach the immutable position plan.",
                )
            )
        if collection.positions and (
            not self.settings.position_management_enabled
            or not self.settings.position_management_armed
            or not position_manager_attached
        ):
            alerts.append(
                SupervisorAlert(
                    "POSITION_WITHOUT_MANAGEMENT",
                    "CRITICAL",
                    "A verified position exists without armed deterministic management.",
                    "Raise a persistent critical incident and keep all new exposure blocked.",
                )
            )
        if dashboard_path is not None and not self._dashboard_fresh(dashboard_path, now):
            restarted = self.recovery.restart_dashboard()
            detail = "Dashboard state is stale or unreadable."
            if restarted:
                detail += " Independent dashboard recovery was requested."
            alerts.append(
                SupervisorAlert(
                    "DASHBOARD_STALE",
                    "WARNING",
                    detail,
                    "Recover dashboard independently; do not disturb the trading runtime.",
                )
            )
        return alerts

    def _latest_ai_failure(self, now: datetime) -> str | None:
        events = self.journal.list_events(EventType.PROPOSAL)
        terra = [item for item in events if str(item["source"]).startswith("terra_live")]
        if not terra:
            return None
        latest = terra[-1]
        occurred_at = _as_datetime(latest["occurred_at"])
        if occurred_at is None or now - occurred_at > timedelta(minutes=15):
            return None
        failure = latest["payload"].get("failure_code")
        return None if failure in {None, ""} else str(failure)

    def _journal_progress_healthy(self, now: datetime, market_open: bool) -> bool:
        if not market_open:
            return True
        events = self.journal.list_events()
        if not events:
            return True
        latest = _as_datetime(events[-1]["occurred_at"])
        return latest is not None and now - latest <= timedelta(seconds=self.cadence_seconds * 3)

    @staticmethod
    def _dashboard_fresh(path: Path, now: datetime) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        updated = _as_datetime(payload.get("updated_at"))
        if updated is None:
            return False
        # Closed/pre-market dashboards remain truthful without being rewritten every minute.
        session = str(payload.get("market", {}).get("session", "")).upper()
        if session not in {"OPEN", "REGULAR"}:
            return True
        return now - updated <= timedelta(minutes=3)

    def _metrics(
        self,
        collection: LiveEvidenceCollection,
        state: dict[str, Any],
        now: datetime,
        session_risk: SessionRiskSnapshot,
    ) -> dict[str, Any]:
        started = _as_datetime(state.get("competition_started_at")) or now
        events = [
            item
            for item in self.journal.list_events()
            if (_as_datetime(item["occurred_at"]) or started) >= started
        ]
        terra_events = [
            item
            for item in events
            if str(item["source"]).startswith("terra_live")
            and isinstance(item["payload"].get("proposal"), dict)
        ]
        directions = Counter(
            str(item["payload"]["proposal"].get("direction", "NO_TRADE"))
            for item in terra_events
        )
        live_passport_ids = {
            str(item["passport_id"])
            for item in terra_events
            if item.get("passport_id") is not None
        }
        referee_events = [
            item
            for item in events
            if item["event_type"] == EventType.REFEREE_VERDICT.value
            and str(item.get("passport_id")) in live_passport_ids
        ]
        verdicts = Counter(
            str(item["payload"].get("verdict", "UNKNOWN")) for item in referee_events
        )
        reasons = Counter(
            str(item["payload"].get("reason_code", "UNKNOWN")) for item in referee_events
        )
        selector_events = [
            item
            for item in events
            if item["source"] == "deterministic_option_selector"
            and str(item.get("passport_id")) in live_passport_ids
        ]
        actionable = sum(
            1
            for item in selector_events
            if item["payload"].get("selection", {}).get("candidate") is not None
        )
        orders = [
            item
            for item in self.journal.broker_order_records()
            if (_as_datetime(item["created_at"]) or started) >= started
        ]
        submitted = [
            item
            for item in orders
            if item["status"]
            not in {"AUTHORITY_GRANTED", "ATTEMPT_RESERVED", "SUBMISSION_FAILED"}
        ]
        fills = [
            item
            for item in orders
            if item["status"].lower() in {"filled", "closed_broker_flat"}
            or (_as_decimal(item["payload"].get("filled_quantity")) or Decimal("0")) > 0
        ]
        lifecycles = self.journal.all_position_lifecycles()
        completed = [item for item in lifecycles if item["state"] == "CLOSED_BROKER_FLAT"]
        risk_stop_exits = sum(
            1 for item in completed if item["lifecycle"].get("exit_reason") == "RISK_STOP"
        )
        realized = [
            value
            for item in completed
            if (value := self._realized_pnl(item)) is not None
        ]
        winners = [value for value in realized if value > 0]
        losers = [value for value in realized if value < 0]
        holding_seconds: list[Decimal] = []
        for item in completed:
            opened = _as_datetime(item["lifecycle"].get("fill_confirmed_at"))
            closed = _as_datetime(
                item["lifecycle"].get("broker_flat_verified_at")
                or item["lifecycle"].get("closed_at")
            )
            if opened is not None and closed is not None and closed >= opened:
                holding_seconds.append(Decimal(str((closed - opened).total_seconds())))
        qualities = [
            item["payload"].get("execution_quality")
            for item in orders
            if isinstance(item["payload"].get("execution_quality"), dict)
        ]
        quoted_spreads = [
            value
            for item in qualities
            if (value := _as_decimal(item.get("quoted_spread_premium_percentage"))) is not None
        ]
        fill_vs_mid = [
            value
            for item in qualities
            if (value := _as_decimal(item.get("fill_vs_midpoint"))) is not None
        ]
        equity = _as_decimal(collection.account.equity)
        peak = _as_decimal(state.get("peak_equity"))
        drawdown = None
        if equity is not None and peak is not None and peak > 0:
            drawdown = (peak - equity) / peak
        deployed = sum(
            (abs(_as_decimal(item.market_value) or Decimal("0")) for item in collection.positions),
            Decimal("0"),
        )
        observation_count = int(state.get("equity_observation_count", 0))
        average_deployed = None
        if observation_count:
            average_deployed = (
                _as_decimal(state.get("capital_observation_total")) or Decimal("0")
            ) / Decimal(observation_count)
        profit_factor = None
        gross_loss = abs(sum(losers, Decimal("0")))
        if winners and gross_loss > 0:
            profit_factor = sum(winners, Decimal("0")) / gross_loss
        entry_passports = [
            str(item["passport_id"])
            for item in submitted
            if item["payload"].get("intent", {}).get("position_intent") == "buy_to_open"
        ]
        entry_identity_violation = any(
            count > 1 for count in Counter(entry_passports).values()
        )
        incident_text = " ".join(
            f"{item['component']} {item['message']}" for item in self.journal.incident_records()
        ).upper()
        return {
            "decision_epochs": len(terra_events),
            "eligible_epochs": len(terra_events),
            "terra_long_call": directions["LONG_CALL"],
            "terra_long_put": directions["LONG_PUT"],
            "terra_no_trade": directions["NO_TRADE"],
            "referee_approve": verdicts["APPROVE"],
            "referee_reduce": verdicts["REDUCE"],
            "referee_abstain": verdicts["ABSTAIN"],
            "referee_block": verdicts["BLOCK"],
            "actionable_candidates": actionable,
            "trades_submitted": len(submitted),
            "fills": len(fills),
            "completed_positions": len(completed),
            "wins": len(winners),
            "losses": len(losers),
            "realized_pnl": _safe_number(sum(realized, Decimal("0"))),
            "realized_session_pnl": _safe_number(session_risk.realized_pnl),
            "session_loss_limit": _safe_number(session_risk.loss_limit),
            "session_loss_remaining": _safe_number(session_risk.loss_remaining),
            "session_risk_status": session_risk.status,
            "unrealized_pnl": _safe_number(
                sum(
                    (
                        _as_decimal(item.unrealized_pl) or Decimal("0")
                        for item in collection.positions
                    ),
                    Decimal("0"),
                )
            ),
            "current_equity": _safe_number(equity),
            "peak_equity": _safe_number(peak),
            "max_drawdown_fraction": _safe_number(drawdown),
            "capital_deployed": _safe_number(deployed),
            "average_capital_deployed": _safe_number(average_deployed),
            "average_winner": _safe_number(_mean(winners)),
            "average_loser": _safe_number(_mean(losers)),
            "profit_factor": _safe_number(profit_factor),
            "average_holding_minutes": _safe_number(
                None if not holding_seconds else _mean(holding_seconds) / Decimal("60")
            ),
            "average_quoted_spread_percent": _safe_number(_mean(quoted_spreads)),
            "average_fill_vs_midpoint": _safe_number(_mean(fill_vs_mid)),
            "risk_stop_exits": risk_stop_exits,
            "entry_identity_violation": entry_identity_violation,
            "risk_limit_warning_active": any(
                marker in incident_text
                for marker in {"DAILY LOSS", "DAILY_LOSS", "RISK LIMIT", "MAX DRAWDOWN"}
            ),
            "top_refusal_reasons": [
                {"reason": reason, "count": count} for reason, count in reasons.most_common(5)
            ],
            "incident_count": len(self.journal.incident_records()),
            "sample_size_note": (
                "Descriptive competition telemetry only; sample size is too small for "
                "statistical significance."
            ),
        }

    def _realized_pnl(self, lifecycle: dict[str, Any]) -> Decimal | None:
        return reconciled_realized_pnl(self.journal, lifecycle)

    @staticmethod
    def _warnings(metrics: dict[str, Any]) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        decisions = int(metrics["decision_epochs"])
        no_trade = int(metrics["terra_no_trade"])
        abstain = int(metrics["referee_abstain"])
        blocked = int(metrics["referee_block"])
        if decisions >= 12 and int(metrics["actionable_candidates"]) == 0:
            warnings.append(
                {
                    "code": "NO_CAPITAL_DEPLOYMENT",
                    "detail": "Twelve or more valid epochs produced no actionable candidate.",
                }
            )
        if decisions >= 12 and (no_trade + abstain) / decisions >= 0.8:
            warnings.append(
                {
                    "code": "EXCESSIVE_ABSTENTION",
                    "detail": "At least 80% of observed epochs are NO_TRADE/ABSTAIN.",
                }
            )
        if decisions >= 8 and blocked / decisions >= 0.6:
            warnings.append(
                {
                    "code": "EXCESSIVE_BLOCKING",
                    "detail": "At least 60% of observed epochs are blocked; inspect top reasons.",
                }
            )
        if metrics["entry_identity_violation"]:
            warnings.append(
                {
                    "code": "OVERTRADING",
                    "detail": (
                        "More than one entry submission identity exists for a single Passport."
                    ),
                }
            )
        if metrics["risk_limit_warning_active"]:
            warnings.append(
                {
                    "code": "DRAWDOWN_WARNING",
                    "detail": (
                        "Existing risk telemetry reports proximity to an approved loss limit."
                    ),
                }
            )
        average_spread = metrics.get("average_quoted_spread_percent")
        if average_spread is not None and float(average_spread) > float(
            MAX_SPREAD_RATIO * Decimal("100")
        ):
            warnings.append(
                {
                    "code": "POOR_EXECUTION_QUALITY",
                    "detail": (
                        "Average quoted spread exceeds the existing 10% selector gate."
                    ),
                }
            )
        if metrics["risk_stop_exits"] >= 2:
            warnings.append(
                {
                    "code": "REPEATED_STOP_OUT",
                    "detail": (
                        "Multiple realized losses exist; review exit evidence without retuning."
                    ),
                }
            )
        if metrics["wins"] >= 2 and (metrics.get("profit_factor") or 0) > 1:
            warnings.append(
                {
                    "code": "PROFITABLE_PATTERN_OBSERVED",
                    "detail": (
                        "Positive descriptive pattern observed; do not increase risk automatically."
                    ),
                }
            )
        return warnings

    def _checkpoint_types(
        self,
        collection: LiveEvidenceCollection,
        *,
        state: dict[str, Any],
        now: datetime,
        outcome: LiveDecisionOutcome | None,
        alerts: list[SupervisorAlert],
        recovered: list[str],
    ) -> list[str]:
        types: list[str] = []
        if not state.get("startup_checkpoint_written"):
            types.append("STARTUP")
        last_checkpoint = _as_datetime(state.get("last_checkpoint_at"))
        if collection.clock.is_open and (
            last_checkpoint is None or now - last_checkpoint >= CHECKPOINT_INTERVAL
        ):
            types.append("HOURLY")
        if (
            outcome is not None
            and outcome.decision.operator_review.state == "READY_FOR_OPERATOR_REVIEW"
            and not state.get("first_actionable_checkpoint_written")
        ):
            types.append("FIRST_ACTIONABLE_CANDIDATE")
        if len(collection.positions) > int(state.get("last_position_count", 0)):
            types.append("TRADE_ENTRY")
        if (
            int(state.get("last_position_count", 0)) > 0
            and not collection.positions
            and not self.journal.has_unverified_exit()
        ):
            types.append("TRADE_EXIT")
        if any(item.severity == "CRITICAL" for item in alerts):
            types.append("CRITICAL_INCIDENT")
        if recovered:
            types.append("SUCCESSFUL_RECOVERY")
        session_date = now.astimezone(NEW_YORK).date().isoformat()
        if (
            state.get("last_session_open") is True
            and not collection.clock.is_open
            and state.get("end_of_session_checkpoint_date") != session_date
        ):
            types.append("END_OF_SESSION")
        return list(dict.fromkeys(types))

    def _summary(
        self,
        collection: LiveEvidenceCollection,
        *,
        now: datetime,
        decision_epoch: str | None,
        loop_state: str,
        alerts: list[SupervisorAlert],
        recovered: list[str],
        warnings: list[dict[str, str]],
        metrics: dict[str, Any],
        session_risk: SessionRiskSnapshot,
    ) -> dict[str, Any]:
        stale_labels = {str(item).upper() for item in collection.snapshot.stale_sources}
        sip_state = (
            "STALE"
            if any("SIP" in item or "STOCK" in item for item in stale_labels)
            else "AVAILABLE"
        )
        opra_state = (
            "STALE"
            if any("OPRA" in item or "OPTION" in item for item in stale_labels)
            else "AVAILABLE"
        )
        return {
            "version": SUPERVISOR_STATE_VERSION,
            "updated_at": now.isoformat(),
            "system_state": (
                "PAUSED"
                if not collection.clock.is_open
                or not session_risk.entry_allowed
                or any(a.severity == "CRITICAL" for a in alerts)
                else ("DEGRADED" if alerts else "HEALTHY")
            ),
            "loop_advancing": not any(item.code == "LOOP_STALLED" for item in alerts),
            "latest_completed_epoch": decision_epoch,
            "loop_state": loop_state,
            "broker_reconciled": collection.reconciliation.matched,
            "sip": sip_state,
            "opra": opra_state,
            "entry_authority": (
                "SESSION_RISK_BLOCKED"
                if not session_risk.entry_allowed
                else "DISABLED"
                if not self.settings.entry_enabled
                else "ENABLED"
            ),
            "position_management": (
                "ARMED" if self.settings.position_management_armed else "DISABLED_OR_UNARMED"
            ),
            "broker_lock": "ACTIVE" if self.settings.broker_lock else "CLEAR",
            "position_count": len(collection.positions),
            "open_order_count": len(collection.open_orders),
            "alerts": [item.to_dict() for item in alerts],
            "recoveries": recovered,
            "behavioral_warnings": warnings,
            "metrics": metrics,
            "session_risk": session_risk.to_dict(),
            "next_expected_action": self._next_action(collection, alerts, loop_state),
            "broker_submission_allowed": False,
        }

    def _next_action(
        self,
        collection: LiveEvidenceCollection,
        alerts: list[SupervisorAlert],
        loop_state: str,
    ) -> str:
        if alerts:
            return "PROTECT, RECOVER, VERIFY, THEN RESUME MONITORING"
        if collection.positions:
            return "CONTINUE DETERMINISTIC POSITION MANAGEMENT UNTIL BROKER-FLAT"
        if not collection.clock.is_open:
            return (
                "WAIT FOR NEXT REGULAR SESSION; PRESERVE AUTONOMOUS PAPER AUTHORITY"
                if self.settings.entry_armed
                else "WAIT FOR NEXT REGULAR SESSION; KEEP ENTRY DISABLED"
            )
        if loop_state.startswith("ENTRY_") or loop_state.startswith("SUBMIT_UNKNOWN"):
            return "RECONCILE ENTRY ORDER AND POSITION BEFORE ANY NEW DECISION"
        if loop_state == "READY_FOR_OPERATOR_REVIEW":
            return (
                "APPLY IMMUTABLE PLAN AND DETERMINISTIC PAPER AUTHORITY"
                if self.settings.entry_armed
                else "RECORD CANDIDATE; CONTINUE MONITORING WHILE ENTRY IS DISABLED"
            )
        return "MONITOR NEXT COMPLETED FIVE-MINUTE EVIDENCE EPOCH"

    def _persist_checkpoint(
        self,
        checkpoint_type: str,
        summary: dict[str, Any],
        now: datetime,
    ) -> None:
        checkpoint_id = f"cajnmnstr-checkpoint-{uuid.uuid4()}"
        payload = {**summary, "checkpoint_id": checkpoint_id, "checkpoint_type": checkpoint_type}
        self.journal.record_checkpoint(
            checkpoint_id=checkpoint_id,
            checkpoint_type=checkpoint_type,
            session_date=now.astimezone(NEW_YORK).date().isoformat(),
            created_at=now,
            payload=payload,
        )
        self.journal.append_event(
            EventType.SUPERVISOR_CHECKPOINT,
            source="competition_supervisor",
            severity="WARNING" if summary["system_state"] != "HEALTHY" else "INFO",
            correlation_id=checkpoint_id,
            payload={
                "checkpoint_id": checkpoint_id,
                "checkpoint_type": checkpoint_type,
                "system_state": summary["system_state"],
                "decision_epochs": summary["metrics"]["decision_epochs"],
                "trades": summary["metrics"]["trades_submitted"],
                "realized_pnl": summary["metrics"]["realized_pnl"],
                "realized_session_pnl": summary["metrics"]["realized_session_pnl"],
                "session_risk_status": summary["metrics"]["session_risk_status"],
                "session_loss_remaining": summary["metrics"]["session_loss_remaining"],
                "entry_submission_allowed": False,
            },
            protective_action=(
                None
                if summary["system_state"] == "HEALTHY"
                else "Keep new exposure paused until the recorded condition is verified resolved."
            ),
        )

    @staticmethod
    def publish_dashboard(summary: dict[str, Any], dashboard_path: Path) -> None:
        try:
            dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dashboard = {
                "schema_version": 1,
                "mode": "PAPER",
                "operational_state": "DEGRADED",
                "truth_label": "SUPERVISOR ONLY · DASHBOARD RECOVERY REQUIRED",
                "updated_at": summary["updated_at"],
            }
        dashboard["supervisor"] = summary
        write_dashboard_state(dashboard_path, dashboard)
