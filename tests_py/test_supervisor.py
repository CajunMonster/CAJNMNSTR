import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.journal import Journal
from cajnmnstr.models import EventType
from cajnmnstr.supervisor import CompetitionSupervisor

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path / "data"),
            "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "ALPACA_API_KEY": "fixture-key",
            "ALPACA_SECRET_KEY": "fixture-secret",
            "ALPACA_STOCK_FEED": "sip",
            "ALPACA_OPTIONS_FEED": "opra",
            "ALPACA_DATA_ENTITLEMENT": "algo_trader_plus",
            "CAJNMNSTR_ENTRY_ENABLED": "false",
            "CAJNMNSTR_POSITION_MANAGEMENT_ENABLED": "true",
            "CAJNMNSTR_BROKER_LOCK": "false",
            "CAJNMNSTR_EXECUTION_CONFIRMATION": EXECUTION_CONFIRMATION,
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-openai-key",
            "CAJNMNSTR_SESSION_LOSS_LIMIT_USD": "1000",
        },
        load_local_file=False,
    )


def collection(
    *,
    at: datetime = NOW,
    stale=(),
    hard=(),
    reconciled=True,
    positions=(),
    open_orders=(),
    market_open=True,
    equity="100000",
):
    return SimpleNamespace(
        account=SimpleNamespace(equity=Decimal(equity)),
        clock=SimpleNamespace(
            timestamp=at,
            is_open=market_open,
            next_open=at + timedelta(days=1),
            next_close=at + timedelta(hours=5),
        ),
        positions=tuple(positions),
        open_orders=tuple(open_orders),
        reconciliation=SimpleNamespace(matched=reconciled),
        snapshot=SimpleNamespace(
            stale_sources=tuple(stale),
            hard_failures=tuple(hard),
        ),
    )


def observe(
    supervisor: CompetitionSupervisor,
    item,
    *,
    epoch="2026-09-01T15:00:00+00:00",
    state="NOT_ELIGIBLE",
    dashboard_path=None,
):
    return supervisor.observe_cycle(
        item,
        decision_epoch=epoch,
        loop_state=state,
        outcome=None,
        dashboard_path=dashboard_path,
        position_manager_attached=True,
    )


def supervisor(tmp_path: Path, *, now=NOW, recovery=None):
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    return app, journal, CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: now,
        recovery=recovery,
    )


def alert_codes(summary):
    return {item["code"] for item in summary["alerts"]}


def test_detects_loop_stall_during_regular_session(tmp_path: Path) -> None:
    app, journal, monitor = supervisor(tmp_path)
    del app
    journal.save_supervisor_state(
        {
            "version": 2,
            "competition_started_at": (NOW - timedelta(days=1)).isoformat(),
            "last_cycle_at": (NOW - timedelta(minutes=4)).isoformat(),
            "last_epoch": "2026-09-01T14:55:00+00:00",
            "last_epoch_advanced_at": (NOW - timedelta(minutes=4)).isoformat(),
            "last_session_open": True,
            "active_alerts": [],
            "equity_observation_count": 0,
            "capital_observation_total": "0",
        }
    )
    summary = observe(monitor, collection(), epoch="2026-09-01T14:55:00+00:00")
    assert "LOOP_STALLED" in alert_codes(summary)
    assert summary["broker_submission_allowed"] is False


@pytest.mark.parametrize("source", ["stock_quote", "option_quote"])
def test_detects_stale_sip_or_opra(tmp_path: Path, source: str) -> None:
    _, _, monitor = supervisor(tmp_path)
    summary = observe(monitor, collection(stale=(source,)))
    assert "DATA_STALE" in alert_codes(summary)


def test_closed_session_stale_quotes_are_truthful_pause_not_incident(tmp_path: Path) -> None:
    _, _, monitor = supervisor(tmp_path)
    summary = observe(
        monitor,
        collection(
            stale=("SPY_OPRA_OPTIONS", "MARKET_SESSION_CLOSED"),
            market_open=False,
        ),
    )
    assert "DATA_STALE" not in alert_codes(summary)
    assert summary["system_state"] == "PAUSED"
    assert summary["opra"] == "STALE"
    assert summary["next_expected_action"].startswith("WAIT FOR NEXT")


def test_detects_broker_mismatch(tmp_path: Path) -> None:
    _, _, monitor = supervisor(tmp_path)
    summary = observe(monitor, collection(reconciled=False))
    assert "BROKER_MISMATCH" in alert_codes(summary)
    assert summary["next_expected_action"].startswith("PROTECT")


