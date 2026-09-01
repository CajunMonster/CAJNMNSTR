from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.entry_execution import AutonomousPaperEntryHandler
from cajnmnstr.health import (
    ENTRY_CRITICAL_COMPONENTS,
    EXIT_CRITICAL_COMPONENTS,
    ComponentHealth,
    HealthReport,
)
from cajnmnstr.journal import Journal
from cajnmnstr.models import (
    BrokerOrderSnapshot,
    HealthState,
    MarketClockSnapshot,
    OrderCandidate,
    PositionSnapshot,
    ReconciliationReport,
    RefereeVerdict,
)
from cajnmnstr.position_policy import (
    FORCED_EOD_TIME,
    PREMIUM_STOP_FRACTION,
    PROFIT_TARGET_FRACTION,
    STRUCTURAL_FORMULA_VERSION,
    TIME_STOP_DURATION_MINUTES,
)
from cajnmnstr.services import DeterministicReferee
from cajnmnstr.session_risk import SessionRiskSnapshot

DECISION_AT = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
NEW_YORK = ZoneInfo("America/New_York")


def settings(tmp_path, *, entry: bool = True, management: bool = True) -> Settings:
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
            "CAJNMNSTR_ENTRY_ENABLED": str(entry).lower(),
            "CAJNMNSTR_POSITION_MANAGEMENT_ENABLED": str(management).lower(),
            "CAJNMNSTR_BROKER_LOCK": "false",
            "CAJNMNSTR_EXECUTION_CONFIRMATION": EXECUTION_CONFIRMATION,
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-openai-key",
            "CAJNMNSTR_SESSION_LOSS_LIMIT_USD": "2000",
        },
        load_local_file=False,
    )


def healthy_report(app: Settings) -> HealthReport:
    checked_at = datetime.now(UTC)
    names = sorted(ENTRY_CRITICAL_COMPONENTS | EXIT_CRITICAL_COMPONENTS)
    components = tuple(
        ComponentHealth(
            component=name,
            state=HealthState.HEALTHY,
            message=f"Fixture {name} is healthy.",
            protective_action="No protective action required.",
            checked_at=checked_at,
        )
        for name in names
    )
    return HealthReport(
        state=HealthState.HEALTHY,
        components=components,
        checked_at=checked_at,
        entry_armed=app.entry_armed,
        position_management_armed=app.position_management_armed,
        broker_lock_active=app.broker_lock,
    )


def candidate(
    *,
    client_order_id: str = "cajnmnstr-auto-001",
    quantity: int = 2,
) -> OrderCandidate:
    return OrderCandidate(
        symbol="SPY260918C00540000",
        quantity=quantity,
        side="buy",
        limit_price=Decimal("4.25"),
        client_order_id=client_order_id,
        position_intent="buy_to_open",
        decision_bid=Decimal("4.20"),
        decision_ask=Decimal("4.30"),
        quote_at=DECISION_AT,
    )


def payload(selected: OrderCandidate) -> dict[str, object]:
    return {
        "evidence_snapshot": {
            "decision_at": DECISION_AT.isoformat(),
            "features": {
                "underlying_price": "540",
                "vwap": "539",
                "opening_range_low": "538",
                "opening_range_high": "541",
            },
        },
        "terra": {"proposal": {"direction": "LONG_CALL"}},
        "option_selection": {
            "candidate": {
                "symbol": selected.symbol,
                "quantity": selected.quantity,
                "side": selected.side,
                "position_intent": selected.position_intent,
                "limit_price": str(selected.limit_price),
                "decision_bid": str(selected.decision_bid),
                "decision_ask": str(selected.decision_ask),
                "client_order_id": selected.client_order_id,
                "quote_at": selected.quote_at.isoformat(),
            }
        },
        "broker_submission_allowed": False,
    }


class FakePaperBroker:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.positions: list[PositionSnapshot] = []
        self.orders: list[BrokerOrderSnapshot] = []
        self.submissions = []
        self.cancel_calls: list[str] = []
        self.submit_error: Exception | None = None

    def list_positions(self):
        return list(self.positions)

    def list_orders(self):
        return list(self.orders)

    def get_clock(self) -> MarketClockSnapshot:
        return MarketClockSnapshot(
            timestamp=DECISION_AT,
            is_open=True,
            next_open=DECISION_AT + timedelta(days=1),
            next_close=DECISION_AT + timedelta(hours=5),
        )

    def submit_limit_order(self, intent):
        assert self.journal.broker_order_status(intent.client_order_id) == ("SUBMISSION_PENDING")
        if self.submit_error is not None:
            raise self.submit_error
        self.submissions.append(intent)
        order = BrokerOrderSnapshot(
            broker_order_id=f"paper-{len(self.submissions):03d}",
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            status="accepted",
            quantity=Decimal(intent.quantity),
            filled_quantity=Decimal("0"),
            filled_avg_price=None,
            limit_price=intent.limit_price,
            submitted_at=DECISION_AT,
            updated_at=DECISION_AT,
        )
        self.orders.append(order)
        return order

    def cancel_order(self, broker_order_id: str) -> None:
        self.cancel_calls.append(broker_order_id)


