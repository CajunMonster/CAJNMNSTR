import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.health import (
    ENTRY_CRITICAL_COMPONENTS,
    EXIT_CRITICAL_COMPONENTS,
    NONCRITICAL_FOR_EXIT_COMPONENTS,
    HealthSupervisor,
    freshness_health,
)
from cajnmnstr.models import HealthState


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path),
            "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
        },
        load_local_file=False,
    )


def test_missing_credentials_pause_broker_authority_and_persist_health(tmp_path: Path) -> None:
    supervisor = HealthSupervisor(settings(tmp_path))
    report = supervisor.evaluate()
    assert report.state is HealthState.PAUSED
    assert not report.execution_armed
    assert (tmp_path / "journal" / "cajnmnstr.sqlite3").exists()
    alpaca = next(component for component in report.components if component.component == "alpaca")
    assert alpaca.state is HealthState.PAUSED
    assert "execution disabled" in alpaca.protective_action.lower()
    supervisor.evaluate()
    connection = sqlite3.connect(tmp_path / "journal" / "cajnmnstr.sqlite3")
    try:
        open_incidents = connection.execute(
            "SELECT component FROM health_incidents WHERE resolved_at IS NULL"
        ).fetchall()
    finally:
        connection.close()
    assert sorted(row[0] for row in open_incidents) == ["ai_provider", "alpaca"]


def test_stale_data_is_paused_with_a_protective_action() -> None:
    now = datetime.now(UTC)
    component = freshness_health(
        component="spy_quote",
        observed_at=now - timedelta(minutes=2),
        maximum_age=timedelta(seconds=30),
        now=now,
    )
    assert component.state is HealthState.PAUSED
    assert "block order" in component.protective_action.lower()


def test_fresh_data_is_healthy() -> None:
    now = datetime.now(UTC)
    component = freshness_health(
        component="spy_quote",
        observed_at=now - timedelta(seconds=2),
        maximum_age=timedelta(seconds=30),
        now=now,
    )
    assert component.state is HealthState.HEALTHY


def test_health_authority_profiles_are_explicit_and_narrow() -> None:
    assert {"ai_provider", "risk_limits", "option_quote"} <= ENTRY_CRITICAL_COMPONENTS
    assert {
        "alpaca",
        "broker_state",
        "broker_reconciliation",
        "market_session",
        "option_quote",
        "evidence_store",
    } <= EXIT_CRITICAL_COMPONENTS
    assert {"ai_provider", "risk_limits", "spy_quote"} <= (
        NONCRITICAL_FOR_EXIT_COMPONENTS
    )
    assert EXIT_CRITICAL_COMPONENTS.isdisjoint(NONCRITICAL_FOR_EXIT_COMPONENTS)


def test_incomplete_supervisor_report_does_not_overstate_exit_authority(
    tmp_path: Path,
) -> None:
    configured = Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path),
            "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "ALPACA_API_KEY": "fixture-key",
            "ALPACA_SECRET_KEY": "fixture-secret",
            "CAJNMNSTR_EXECUTION_ENABLED": "true",
            "CAJNMNSTR_EXECUTION_CONFIRMATION": EXECUTION_CONFIRMATION,
        },
        load_local_file=False,
    )
    report = HealthSupervisor(configured, alpaca_probe=lambda: None).evaluate()
    assert report.state is HealthState.DEGRADED
    assert not report.execution_armed
    assert not report.position_management_armed
    payload = report.to_dict()
    assert not payload["entry_execution_armed"]
    assert not payload["position_management_armed"]