def test_terra_outage_blocks_analysis_only(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.append_event(
        EventType.PROPOSAL,
        source="terra_live",
        payload={"failure_code": "TIMEOUT", "proposal": {"direction": "NO_TRADE"}},
    )
    with journal._connect() as connection:
        connection.execute(
            "UPDATE journal_events SET occurred_at = ? WHERE source = 'terra_live'",
            (NOW.isoformat(),),
        )
    summary = observe(monitor, collection())
    assert "AI_UNAVAILABLE" in alert_codes(summary)
    assert summary["position_management"] == "ARMED"
    assert summary["entry_authority"] == "DISABLED"


class DashboardRecovery:
    def __init__(self) -> None:
        self.calls = 0

    def restart_dashboard(self) -> bool:
        self.calls += 1
        return True

    def dashboard_healthy(self) -> bool:
        return True


def test_dashboard_failure_is_recovered_independently(tmp_path: Path) -> None:
    recovery = DashboardRecovery()
    _, _, monitor = supervisor(tmp_path, recovery=recovery)
    dashboard = tmp_path / "dashboard-state.json"
    dashboard.write_text("not-json", encoding="utf-8")
    summary = observe(monitor, collection(), dashboard_path=dashboard)
    assert recovery.calls == 1
    assert "DASHBOARD_STALE" in alert_codes(summary)
    assert json.loads(dashboard.read_text(encoding="utf-8"))["supervisor"]


def test_journal_stall_is_detected(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.append_event(EventType.CONNECTION, source="fixture", payload={})
    with journal._connect() as connection:
        connection.execute(
            "UPDATE journal_events SET occurred_at = ?",
            ((NOW - timedelta(minutes=10)).isoformat(),),
        )
    summary = observe(monitor, collection())
    assert "JOURNAL_STALLED" in alert_codes(summary)


def test_recovery_must_verify_before_resume_and_is_checkpointed(tmp_path: Path) -> None:
    current = [NOW]
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    monitor = CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: current[0],
    )
    first = observe(monitor, collection(stale=("stock_quote",)))
    assert "DATA_STALE" in alert_codes(first)
    current[0] += timedelta(minutes=1)
    second = observe(
        monitor,
        collection(at=current[0]),
        epoch="2026-09-01T15:05:00+00:00",
    )
    assert "DATA_STALE" in second["recoveries"]
    assert any(
        item["checkpoint_type"] == "SUCCESSFUL_RECOVERY"
        for item in journal.checkpoint_records()
    )


def test_submit_unknown_and_unresolved_exit_are_separate_and_never_retried(
    tmp_path: Path,
) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.create_passport("passport-entry", {"source_mode": "LIVE"})
    journal.create_passport("passport-exit", {"source_mode": "LIVE"})
    assert journal.authorize_order_attempt(
        client_order_id="cajnmnstr-entry-1",
        passport_id="passport-entry",
        payload={"intent": {"position_intent": "buy_to_open"}},
    )
    assert journal.authorize_order_attempt(
        client_order_id="cajnmnstr-exit-1",
        passport_id="passport-exit",
        payload={"intent": {"position_intent": "sell_to_close"}},
    )
    journal.update_broker_order(
        client_order_id="cajnmnstr-entry-1",
        broker_order_id=None,
        status="SUBMIT_UNKNOWN",
        payload={},
    )
    journal.update_broker_order(
        client_order_id="cajnmnstr-exit-1",
        broker_order_id=None,
        status="SUBMIT_UNKNOWN",
        payload={},
    )
    summary = observe(monitor, collection())
    assert {"UNRESOLVED_SUBMISSION", "UNRESOLVED_EXIT"} <= alert_codes(summary)
    assert len(journal.broker_order_records()) == 2


def test_unresolved_exit_blocks_new_entry(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.create_passport("passport-exit", {"source_mode": "LIVE"})
    assert journal.authorize_order_attempt(
        client_order_id="cajnmnstr-exit-only",
        passport_id="passport-exit",
        payload={"intent": {"position_intent": "sell_to_close"}},
    )
    summary = observe(monitor, collection())
    assert "UNRESOLVED_EXIT" in alert_codes(summary)
    assert summary["entry_authority"] == "DISABLED"


def test_hourly_checkpoint_and_metric_distribution(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.save_supervisor_state(
        {
            "version": 2,
            "competition_started_at": (NOW - timedelta(days=1)).isoformat(),
            "last_cycle_at": (NOW - timedelta(minutes=1)).isoformat(),
            "last_epoch": None,
            "last_epoch_advanced_at": None,
            "last_checkpoint_at": (NOW - timedelta(hours=2)).isoformat(),
            "startup_checkpoint_written": True,
            "active_alerts": [],
            "peak_equity": "100100",
            "equity_observation_count": 1,
            "capital_observation_total": "0",
        }
    )
    for index, (direction, verdict, reason) in enumerate([
        ("LONG_CALL", "APPROVE", "DIRECTION_STRONGLY_CONFIRMED"),
        ("LONG_PUT", "REDUCE", "DIRECTION_CONFIRMED_WITH_SOFT_CONFLICT"),
        ("NO_TRADE", "ABSTAIN", "AI_NO_TRADE"),
        ("NO_TRADE", "BLOCK", "STALE_EVIDENCE"),
    ]):
        passport_id = f"live-passport-{index}"
        journal.create_passport(passport_id, {"source_mode": "LIVE"})
        journal.append_event(
            EventType.PROPOSAL,
            source="terra_live",
            passport_id=passport_id,
            payload={"proposal": {"direction": direction}, "failure_code": None},
        )
        journal.append_event(
            EventType.REFEREE_VERDICT,
            source="deterministic_referee",
            passport_id=passport_id,
            payload={"verdict": verdict, "reason_code": reason},
        )
    position = SimpleNamespace(
        symbol="SPY260911C00500000",
        market_value=Decimal("425"),
        unrealized_pl=Decimal("12.50"),
    )
    summary = observe(monitor, collection(positions=(position,), equity="100050"))
    metrics = summary["metrics"]
    assert metrics["decision_epochs"] == 4
    assert metrics["terra_long_call"] == 1
    assert metrics["terra_long_put"] == 1
    assert metrics["terra_no_trade"] == 2
    assert metrics["referee_approve"] == 1
    assert metrics["referee_reduce"] == 1
    assert metrics["referee_abstain"] == 1
    assert metrics["referee_block"] == 1
    assert metrics["current_equity"] == 100050.0
    assert metrics["peak_equity"] == 100100.0
    assert metrics["capital_deployed"] == 425.0
    assert metrics["unrealized_pnl"] == 12.5
    assert any(
        item["checkpoint_type"] == "HOURLY" for item in journal.checkpoint_records()
    )


def test_behavioral_warnings_are_informational_and_do_not_change_policy(
    tmp_path: Path,
) -> None:
    app, journal, monitor = supervisor(tmp_path)
    journal.save_supervisor_state(
        {
            "version": 2,
            "competition_started_at": (NOW - timedelta(days=1)).isoformat(),
            "active_alerts": [],
            "equity_observation_count": 0,
            "capital_observation_total": "0",
        }
    )
    for index in range(12):
        passport_id = f"abstain-passport-{index}"
        journal.create_passport(passport_id, {"source_mode": "LIVE"})
        journal.append_event(
            EventType.PROPOSAL,
            source="terra_live",
            passport_id=passport_id,
            payload={"proposal": {"direction": "NO_TRADE"}, "failure_code": None},
        )
        journal.append_event(
            EventType.REFEREE_VERDICT,
            source="deterministic_referee",
            passport_id=passport_id,
            payload={"verdict": "ABSTAIN", "reason_code": "AI_NO_TRADE"},
        )
    summary = observe(monitor, collection())
    codes = {item["code"] for item in summary["behavioral_warnings"]}
    assert {"NO_CAPITAL_DEPLOYMENT", "EXCESSIVE_ABSTENTION"} <= codes
    assert app.entry_enabled is False
    assert app.position_management_enabled is True
    assert summary["broker_submission_allowed"] is False


def test_realized_pnl_uses_durable_entry_and_execution_quality(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    journal.create_passport("passport-exit-quality", {"source_mode": "LIVE"})
    assert journal.authorize_order_attempt(
        client_order_id="cajnmnstr-exit-quality",
        passport_id="passport-exit-quality",
        payload={"intent": {"position_intent": "sell_to_close"}},
    )
    journal.update_broker_order(
        client_order_id="cajnmnstr-exit-quality",
        broker_order_id="paper-order-quality",
        status="CLOSED_BROKER_FLAT",
        payload={
            "execution_quality": {
                "actual_fill_price": "5.40",
                "filled_quantity": "1",
            }
        },
    )
    lifecycle = {
        "state": "CLOSED_BROKER_FLAT",
        "exit_client_order_id": "cajnmnstr-exit-quality",
        "lifecycle": {
            "broker_flat_verified": True,
            "initial_confirmed_average_entry_price": "4.00",
            "initial_confirmed_quantity": "1",
        },
    }
    assert monitor._realized_pnl(lifecycle) == Decimal("140.00")


def test_watchdog_is_bounded_and_requires_explicit_autonomous_authority() -> None:
    script = (
        Path(__file__).parents[1]
        / "launcher"
        / "Start-Competition-Supervisor.ps1"
    ).read_text(encoding="utf-8")
    assert "MaximumRestarts = 3" in script
    assert "PAPER_AUTONOMOUS_COMPETITION" in script
    assert "position_management_armed" in script
    assert "session_loss_limit_usd -ne '2000'" in script
    assert "PAPER_POSITION_MANAGEMENT_LOOP" in script
    assert "PAPER_READ_ONLY_LOOP" in script
    assert "broker_submission_allowed = $false" in script
    assert "submit_order" not in script.lower()
    assert "CAJNMNSTRCompetitionSupervisor" in script
    assert "DUPLICATE_SUPERVISOR_BLOCKED" in script
    assert "competition-startup.jsonl" in script
    assert "--max-cycles', '1'" in script
    assert "--no-order-test" in script
    assert "PAPER_NO_ORDER_STARTUP_TEST" in script


def test_tuesday_task_registration_is_local_idempotent_and_secret_free() -> None:
    script = (
        Path(__file__).parents[1]
        / "launcher"
        / "Register-Tuesday-Competition-Startup.ps1"
    ).read_text(encoding="utf-8")
    assert "2026-09-01T08:15:00" in script
    assert "Start-Competition-Supervisor.ps1" in script
    assert "MultipleInstances = 'IgnoreNew'" in script
    assert "LogonType = 'Interactive'" in script
    assert "RunLevel = 'Limited'" in script
    assert "WorkingDirectory = $projectRoot" in script
    assert "ALPACA_API_KEY" not in script
    assert "ALPACA_SECRET_KEY" not in script
    assert "OPENAI_API_KEY" not in script


def test_restart_recovers_durable_supervisor_state(tmp_path: Path) -> None:
    app, journal, first = supervisor(tmp_path)
    initial = observe(first, collection())
    assert initial["latest_completed_epoch"]
    second = CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: NOW + timedelta(minutes=1),
    )
    restored = observe(
        second,
        collection(at=NOW + timedelta(minutes=1)),
        epoch="2026-09-01T15:05:00+00:00",
    )
    state = journal.load_supervisor_state()
    assert state is not None
    assert state["last_epoch"] == "2026-09-01T15:05:00+00:00"
    assert restored["loop_advancing"] is True
    assert len([c for c in journal.checkpoint_records() if c["checkpoint_type"] == "STARTUP"]) == 1


def test_startup_is_session_scoped_and_market_open_is_checkpointed(tmp_path: Path) -> None:
    current = [NOW - timedelta(hours=1)]
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    premarket = CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: current[0],
    )
    observe(
        premarket,
        collection(at=current[0], market_open=False),
        epoch=None,
    )
    assert [item["checkpoint_type"] for item in journal.checkpoint_records()] == [
        "STARTUP"
    ]

    current[0] = NOW
    opened = CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: current[0],
    )
    observe(opened, collection(at=current[0]), epoch="2026-09-01T15:00:00+00:00")
    checkpoint_types = [
        item["checkpoint_type"] for item in journal.checkpoint_records()
    ]
    assert checkpoint_types.count("STARTUP") == 1
    assert checkpoint_types.count("MARKET_OPEN") == 1

    next_day = NOW + timedelta(days=1)
    next_session = CompetitionSupervisor(
        app,
        journal,
        cadence_seconds=60,
        now=lambda: next_day,
    )
    observe(
        next_session,
        collection(at=next_day, market_open=False),
        epoch=None,
    )
    assert [
        item["checkpoint_type"] for item in journal.checkpoint_records()
    ].count("STARTUP") == 2


def test_clean_terminal_state_never_claims_loop_is_still_advancing(tmp_path: Path) -> None:
    _, journal, monitor = supervisor(tmp_path)
    summary = observe(monitor, collection(market_open=False))
    assert summary["loop_advancing"] is True
    dashboard = tmp_path / "dashboard.json"
    monitor.publish_dashboard(summary, dashboard)
    monitor.observe_terminal("REGULAR_SESSION_COMPLETE", dashboard_path=dashboard)
    published = json.loads(dashboard.read_text(encoding="utf-8"))["supervisor"]
    assert published["loop_advancing"] is False
    assert published["loop_state"] == "REGULAR_SESSION_COMPLETE"
    assert journal.load_supervisor_state()["runtime_terminal_state"] == (
        "REGULAR_SESSION_COMPLETE"
    )
