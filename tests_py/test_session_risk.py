from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from cajnmnstr.config import PAPER_API_URL, Settings
from cajnmnstr.health import (
    ENTRY_CRITICAL_COMPONENTS,
    EXIT_CRITICAL_COMPONENTS,
    ComponentHealth,
    HealthReport,
    authority_health,
)
from cajnmnstr.journal import Journal
from cajnmnstr.models import HealthState, MarketClockSnapshot
from cajnmnstr.session_risk import SessionRiskAuthority

SESSION_AT = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)


def settings(tmp_path: Path, *, limit: str | None = "500") -> Settings:
    values = {
        "CAJNMNSTR_ENV": "paper",
        "CAJNMNSTR_DATA_ROOT": str(tmp_path / "data"),
        "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "ALPACA_API_BASE_URL": PAPER_API_URL,
    }
    if limit is not None:
        values["CAJNMNSTR_SESSION_LOSS_LIMIT_USD"] = limit
    return Settings.from_env(values, load_local_file=False)


def clock(at: datetime = SESSION_AT, *, is_open: bool = True) -> MarketClockSnapshot:
    return MarketClockSnapshot(
        timestamp=at,
        is_open=is_open,
        next_open=at + timedelta(days=1),
        next_close=at + timedelta(hours=5),
    )


def completed_lifecycle(
    identifier: str,
    *,
    fill_at: datetime = SESSION_AT,
    entry: str = "4.00",
    exit_price: str = "4.00",
    quantity: str = "1",
) -> tuple[dict[str, object], dict[str, object]]:
    exit_id = f"cajnmnstr-exit-{identifier}"
    lifecycle = {
        "plan_id": f"plan-{identifier}",
        "state": "CLOSED_BROKER_FLAT",
        "created_at": fill_at.isoformat(),
        "exit_client_order_id": exit_id,
        "lifecycle": {
            "fill_confirmed_at": fill_at.isoformat(),
            "initial_confirmed_average_entry_price": entry,
            "initial_confirmed_quantity": quantity,
            "broker_flat_verified": True,
        },
    }
    order = {
        "status": "CLOSED_BROKER_FLAT",
        "payload": {
            "execution_quality": {
                "actual_fill_price": exit_price,
                "filled_quantity": quantity,
            }
        },
    }
    return lifecycle, order


def install_reconciled_records(
    journal: Journal,
    monkeypatch,
    records: list[tuple[dict[str, object], dict[str, object]]],
) -> None:
    monkeypatch.setattr(
        journal,
        "all_position_lifecycles",
        lambda: [item[0] for item in records],
    )
    monkeypatch.setattr(
        journal,
        "broker_order_record",
        lambda client_order_id: next(
            (
                item[1]
                for item in records
                if item[0]["exit_client_order_id"] == client_order_id
            ),
            None,
        ),
    )


def test_multiple_trades_continue_until_reconciled_session_loss_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    records = [
        completed_lifecycle("winner-1", exit_price="5.00"),
        completed_lifecycle("winner-2", exit_price="4.50"),
    ]
    install_reconciled_records(journal, monkeypatch, records)
    profitable = SessionRiskAuthority(app, journal).evaluate(clock())
    assert profitable.status == "READY"
    assert profitable.realized_pnl == Decimal("150.00")
    assert profitable.completed_lifecycles == 2

    records[:] = [
        completed_lifecycle(f"loser-{index}", exit_price="3.00")
        for index in range(4)
    ]
    before_limit = SessionRiskAuthority(app, journal).evaluate(clock())
    assert before_limit.status == "READY"
    assert before_limit.realized_pnl == Decimal("-400.00")
    assert before_limit.loss_remaining == Decimal("100.00")

    records.append(completed_lifecycle("loser-5", exit_price="3.00"))
    at_limit = SessionRiskAuthority(app, journal).evaluate(clock())
    assert at_limit.status == "LOCKED"
    assert at_limit.realized_pnl == Decimal("-500.00")
    assert at_limit.loss_remaining == Decimal("0")
    assert not at_limit.entry_allowed


def test_restart_preserves_loss_and_new_open_session_resets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = settings(tmp_path, limit="200")
    journal = Journal(app.journal_path)
    journal.initialize()
    prior = [completed_lifecycle("prior", exit_price="2.00")]
    install_reconciled_records(journal, monkeypatch, prior)
    first = SessionRiskAuthority(app, journal).evaluate(clock())
    assert first.status == "LOCKED"

    restarted = SessionRiskAuthority(app, journal).evaluate(clock())
    assert restarted.realized_pnl == Decimal("-200.00")
    assert journal.session_risk_state("2026-09-01")["status"] == "LOCKED"

    next_session = SESSION_AT + timedelta(days=1)
    reset = SessionRiskAuthority(app, journal).evaluate(clock(next_session))
    assert reset.status == "READY"
    assert reset.realized_pnl == Decimal("0")
    assert reset.entry_allowed


def test_unknown_reconciled_pnl_and_missing_threshold_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    lifecycle, order = completed_lifecycle("unverified", exit_price="3.00")
    lifecycle["lifecycle"]["broker_flat_verified"] = False
    install_reconciled_records(journal, monkeypatch, [(lifecycle, order)])
    unknown = SessionRiskAuthority(app, journal).evaluate(clock())
    assert unknown.status == "UNKNOWN"
    assert not unknown.entry_allowed

    no_limit = settings(tmp_path / "unconfigured", limit=None)
    no_limit_journal = Journal(no_limit.journal_path)
    no_limit_journal.initialize()
    unconfigured = SessionRiskAuthority(no_limit, no_limit_journal).evaluate(clock())
    assert unconfigured.status == "UNCONFIGURED"
    assert not unconfigured.entry_allowed


def test_session_loss_is_entry_critical_but_never_exit_critical() -> None:
    checked_at = SESSION_AT
    components = tuple(
        ComponentHealth(
            component=name,
            state=HealthState.PAUSED if name == "session_risk" else HealthState.HEALTHY,
            message=name,
            protective_action="Protect.",
            checked_at=checked_at,
        )
        for name in sorted(ENTRY_CRITICAL_COMPONENTS | EXIT_CRITICAL_COMPONENTS)
    )
    report = HealthReport(
        state=HealthState.PAUSED,
        components=components,
        checked_at=checked_at,
        entry_armed=False,
        position_management_armed=True,
        broker_lock_active=False,
    )
    assert not authority_health(report, position_intent="buy_to_open").allowed
    assert authority_health(report, position_intent="sell_to_close").allowed


def test_approved_eod_boundary_blocks_new_entry_without_touching_exit(
    tmp_path: Path,
) -> None:
    app = settings(tmp_path, limit="500")
    journal = Journal(app.journal_path)
    journal.initialize()
    at_cutoff = datetime(2026, 9, 1, 19, 35, tzinfo=UTC)
    snapshot = SessionRiskAuthority(app, journal).evaluate(clock(at_cutoff))
    assert snapshot.status == "ENTRY_WINDOW_CLOSED"
    assert snapshot.reason_code == "SESSION_ENTRY_WINDOW_CLOSED"
    assert not snapshot.entry_allowed
