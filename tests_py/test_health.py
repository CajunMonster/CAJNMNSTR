import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cajnmnstr.config import PAPER_API_URL, Settings
from cajnmnstr.health import HealthSupervisor, freshness_health
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