def outcome(
    app: Settings,
    journal: Journal,
    selected: OrderCandidate,
    *,
    session_allowed: bool = True,
    positions=(),
    open_orders=(),
    reconciled: bool = True,
):
    passport_id = f"passport-{selected.client_order_id}"
    sealed = payload(selected)
    journal.create_passport(passport_id, sealed)
    journal.seal_passport(passport_id, sealed)
    DeterministicReferee(journal).issue(
        passport_id=passport_id,
        verdict=RefereeVerdict.APPROVE,
        reason_code="FIXTURE_APPROVE",
        max_quantity=selected.quantity,
        max_limit_price=selected.limit_price,
    )
    health = healthy_report(app)
    session = SessionRiskSnapshot(
        session_date="2026-09-01",
        status="READY" if session_allowed else "LOCKED",
        realized_pnl=Decimal("0") if session_allowed else Decimal("-2000"),
        loss_limit=Decimal("2000"),
        loss_remaining=Decimal("2000") if session_allowed else Decimal("0"),
        completed_lifecycles=0,
        lifecycle_ids=(),
        entry_allowed=session_allowed,
        reason_code=("SESSION_RISK_READY" if session_allowed else "SESSION_LOSS_LIMIT_REACHED"),
        detail="Fixture session-risk authority.",
        evaluated_at=DECISION_AT,
    )
    report = ReconciliationReport(
        checked_at=DECISION_AT,
        broker_order_count=len(open_orders),
        broker_position_count=len(positions),
        unknown_broker_client_ids=() if reconciled else ("unexpected",),
    )
    return SimpleNamespace(
        collection=SimpleNamespace(
            positions=tuple(positions),
            open_orders=tuple(open_orders),
            reconciliation=report,
        ),
        decision=SimpleNamespace(
            passport_id=passport_id,
            selection=SimpleNamespace(candidate=selected),
            operator_review=SimpleNamespace(state="READY_FOR_OPERATOR_REVIEW"),
            referee=SimpleNamespace(verdict=RefereeVerdict.APPROVE),
        ),
        health=health,
        dashboard={
            "updated_at": DECISION_AT.isoformat(),
            "truth_label": "fixture",
            "decision": {"state": "READY_FOR_OPERATOR_REVIEW"},
            "controls": {"broker_submission_allowed": False},
            "execution": [],
            "activity": [],
        },
        session_risk=session,
    )


def setup_handler(tmp_path):
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    broker = FakePaperBroker(journal)
    handler = AutonomousPaperEntryHandler(app, journal, broker, broker)
    return app, journal, broker, handler


