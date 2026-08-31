from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.journal import Journal
from cajnmnstr.models import (
    AuthorityGrant,
    BrokerOrderSnapshot,
    InvalidationRule,
    MarketClockSnapshot,
    OptionChainSnapshot,
    OrderIntent,
    PositionManagementPlan,
    PositionSnapshot,
    ReconciliationReport,
    RefereeVerdict,
)
from cajnmnstr.position_management import DeterministicPositionManager
from cajnmnstr.services import (
    BrokerReconciler,
    DeterministicReferee,
    OperatorAuthorityPath,
    PaperExecutionCoordinator,
)

NOW = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
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


def plan(
    *,
    stop: Decimal = Decimal("0.20"),
    target: Decimal | None = Decimal("0.30"),
    invalidation_threshold: Decimal = Decimal("-0.01"),
    time_stop_at: datetime = NOW + timedelta(hours=2),
    forced_eod_at: datetime = NOW + timedelta(hours=3),
    maximum_quantity: int = 2,
) -> PositionManagementPlan:
    return PositionManagementPlan(
        plan_id="cajnmnstr-plan-position-fixture",
        entry_passport_id="entry-passport",
        symbol=SYMBOL,
        maximum_quantity=maximum_quantity,
        stop_loss_fraction=stop,
        profit_target_fraction=target,
        invalidation=InvalidationRule(
            feature_name="return_5m",
            comparison="lte",
            threshold=invalidation_threshold,
        ),
        time_stop_at=time_stop_at,
        forced_eod_at=forced_eod_at,
        strategy_version="fixture-v1",
        rationale="Test-only explicit exit values; no production defaults.",
    )


def setup_manager(
    tmp_path,
    *,
    approved_plan: PositionManagementPlan | None = None,
    quantity=Decimal("1"),
):
    app = settings(tmp_path)
    journal = Journal(app.journal_path)
    journal.initialize()
    journal.create_passport("entry-passport", {"source": "fixture"})
    journal.seal_passport("entry-passport", {"source": "fixture", "sealed": True})
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
    feature: Decimal | None = Decimal("0.01"),
    market_open: bool = True,
    reconciled: bool = True,
):
    features = {} if feature is None else {"return_5m": feature}
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
        ({"bid": Decimal("3.10")}, "RISK_STOP"),
        ({"bid": Decimal("5.30"), "ask": Decimal("5.40")}, "PROFIT_TARGET"),
        ({"feature": Decimal("-0.02")}, "THESIS_INVALIDATION"),
        ({"now": NOW + timedelta(hours=2, minutes=1)}, "TIME_STOP"),
        ({"now": NOW + timedelta(hours=3, minutes=1)}, "FORCED_EOD"),
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
    approved = plan(time_stop_at=NOW - timedelta(minutes=1))
    _, _, broker, manager = setup_manager(tmp_path, approved_plan=approved)

    state = manager.run_cycle(collection(broker, feature=None))

    assert state == "EXIT_PENDING_RECONCILIATION"
    assert len(broker.submissions) == 1


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
    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
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

    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "SUBMIT_UNKNOWN_RECONCILIATION_REQUIRED"
    )
    assert journal.has_broker_uncertainty()
    assert broker.submissions == []


def test_definite_submission_failure_is_persistent_and_not_retried(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    broker.submit_error = ValueError("fixture broker rejection")

    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "EXIT_SUBMISSION_FAILED_RECONCILIATION_REQUIRED"
    )
    assert journal.has_broker_uncertainty()
    assert broker.submissions == []


def test_submission_is_not_flat_until_reconciliation_proves_zero(tmp_path) -> None:
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=plan())
    manager.run_cycle(collection(broker, bid=Decimal("3.10")))
    lifecycle = journal.position_lifecycle(symbol=SYMBOL)
    assert lifecycle["state"] == "EXIT_PENDING_RECONCILIATION"

    broker.positions = []
    BrokerReconciler(journal, broker).reconcile()
    assert manager.run_cycle(collection(broker)) == "FLAT"
    assert journal.position_lifecycle(symbol=SYMBOL) is None
    records = journal.broker_order_records()
    assert records[-1]["status"] == "CLOSED_BROKER_FLAT"


def test_broker_mismatch_or_overclose_attempt_fails_without_submission(tmp_path) -> None:
    _, _, broker, manager = setup_manager(
        tmp_path,
        approved_plan=plan(maximum_quantity=1),
        quantity=Decimal("2"),
    )
    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
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

    assert restarted.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
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
        max_limit_price=Decimal("3.10"),
    )
    intent = OrderIntent(
        symbol=SYMBOL,
        quantity=1,
        side="sell",
        limit_price=Decimal("3.10"),
        client_order_id=client_order_id,
        position_intent="sell_to_close",
        passport_id=passport_id,
        decision_bid=Decimal("3.10"),
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
    assert restarted.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "EXIT_PENDING_RECONCILIATION"
    )
    assert len(broker.submissions) == 1


def test_market_closed_keeps_exit_pending_without_submission_or_cancel(tmp_path) -> None:
    approved = plan(forced_eod_at=NOW - timedelta(minutes=1), time_stop_at=NOW - timedelta(hours=1))
    _, journal, broker, manager = setup_manager(tmp_path, approved_plan=approved)

    state = manager.run_cycle(collection(broker, market_open=False))

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
        collection(broker, bid=Decimal("3.10"), reconciled=False)
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

    assert manager.run_cycle(collection(broker, bid=Decimal("3.10"))) == (
        "BROKER_RECONCILIATION_REQUIRED"
    )
    assert broker.cancel_calls == 0
    assert broker.submissions == []
