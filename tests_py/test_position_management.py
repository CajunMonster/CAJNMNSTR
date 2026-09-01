from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.journal import Journal
from cajnmnstr.models import (
    AuthorityGrant,
    BrokerOrderSnapshot,
    MarketClockSnapshot,
    OptionChainSnapshot,
    OrderIntent,
    PositionManagementPlan,
    PositionSnapshot,
    ReconciliationReport,
    RefereeResult,
    RefereeVerdict,
)
from cajnmnstr.position_management import DeterministicPositionManager
from cajnmnstr.position_policy import build_initial_position_plan
from cajnmnstr.services import (
    BrokerReconciler,
    DeterministicReferee,
    OperatorAuthorityPath,
    PaperExecutionCoordinator,
)

NOW = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
FILL_AT = NOW - timedelta(minutes=30)
SYMBOL = "SPY260918C00540000"


def settings(tmp_path) -> Settings:
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
        },
        load_local_file=False,
    )


def position(quantity: Decimal = Decimal("1")) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=SYMBOL,
        quantity=quantity,
        side="long",
        market_value=Decimal("400") * quantity,
        average_entry_price=Decimal("4.00"),
        unrealized_pl=Decimal("0"),
    )


class Broker:
    def __init__(self, journal: Journal, *, quantity: Decimal = Decimal("1")) -> None:
        self.journal = journal
        self.positions = [position(quantity)]
        self.orders: list[BrokerOrderSnapshot] = []
        self.submissions: list[OrderIntent] = []
        self.submit_error: Exception | None = None
        self.fill_quantity = Decimal("0")
        self.cancel_calls = 0

    def list_positions(self):
        return list(self.positions)

    def list_orders(self):
        return list(self.orders)

    def list_open_orders(self):
        return [
            order
            for order in self.orders
            if order.status in {"accepted", "new", "partially_filled"}
        ]

    def submit_limit_order(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        assert intent.side == "sell"
        assert intent.position_intent == "sell_to_close"
        assert self.journal.broker_order_status(intent.client_order_id) == "SUBMISSION_PENDING"
        if self.submit_error is not None:
            raise self.submit_error
        self.submissions.append(intent)
        status = "partially_filled" if self.fill_quantity else "accepted"
        order = BrokerOrderSnapshot(
            broker_order_id="paper-exit-001",
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            status=status,
            quantity=Decimal(intent.quantity),
            filled_quantity=self.fill_quantity,
            filled_avg_price=(
                None if self.fill_quantity == 0 else intent.limit_price
            ),
            limit_price=intent.limit_price,
            submitted_at=NOW,
            updated_at=NOW,
        )
        self.orders.append(order)
        return order

    def cancel_order(self, broker_order_id: str) -> None:
        del broker_order_id
        self.cancel_calls += 1


def entry_passport_payload() -> dict[str, object]:
    return {
        "evidence_snapshot": {
            "decision_at": NOW.isoformat(),
            "features": {
                "underlying_price": "540",
                "vwap": "539",
                "opening_range_low": "538",
                "opening_range_high": "541",
            },
        },
        "terra": {"proposal": {"direction": "LONG_CALL"}},
        "option_selection": {"candidate": {"symbol": SYMBOL}},
        "broker_submission_allowed": False,
    }


def plan(*, maximum_quantity: int = 2) -> PositionManagementPlan:
    referee = RefereeResult(
        result_id="fixture-referee-result",
        passport_id="entry-passport",
        verdict="APPROVE",
        max_quantity=2,
        max_limit_price=Decimal("4.25"),
        reason_code="FIXTURE_ENTRY",
        created_at=NOW,
    )
    return build_initial_position_plan(
        entry_passport_payload(),
        referee,
        plan_id="cajnmnstr-plan-position-fixture",
        entry_passport_id="entry-passport",
        symbol=SYMBOL,
        maximum_quantity=maximum_quantity,
        strategy_version="fixture-v1",
        rationale="Test-only owner-approved initial policy.",
    )


def setup_manager(
    tmp_path,
    *,
    approved_plan: PositionManagementPlan | None = None,
    quantity=Decimal("1"),
    fill_at: datetime = FILL_AT,
    entry_order_quantity: Decimal | None = None,
    entry_filled_quantity: Decimal | None = None,
    entry_status: str = "filled",
):
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    payload = entry_passport_payload()
    journal.create_passport("entry-passport", payload)
    journal.seal_passport("entry-passport", payload)
    DeterministicReferee(journal).issue(
        passport_id="entry-passport",
        verdict=RefereeVerdict.APPROVE,
        reason_code="FIXTURE_ENTRY",
        max_quantity=2,
        max_limit_price=Decimal("4.25"),
    )
    if approved_plan is not None:
        assert journal.register_position_plan(approved_plan)
    broker = Broker(journal, quantity=quantity)
    order_quantity = entry_order_quantity or quantity
    filled_quantity = entry_filled_quantity or quantity
    entry_intent = {
        "symbol": SYMBOL,
        "quantity": int(order_quantity),
        "side": "buy",
        "position_intent": "buy_to_open",
    }
    entry_order = BrokerOrderSnapshot(
        broker_order_id="paper-entry-001",
        client_order_id="cajnmnstr-entry-fixture",
        symbol=SYMBOL,
        status=entry_status,
        quantity=order_quantity,
        filled_quantity=filled_quantity,
        filled_avg_price=Decimal("4.00"),
        limit_price=Decimal("4.00"),
        submitted_at=fill_at - timedelta(minutes=1),
        updated_at=fill_at,
        filled_at=fill_at,
    )
    assert journal.authorize_order_attempt(
        client_order_id=entry_order.client_order_id,
        passport_id="entry-passport",
        payload={"intent": entry_intent},
    )
    journal.update_broker_order(
        client_order_id=entry_order.client_order_id,
        broker_order_id=entry_order.broker_order_id,
        status=entry_order.status,
        payload={"intent": entry_intent, "broker_order": asdict(entry_order)},
    )
    broker.orders.append(entry_order)
    coordinator = PaperExecutionCoordinator(
        app, journal, broker, broker, lambda: pytest.fail("manager must supply exit health")
    )
    authority = OperatorAuthorityPath(
        app, journal, coordinator, lambda: pytest.fail("manager must supply exit health")
    )
    return app, journal, broker, DeterministicPositionManager(app, journal, authority)


def collection(
    broker: Broker,
    *,
    now: datetime = NOW,
    bid: Decimal = Decimal("4.00"),
    ask: Decimal = Decimal("4.10"),
    feature: Decimal | None = Decimal("540"),
    market_open: bool = True,
    reconciled: bool = True,
):
    features = {} if feature is None else {"underlying_price": feature}
    report = ReconciliationReport(
        checked_at=now,
        broker_order_count=len(broker.orders),
        broker_position_count=len(broker.positions),
        unknown_broker_client_ids=(() if reconciled else ("cajnmnstr-unknown",)),
    )
    return SimpleNamespace(
        positions=tuple(broker.positions),
        open_orders=tuple(broker.list_open_orders()),
        reconciliation=report,
        option_chain=(
            OptionChainSnapshot(
                symbol=SYMBOL,
                bid_price=bid,
                ask_price=ask,
                bid_size=Decimal("10"),
                ask_size=Decimal("10"),
                quote_at=now - timedelta(seconds=1),
                trade_price=bid,
                trade_at=now - timedelta(seconds=1),
                implied_volatility=Decimal("0.20"),
                delta=Decimal("0.50"),
                gamma=Decimal("0.03"),
                rho=Decimal("0.02"),
                theta=Decimal("-0.10"),
                vega=Decimal("0.15"),
                feed="opra",
            ),
        ),
        clock=MarketClockSnapshot(
            timestamp=now,
            is_open=market_open,
            next_open=now + timedelta(days=1),
            next_close=now + timedelta(hours=3),
        ),
        snapshot=SimpleNamespace(decision_at=now, features=features),
    )


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"bid": Decimal("3.00")}, "RISK_STOP"),
        ({"bid": Decimal("5.40"), "ask": Decimal("5.50")}, "PROFIT_TARGET"),
        ({"feature": Decimal("539")}, "THESIS_INVALIDATION"),
        ({"now": FILL_AT + timedelta(minutes=76)}, "TIME_STOP"),
        ({"now": NOW.replace(hour=19, minute=36)}, "FORCED_EOD"),
    ],
)
def test_all_deterministic_exit_conditions_submit_sell_to_close(
    tmp_path, kwargs, expected_reason
) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    state = manager.run_cycle(collection(broker, **kwargs))

    assert state == "EXIT_PENDING_RECONCILIATION"
    assert len(broker.submissions) == 1
    assert broker.submissions[0].side == "sell"
    assert broker.submissions[0].position_intent == "sell_to_close"
    exit_passports = [
        event
        for event in journal.list_events()
        if event["payload"].get("reason_code") == expected_reason
    ]
    assert exit_passports