def test_valid_candidate_registers_frozen_plan_before_one_submission(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    selected = candidate()

    result = handler.submit(outcome(app, journal, selected))

    assert result.state == "ENTRY_PENDING_FILL"
    assert result.submission_attempted is True
    assert len(broker.submissions) == 1
    lifecycle = journal.position_lifecycle(symbol=selected.symbol)
    assert lifecycle is not None
    plan = lifecycle["plan"]
    assert plan.stop_loss_fraction == PREMIUM_STOP_FRACTION
    assert plan.profit_target_fraction == PROFIT_TARGET_FRACTION
    assert plan.time_stop_duration_minutes == TIME_STOP_DURATION_MINUTES
    assert (
        plan.forced_eod_at.astimezone(NEW_YORK).timetz().replace(tzinfo=None)
        == FORCED_EOD_TIME
    )
    assert plan.invalidation_formula_version == STRUCTURAL_FORMULA_VERSION
    assert journal.broker_order_status(selected.client_order_id) == "accepted"


def test_sealed_selector_mismatch_cannot_submit(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    sealed_candidate = candidate()
    decision = outcome(app, journal, sealed_candidate)
    decision.decision.selection.candidate = candidate(
        client_order_id="cajnmnstr-auto-tampered",
    )

    result = handler.submit(decision)

    assert result.state == "ENTRY_BLOCKED_SELECTOR_PASSPORT_MISMATCH"
    assert broker.submissions == []
    assert journal.active_position_lifecycles() == []


def test_position_manager_must_be_armed_before_entry(tmp_path) -> None:
    app = settings(tmp_path, management=False)
    journal = Journal(app.journal_path)
    journal.initialize()
    broker = FakePaperBroker(journal)
    handler = AutonomousPaperEntryHandler(app, journal, broker, broker)

    result = handler.submit(outcome(app, journal, candidate()))

    assert result.state == "ENTRY_BLOCKED_POSITION_MANAGEMENT_NOT_ARMED"
    assert broker.submissions == []


def test_session_loss_lock_blocks_entry_without_disabling_exit_authority(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)

    result = handler.submit(outcome(app, journal, candidate(), session_allowed=False))

    assert result.state == "ENTRY_BLOCKED_SESSION_LOSS_LIMIT_REACHED"
    assert broker.submissions == []
    assert app.position_management_armed is True


def test_broker_mismatch_and_existing_position_block_entry(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    existing = PositionSnapshot(
        symbol="SPY260918C00540000",
        quantity=Decimal("1"),
        side="long",
        market_value=Decimal("425"),
        average_entry_price=Decimal("4.25"),
        unrealized_pl=Decimal("0"),
    )

    mismatch = handler.submit(outcome(app, journal, candidate(), reconciled=False))
    second = candidate(client_order_id="cajnmnstr-auto-position")
    position = handler.submit(outcome(app, journal, second, positions=(existing,)))

    assert mismatch.state == "ENTRY_BLOCKED_BROKER_NOT_FLAT_RECONCILED"
    assert position.state == "ENTRY_BLOCKED_BROKER_NOT_FLAT_RECONCILED"
    assert broker.submissions == []


def test_timeout_is_submit_unknown_and_reconcile_never_retries(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    selected = candidate(client_order_id="cajnmnstr-auto-timeout")
    broker.submit_error = TimeoutError("fixture timeout after send")

    result = handler.submit(outcome(app, journal, selected))
    broker.submit_error = None
    collection = SimpleNamespace(
        positions=(),
        open_orders=(),
        reconciliation=ReconciliationReport(
            checked_at=DECISION_AT,
            broker_order_count=0,
            broker_position_count=0,
            missing_broker_client_ids=(selected.client_order_id,),
        ),
    )
    recovery = handler.reconcile(collection)

    assert result.state == "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    assert recovery == "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    assert journal.broker_order_status(selected.client_order_id) == "SUBMIT_UNKNOWN"
    assert broker.submissions == []


def test_partial_fill_preserves_position_and_cancels_remainder_only_once(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    selected = candidate(client_order_id="cajnmnstr-auto-partial")
    submitted = handler.submit(outcome(app, journal, selected))
    assert submitted.state == "ENTRY_PENDING_FILL"
    partial = replace(
        broker.orders[0],
        status="partially_filled",
        filled_quantity=Decimal("1"),
    )
    position = PositionSnapshot(
        symbol=selected.symbol,
        quantity=Decimal("1"),
        side="long",
        market_value=Decimal("425"),
        average_entry_price=Decimal("4.25"),
        unrealized_pl=Decimal("0"),
    )
    collection = SimpleNamespace(
        positions=(position,),
        open_orders=(partial,),
        reconciliation=ReconciliationReport(
            checked_at=DECISION_AT,
            broker_order_count=1,
            broker_position_count=1,
        ),
    )

    first = handler.reconcile(collection)
    second = handler.reconcile(collection)

    assert first == "POSITION_OPEN"
    assert second == "POSITION_OPEN"
    assert broker.cancel_calls == ["paper-001"]
    lifecycle = journal.position_lifecycle(symbol=selected.symbol)
    assert lifecycle is not None
    assert lifecycle["state"] == "ENTRY_PARTIAL_FILL_CANCEL_PENDING"


def test_definite_rejected_entry_releases_slot_for_later_epoch(tmp_path) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    first = candidate(client_order_id="cajnmnstr-auto-rejected")
    first_result = handler.submit(outcome(app, journal, first))
    assert first_result.submission_attempted is True
    journal.update_broker_order(
        client_order_id=first.client_order_id,
        broker_order_id="paper-001",
        status="rejected",
        payload={"reconciliation_required": False},
    )
    broker.orders = []
    recovered = handler.reconcile(
        SimpleNamespace(
            positions=(),
            open_orders=(),
            reconciliation=ReconciliationReport(
                checked_at=DECISION_AT,
                broker_order_count=0,
                broker_position_count=0,
            ),
        )
    )
    second = candidate(client_order_id="cajnmnstr-auto-later")
    later = handler.submit(outcome(app, journal, second))

    assert recovered == "ENTRY_ABORTED_RECOVERED"
    assert later.state == "ENTRY_PENDING_FILL"
    assert len(broker.submissions) == 2
    assert len(journal.active_position_lifecycles()) == 1


def test_entry_disabled_remains_fail_closed(tmp_path) -> None:
    app = settings(tmp_path, entry=False)
    journal = Journal(app.journal_path)
    journal.initialize()
    broker = FakePaperBroker(journal)
    handler = AutonomousPaperEntryHandler(app, journal, broker, broker)

    result = handler.submit(outcome(app, journal, candidate()))

    assert result.state == "ENTRY_BLOCKED_ENTRY_AUTHORITY_NOT_ARMED"
    assert broker.submissions == []
    assert app.position_management_armed is True


def test_degraded_ai_health_blocks_entry_but_not_management_configuration(
    tmp_path,
) -> None:
    app, journal, broker, handler = setup_handler(tmp_path)
    selected = candidate()
    decision = outcome(app, journal, selected)
    components = tuple(
        replace(item, state=HealthState.DEGRADED) if item.component == "ai_provider" else item
        for item in decision.health.components
    )
    decision.health = replace(
        decision.health,
        state=HealthState.DEGRADED,
        entry_armed=False,
        components=components,
    )

    result = handler.submit(decision)

    assert result.state == "ENTRY_BLOCKED_ENTRY_CRITICAL_HEALTH_BLOCKED"
    assert broker.submissions == []
    assert app.position_management_armed is True