def test_ai_and_stale_analytical_input_do_not_trap_time_exit(tmp_path) -> None:
    _, _, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    state = manager.run_cycle(
        collection(broker, now=FILL_AT + timedelta(minutes=76), feature=None)
    )

    assert state == "EXIT_PENDING_RECONCILIATION"
    assert len(broker.submissions) == 1


def test_time_stop_is_bound_once_to_confirmed_fill_plus_75_minutes(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    assert manager.run_cycle(collection(broker, now=NOW)) == "POSITION_MONITORING"
    lifecycle = journal.position_lifecycle(symbol=SYMBOL)["lifecycle"]
    assert datetime.fromisoformat(lifecycle["fill_confirmed_at"]) == FILL_AT
    assert datetime.fromisoformat(lifecycle["time_stop_at"]) == FILL_AT + timedelta(minutes=75)


def test_delayed_fill_does_not_consume_time_stop_before_fill(tmp_path) -> None:
    delayed_fill = NOW + timedelta(minutes=60)
    app, journal, broker, manager = setup_manager(
        tmp_path,
        approved_plan=plan(),
        fill_at=delayed_fill,
    )
    broker.positions = []
    assert manager.run_cycle(collection(broker, now=NOW)) == "FLAT"
    assert journal.position_lifecycle(symbol=SYMBOL)["lifecycle"]["time_stop_at"] is None

    broker.positions = [position()]
    assert manager.run_cycle(collection(broker, now=delayed_fill)) == "POSITION_MONITORING"
    anchored = journal.position_lifecycle(symbol=SYMBOL)["lifecycle"]
    assert datetime.fromisoformat(anchored["time_stop_at"]) == delayed_fill + timedelta(minutes=75)

    restarted = DeterministicPositionManager(app, journal, manager.authority)
    assert restarted.run_cycle(
        collection(broker, now=delayed_fill + timedelta(minutes=74))
    ) == "POSITION_MONITORING"
    assert restarted.run_cycle(
        collection(broker, now=delayed_fill + timedelta(minutes=76))
    ) == "EXIT_PENDING_RECONCILIATION"


def test_partial_entry_fill_anchors_once_and_exit_never_overcloses(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(
        tmp_path,
        approved_plan=plan(),
        quantity=Decimal("1"),
        entry_order_quantity=Decimal("2"),
        entry_filled_quantity=Decimal("1"),
        entry_status="partially_filled",
    )
    assert manager.run_cycle(collection(broker, now=NOW)) == (
        "BROKER_RECONCILIATION_REQUIRED"
    )
    anchored = journal.position_lifecycle(symbol=SYMBOL)["lifecycle"]
    assert datetime.fromisoformat(anchored["fill_confirmed_at"]) == FILL_AT
    assert anchored["initial_confirmed_quantity"] == "1"

    completed = replace(broker.orders[0], status="canceled", updated_at=NOW)
    broker.orders[0] = completed
    journal.update_broker_order(
        client_order_id=completed.client_order_id,
        broker_order_id=completed.broker_order_id,
        status=completed.status,
        payload={"broker_order": asdict(completed)},
    )
    assert manager.run_cycle(
        collection(broker, now=FILL_AT + timedelta(minutes=76))
    ) == "EXIT_PENDING_RECONCILIATION"
    assert broker.submissions[-1].quantity == 1


def test_missing_invalidation_feature_fails_loud_but_keeps_other_guards_active(
    tmp_path,
) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    assert manager.run_cycle(collection(broker, feature=None)) == (
        "POSITION_MONITORING_DEGRADED"
    )
    assert broker.submissions == []
    incidents = journal.list_events()
    monitor = [
        event
        for event in incidents
        if event["payload"].get("state") == "POSITION_MONITORING"
    ][-1]
    assert monitor["payload"]["thesis_invalidation_available"] is False


def test_partial_fill_remains_pending_and_never_overcloses(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(
        tmp_path,
        approved_plan=plan(),
        quantity=Decimal("2"),
    )
    broker.fill_quantity = Decimal("1")
    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "EXIT_PENDING_RECONCILIATION"
    )
    broker.positions = [position(Decimal("1"))]
    reconciled = BrokerReconciler(journal, broker).reconcile()

    assert not reconciled.matched
    assert manager.run_cycle(collection(broker, reconciled=False)) == (
        "EXIT_PENDING_RECONCILIATION"
    )
    assert len(broker.submissions) == 1
    assert broker.submissions[0].quantity == 2


def test_submit_timeout_is_unknown_and_never_blindly_retried(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    broker.submit_error = TimeoutError("fixture transport timeout")

    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    )
    assert journal.has_broker_uncertainty()
    assert broker.submissions == []


def test_definite_submission_failure_is_persistent_and_not_retried(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    broker.submit_error = ValueError("fixture broker rejection")

    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
    )
    assert journal.has_broker_uncertainty()
    assert broker.submissions == []


def test_submission_is_not_flat_until_reconciliation_proves_zero(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    manager.run_cycle(collection(broker, bid=Decimal("3.00")))
    lifecycle = journal.position_lifecycle(symbol=SYMBOL)
    assert lifecycle["state"] == "EXIT_PENDING_RECONCILIATION"

    broker.positions = []
    journal.open_incident(
        component="autonomous_entry",
        severity="CRITICAL",
        state="PAUSED",
        message="The entry order is filled but the broker position is not yet reconciled.",
        protective_action="Reconcile.",
    )
    BrokerReconciler(journal, broker).reconcile()
    assert manager.run_cycle(collection(broker)) == "FLAT"
    assert journal.position_lifecycle(symbol=SYMBOL) is None
    records = journal.broker_order_records()
    assert records[-1]["status"] == "CLOSED_BROKER_FLAT"
    closed = journal.all_position_lifecycles()[0]
    assert closed["lifecycle"]["broker_flat_verified"] is True
    assert closed["lifecycle"]["reconciliation_required"] is False
    assert closed["lifecycle"]["submission_status"] == "CLOSED_BROKER_FLAT"
    assert journal.unresolved_incidents("autonomous_entry") == []


def test_legacy_closed_lifecycle_snapshot_is_normalized_without_deleting_history(
    tmp_path,
) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    manager.run_cycle(collection(broker, bid=Decimal("3.00")))
    broker.positions = []
    BrokerReconciler(journal, broker).reconcile()
    assert manager.run_cycle(collection(broker)) == "FLAT"
    before_events = len(journal.list_events())
    lifecycle = journal.all_position_lifecycles()[0]
    with journal._connect() as connection:
        stale = dict(lifecycle["lifecycle"])
        stale.update({"reconciliation_required": True, "submission_status": "pending_new"})
        connection.execute(
            "UPDATE position_lifecycles SET broker_quantity = '2', lifecycle_json = ?",
            (json.dumps(stale, sort_keys=True),),
        )

    normalized = journal.normalize_closed_position_lifecycles()

    assert normalized == [lifecycle["plan_id"]]
    repaired = journal.all_position_lifecycles()[0]
    assert repaired["broker_quantity"] == Decimal("0")
    assert repaired["lifecycle"]["reconciliation_required"] is False
    assert repaired["lifecycle"]["submission_status"] == "CLOSED_BROKER_FLAT"
    assert len(journal.list_events()) == before_events


def test_broker_mismatch_or_overclose_attempt_fails_without_submission(tmp_path) -> None:
    _, _, broker, manager = setup_manager(
        tmp_path,
        approved_plan=plan(maximum_quantity=1),
        quantity=Decimal("2"),
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "BROKER_POSITION_MISMATCH"
    )
    assert broker.submissions == []


def test_restart_recovers_open_position_from_durable_plan(tmp_path) -> None:
    app, journal, broker, _ = setup_manager(tmp_path, approved_plan=plan())
    coordinator = PaperExecutionCoordinator(
        app, journal, broker, broker, lambda: pytest.fail("manager supplies health")
    )
    restarted = DeterministicPositionManager(
        app,
        journal,
        OperatorAuthorityPath(
            app, journal, coordinator, lambda: pytest.fail("manager supplies health")
        ),
    )

    assert restarted.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "EXIT_PENDING_RECONCILIATION"
    )
    assert len(broker.submissions) == 1


def test_restart_resumes_exact_durable_exit_authority_before_submission(tmp_path) -> None:
    approved = plan()
    app, journal, broker, manager = setup_manager(tmp_path, approved_plan=approved)
    passport_id, client_order_id = manager._exit_identity(approved)
    journal.create_passport(passport_id, {"source": "fixture-exit"})
    journal.seal_passport(passport_id, {"source": "fixture-exit", "sealed": True})
    referee = DeterministicReferee(journal).issue(
        passport_id=passport_id,
        verdict=RefereeVerdict.EXIT,
        reason_code="RISK_STOP",
        max_quantity=1,
        max_limit_price=Decimal("3.00"),
    )
    intent = OrderIntent(
        symbol=SYMBOL,
        quantity=1,
        side="sell",
        limit_price=Decimal("3.00"),
        client_order_id=client_order_id,
        position_intent="sell_to_close",
        passport_id=passport_id,
        decision_bid=Decimal("3.00"),
        decision_ask=Decimal("4.10"),
        quote_at=NOW - timedelta(seconds=1),
    )
    assert journal.authorize_order_attempt(
        client_order_id=client_order_id,
        passport_id=passport_id,
        payload={
            "passport_id": passport_id,
            "referee_result_id": referee.result_id,
            "verdict": "EXIT",
            "authority_granted": AuthorityGrant.POSITION_MANAGEMENT.value,
            "intent": asdict(intent),
        },
    )

    restarted = DeterministicPositionManager(
        app,
        journal,
        manager.authority,
    )
    assert restarted.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "EXIT_PENDING_RECONCILIATION"
    )
    assert len(broker.submissions) == 1


def test_market_closed_keeps_exit_pending_without_submission_or_cancel(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    state = manager.run_cycle(
        collection(broker, now=NOW.replace(hour=19, minute=36), market_open=False)
    )

    assert state == "EXIT_PENDING_MARKET_SESSION"
    assert broker.submissions == []
    assert broker.cancel_calls == 0
    assert journal.position_lifecycle(symbol=SYMBOL)["state"] == (
        "EXIT_CONDITION_PENDING_MARKET"
    )


def test_position_without_plan_is_persistent_critical(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=None)

    assert manager.run_cycle(collection(broker)) == "POSITION_PLAN_MISSING"
    assert broker.submissions == []
    incidents = journal.list_events()
    assert incidents[-1]["severity"] == "CRITICAL"


def test_unmatched_broker_state_blocks_exit_submission(tmp_path) -> None:
    _, _, broker, manager = setup_manager(tmp_path, approved_plan=plan())

    state = manager.run_cycle(
        collection(broker, bid=Decimal("3.00"), reconciled=False)
    )

    assert state == "BROKER_RECONCILIATION_REQUIRED"
    assert broker.submissions == []


def test_unknown_open_order_is_not_canceled_or_replaced_blindly(tmp_path) -> None:
    _, _, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    broker.orders.append(
        BrokerOrderSnapshot(
            broker_order_id="unknown-open-order",
            client_order_id="unrelated-client-order",
            symbol=SYMBOL,
            status="accepted",
            quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            filled_avg_price=None,
            limit_price=Decimal("3.10"),
            submitted_at=NOW,
            updated_at=NOW,
        )
    )

    assert manager.run_cycle(collection(broker, bid=Decimal("3.00"))) == (
        "BROKER_RECONCILIATION_REQUIRED"
    )
    assert broker.cancel_calls == 0
    assert broker.submissions == []
