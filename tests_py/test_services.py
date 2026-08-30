import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, Settings
from cajnmnstr.errors import (
    AuthorityDeniedError,
    DuplicateOrderIdentityError,
    ExecutionDisabledError,
    InvalidRefereeResultError,
)
from cajnmnstr.health import (
    ENTRY_CRITICAL_COMPONENTS,
    EXIT_CRITICAL_COMPONENTS,
    ComponentHealth,
    HealthReport,
)
from cajnmnstr.journal import Journal
from cajnmnstr.models import (
    BrokerOrderSnapshot,
    EventType,
    HealthState,
    OrderCandidate,
    OrderIntent,
    PositionSnapshot,
    RefereeVerdict,
)
from cajnmnstr.services import (
    BrokerReconciler,
    DeterministicReferee,
    OperatorAuthorityPath,
    PaperExecutionCoordinator,
)


class MockBroker:
    def __init__(
        self,
        journal: Journal,
        positions: list[PositionSnapshot] | None = None,
    ) -> None:
        self.journal = journal
        self.submissions: list[OrderIntent] = []
        self.positions = positions if positions is not None else []
        self.orders: list[BrokerOrderSnapshot] = []
        self.submit_error: Exception | None = None
        self.fill_price: Decimal | None = None

    def submit_limit_order(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        assert self.journal.broker_order_status(intent.client_order_id) == "SUBMISSION_PENDING"
        if self.submit_error is not None:
            raise self.submit_error
        self.submissions.append(intent)
        now = datetime.now(UTC)
        order = BrokerOrderSnapshot(
            broker_order_id=f"mock-broker-{len(self.submissions):03d}",
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            status="filled" if self.fill_price is not None else "accepted",
            quantity=Decimal(intent.quantity),
            filled_quantity=(
                Decimal(intent.quantity) if self.fill_price is not None else Decimal("0")
            ),
            filled_avg_price=self.fill_price,
            limit_price=intent.limit_price,
            submitted_at=now,
            updated_at=now,
        )
        self.orders.append(order)
        return order

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot:
        raise AssertionError(f"Duplicate lookup must not occur: {client_order_id}")

    def list_positions(self) -> list[PositionSnapshot]:
        return list(self.positions)

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        return list(self.orders)


def verified_position(*, quantity: Decimal = Decimal("2")) -> PositionSnapshot:
    return PositionSnapshot(
        symbol="SPY260918C00540000",
        quantity=quantity,
        side="long",
        market_value=Decimal("850"),
        average_entry_price=Decimal("4.25"),
        unrealized_pl=Decimal("0"),
    )


def settings(
    tmp_path: Path,
    *,
    entry_enabled: bool = True,
    position_management_enabled: bool = True,
    broker_lock: bool = False,
) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "ALPACA_API_KEY": "mock-paper-key",
            "ALPACA_SECRET_KEY": "mock-paper-secret",
            "CAJNMNSTR_ENTRY_ENABLED": "true" if entry_enabled else "false",
            "CAJNMNSTR_POSITION_MANAGEMENT_ENABLED": (
                "true" if position_management_enabled else "false"
            ),
            "CAJNMNSTR_BROKER_LOCK": "true" if broker_lock else "false",
            "CAJNMNSTR_EXECUTION_CONFIRMATION": EXECUTION_CONFIRMATION,
        },
        load_local_file=False,
    )


def candidate(
    *,
    quantity: int = 2,
    client_order_id: str = "cajnmnstr-mock-001",
    side: str = "buy",
    position_intent: str = "buy_to_open",
    limit_price: Decimal = Decimal("4.25"),
) -> OrderCandidate:
    return OrderCandidate(
        symbol="SPY260918C00540000",
        quantity=quantity,
        side=side,
        limit_price=limit_price,
        client_order_id=client_order_id,
        position_intent=position_intent,
        decision_bid=Decimal("4.20"),
        decision_ask=Decimal("4.30"),
        quote_at=datetime.now(UTC),
    )


def authority_fixture(
    tmp_path: Path,
    *,
    verdict: RefereeVerdict = RefereeVerdict.APPROVE,
    max_quantity: int | None = 2,
    max_limit_price: Decimal | None = Decimal("4.25"),
    health: HealthReport | HealthState | None = None,
    entry_enabled: bool = True,
    position_management_enabled: bool = True,
    broker_lock: bool = False,
    positions: list[PositionSnapshot] | None = None,
    referee_reason: str | None = None,
) -> tuple[Journal, MockBroker, PaperExecutionCoordinator, OperatorAuthorityPath]:
    configured = settings(
        tmp_path,
        entry_enabled=entry_enabled,
        position_management_enabled=position_management_enabled,
        broker_lock=broker_lock,
    )
    journal = Journal(configured.journal_path)
    journal.initialize()
    journal.create_passport("passport-001", {"symbol": "SPY"})
    journal.seal_passport("passport-001", {"symbol": "SPY", "sealed": True})
    DeterministicReferee(journal).issue(
        passport_id="passport-001",
        verdict=verdict,
        reason_code=referee_reason or f"MOCK_{verdict.value}",
        max_quantity=max_quantity,
        max_limit_price=max_limit_price,
    )
    resolved_positions = positions
    if resolved_positions is None:
        resolved_positions = [verified_position()] if verdict is RefereeVerdict.EXIT else []
    broker = MockBroker(journal, resolved_positions)

    resolved_health = health if health is not None else detailed_health()

    def current_health() -> HealthReport | HealthState:
        return resolved_health

    coordinator = PaperExecutionCoordinator(
        configured, journal, broker, broker, current_health
    )
    authority = OperatorAuthorityPath(
        configured, journal, coordinator, current_health
    )
    return journal, broker, coordinator, authority


def detailed_health(**overrides: HealthState) -> HealthReport:
    states = {
        "configuration": HealthState.HEALTHY,
        "evidence_store": HealthState.HEALTHY,
        "alpaca": HealthState.HEALTHY,
        "broker_state": HealthState.HEALTHY,
        "broker_reconciliation": HealthState.HEALTHY,
        "market_session": HealthState.HEALTHY,
        "spy_quote": HealthState.HEALTHY,
        "option_quote": HealthState.HEALTHY,
        "risk_limits": HealthState.HEALTHY,
        "ai_provider": HealthState.HEALTHY,
        "news": HealthState.HEALTHY,
        "event_calendar": HealthState.HEALTHY,
    }
    states.update(overrides)
    checked_at = datetime.now(UTC)
    components = tuple(
        ComponentHealth(
            component=name,
            state=state,
            message=f"Mock {name} is {state.value}",
            protective_action="Mock protective action.",
            checked_at=checked_at,
        )
        for name, state in states.items()
    )
    aggregate = (
        HealthState.PAUSED
        if HealthState.PAUSED in states.values()
        else (
            HealthState.DEGRADED
            if HealthState.DEGRADED in states.values()
            else HealthState.HEALTHY
        )
    )
    return HealthReport(
        state=aggregate,
        components=components,
        checked_at=checked_at,
        entry_armed=all(
            states[component] is HealthState.HEALTHY
            for component in ENTRY_CRITICAL_COMPONENTS
        ),
        position_management_armed=all(
            states[component] is HealthState.HEALTHY
            for component in EXIT_CRITICAL_COMPONENTS
        ),
        broker_lock_active=False,
    )


def last_transition(journal: Journal) -> dict[str, object]:
    events = journal.list_events(EventType.AUTHORITY_TRANSITION)
    assert events
    payload = events[-1]["payload"]
    assert set(payload) == {
        "passport_id",
        "verdict",
        "authority_granted",
        "execution_allowed",
        "reason_code",
        "mock_broker_result",
    }
    return payload


def test_valid_approve_passes_within_limits(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)

    result = authority.execute(passport_id="passport-001", candidate=candidate())

    assert result.broker_order_id == "mock-broker-001"
    assert len(broker.submissions) == 1
    assert broker.submissions[0].quantity == 2
    transition = last_transition(journal)
    assert transition["verdict"] == "APPROVE"
    assert transition["authority_granted"] == "ENTRY_FULL"
    assert transition["execution_allowed"] is True
    assert transition["mock_broker_result"]["status"] == "accepted"


def test_valid_reduce_passes_only_with_reduced_size(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.REDUCE,
        max_quantity=1,
    )

    authority.execute(passport_id="passport-001", candidate=candidate(quantity=2))

    assert len(broker.submissions) == 1
    assert broker.submissions[0].quantity == 1
    transition = last_transition(journal)
    assert transition["authority_granted"] == "ENTRY_REDUCED"
    assert transition["execution_allowed"] is True


@pytest.mark.parametrize(
    "order_candidate",
    [candidate(quantity=3), candidate(limit_price=Decimal("4.26"))],
)
def test_approve_outside_limits_never_submits(
    tmp_path: Path, order_candidate: OrderCandidate
) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)

    with pytest.raises(AuthorityDeniedError):
        authority.execute(passport_id="passport-001", candidate=order_candidate)

    assert broker.submissions == []
    transition = last_transition(journal)
    assert transition["reason_code"] in {
        "QUANTITY_LIMIT_EXCEEDED",
        "PREMIUM_LIMIT_EXCEEDED",
    }


@pytest.mark.parametrize("verdict", [RefereeVerdict.ABSTAIN, RefereeVerdict.BLOCK])
def test_no_authority_verdicts_never_submit(
    tmp_path: Path, verdict: RefereeVerdict
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=verdict,
        max_quantity=None,
        max_limit_price=None,
    )

    with pytest.raises(AuthorityDeniedError, match="grants no order authority"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    transition = last_transition(journal)
    assert transition["verdict"] == verdict.value
    assert transition["authority_granted"] == "NONE"
    assert transition["execution_allowed"] is False


def test_paused_or_stale_health_never_submits(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        health=HealthState.PAUSED,
    )

    with pytest.raises(ExecutionDisabledError, match="requires HEALTHY"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "SYSTEM_PAUSED"


def test_aggregate_healthy_without_reconciliation_detail_fails_closed(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=HealthState.HEALTHY,
    )
    with pytest.raises(ExecutionDisabledError, match="Component-level"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-aggregate-health-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "EXIT_HEALTH_DETAIL_REQUIRED"


@pytest.mark.parametrize("ai_state", [HealthState.DEGRADED, HealthState.PAUSED])
def test_ai_failure_blocks_entry_but_not_existing_position_exit(
    tmp_path: Path,
    ai_state: HealthState,
) -> None:
    health = detailed_health(ai_provider=ai_state)
    entry_journal, entry_broker, _, entry_authority = authority_fixture(
        tmp_path / "entry",
        health=health,
    )
    with pytest.raises(ExecutionDisabledError, match="entry-critical"):
        entry_authority.execute(passport_id="passport-001", candidate=candidate())
    assert entry_broker.submissions == []
    assert last_transition(entry_journal)["reason_code"] == f"SYSTEM_{ai_state.value}"

    exit_journal, exit_broker, _, exit_authority = authority_fixture(
        tmp_path / "exit",
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
    )
    exit_authority.execute(
        passport_id="passport-001",
        candidate=candidate(
            quantity=1,
            client_order_id="cajnmnstr-ai-independent-exit",
            side="sell",
            position_intent="sell_to_close",
        ),
    )
    assert len(exit_broker.submissions) == 1
    assert last_transition(exit_journal)["authority_granted"] == "POSITION_MANAGEMENT"


def test_stale_option_quote_blocks_entry_and_keeps_exit_pending(tmp_path: Path) -> None:
    health = detailed_health(option_quote=HealthState.PAUSED)
    entry_journal, entry_broker, _, entry_authority = authority_fixture(
        tmp_path / "entry",
        health=health,
    )
    with pytest.raises(ExecutionDisabledError):
        entry_authority.execute(passport_id="passport-001", candidate=candidate())
    assert entry_broker.submissions == []
    assert last_transition(entry_journal)["reason_code"] == "SYSTEM_PAUSED"

    exit_journal, exit_broker, _, exit_authority = authority_fixture(
        tmp_path / "exit",
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
    )
    exit_candidate = candidate(
        quantity=1,
        client_order_id="cajnmnstr-stale-quote-exit",
        side="sell",
        position_intent="sell_to_close",
    )
    with pytest.raises(ExecutionDisabledError, match="remains pending"):
        exit_authority.execute(passport_id="passport-001", candidate=exit_candidate)
    assert exit_broker.submissions == []
    assert exit_journal.broker_order_status(exit_candidate.client_order_id) is None
    assert last_transition(exit_journal)["reason_code"] == "EXIT_PENDING_OPTION_QUOTE"


def test_missing_exit_critical_health_component_fails_closed(tmp_path: Path) -> None:
    complete = detailed_health()
    incomplete = HealthReport(
        state=HealthState.HEALTHY,
        components=tuple(
            component
            for component in complete.components
            if component.component != "broker_reconciliation"
        ),
        checked_at=complete.checked_at,
        entry_armed=True,
        position_management_armed=True,
        broker_lock_active=False,
    )
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=incomplete,
    )
    with pytest.raises(ExecutionDisabledError, match="reconciled Alpaca broker state"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-incomplete-health-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "EXIT_RECONCILIATION_REQUIRED"


def test_uncertain_broker_state_requires_reconciliation_before_exit(
    tmp_path: Path,
) -> None:
    health = detailed_health(broker_state=HealthState.PAUSED)
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
    )
    exit_candidate = candidate(
        quantity=1,
        client_order_id="cajnmnstr-reconcile-before-exit",
        side="sell",
        position_intent="sell_to_close",
    )
    with pytest.raises(ExecutionDisabledError, match="reconciled Alpaca broker state"):
        authority.execute(passport_id="passport-001", candidate=exit_candidate)
    assert broker.submissions == []
    assert journal.broker_order_status(exit_candidate.client_order_id) is None
    assert last_transition(journal)["reason_code"] == "EXIT_RECONCILIATION_REQUIRED"


@pytest.mark.parametrize("session_state", [HealthState.DEGRADED, HealthState.PAUSED])
def test_closed_or_halted_market_retains_pending_exit_without_false_fill(
    tmp_path: Path,
    session_state: HealthState,
) -> None:
    health = detailed_health(market_session=session_state)
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
    )
    exit_candidate = candidate(
        quantity=1,
        client_order_id="cajnmnstr-market-pending-exit",
        side="sell",
        position_intent="sell_to_close",
    )
    with pytest.raises(ExecutionDisabledError, match="remains pending"):
        authority.execute(passport_id="passport-001", candidate=exit_candidate)
    assert broker.submissions == []
    assert journal.broker_order_status(exit_candidate.client_order_id) is None
    assert journal.get_referee_result("passport-001").verdict == "EXIT"
    assert last_transition(journal)["reason_code"] == "EXIT_PENDING_MARKET_SESSION"


def test_daily_loss_lock_blocks_entry_but_preserves_exit_authority(tmp_path: Path) -> None:
    health = detailed_health(risk_limits=HealthState.PAUSED)
    entry_journal, entry_broker, _, entry_authority = authority_fixture(
        tmp_path / "entry",
        health=health,
    )
    with pytest.raises(ExecutionDisabledError):
        entry_authority.execute(passport_id="passport-001", candidate=candidate())
    assert entry_broker.submissions == []
    assert last_transition(entry_journal)["reason_code"] == "SYSTEM_PAUSED"

    exit_journal, exit_broker, _, exit_authority = authority_fixture(
        tmp_path / "exit",
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
    )
    exit_authority.execute(
        passport_id="passport-001",
        candidate=candidate(
            quantity=1,
            client_order_id="cajnmnstr-loss-lock-exit",
            side="sell",
            position_intent="sell_to_close",
        ),
    )
    assert len(exit_broker.submissions) == 1
    assert last_transition(exit_journal)["execution_allowed"] is True


def test_forced_eod_exit_does_not_depend_on_ai_availability(tmp_path: Path) -> None:
    health = detailed_health(ai_provider=HealthState.PAUSED)
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        health=health,
        referee_reason="FORCED_EOD",
    )
    authority.execute(
        passport_id="passport-001",
        candidate=candidate(
            quantity=1,
            client_order_id="cajnmnstr-forced-eod-exit",
            side="sell",
            position_intent="sell_to_close",
        ),
    )
    assert len(broker.submissions) == 1
    assert journal.get_referee_result("passport-001").reason_code == "FORCED_EOD"


def test_duplicate_client_order_identity_is_rejected(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)
    order_candidate = candidate()
    authority.execute(passport_id="passport-001", candidate=order_candidate)

    with pytest.raises(DuplicateOrderIdentityError, match="already exists"):
        authority.execute(passport_id="passport-001", candidate=order_candidate)

    assert len(broker.submissions) == 1
    assert last_transition(journal)["reason_code"] == "DUPLICATE_CLIENT_ORDER_ID"


def test_missing_passport_is_rejected_and_journaled(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    journal = Journal(configured.journal_path)
    journal.initialize()
    broker = MockBroker(journal)
    coordinator = PaperExecutionCoordinator(
        configured, journal, broker, broker, lambda: HealthState.HEALTHY
    )
    authority = OperatorAuthorityPath(
        configured, journal, coordinator, lambda: HealthState.HEALTHY
    )

    with pytest.raises(AuthorityDeniedError, match="sealed Evidence Passport"):
        authority.execute(passport_id="passport-missing", candidate=candidate())

    assert broker.submissions == []
    transition = last_transition(journal)
    assert transition["passport_id"] == "passport-missing"
    assert transition["reason_code"] == "PASSPORT_MISSING"


def test_unsealed_passport_is_rejected(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    journal = Journal(configured.journal_path)
    journal.initialize()
    journal.create_passport("passport-001", {"symbol": "SPY"})
    broker = MockBroker(journal)
    coordinator = PaperExecutionCoordinator(
        configured, journal, broker, broker, lambda: HealthState.HEALTHY
    )
    authority = OperatorAuthorityPath(
        configured, journal, coordinator, lambda: HealthState.HEALTHY
    )

    with pytest.raises(AuthorityDeniedError, match="sealed Evidence Passport"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "PASSPORT_UNSEALED"


def test_missing_referee_result_is_rejected(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    journal = Journal(configured.journal_path)
    journal.initialize()
    journal.create_passport("passport-001", {"symbol": "SPY"})
    journal.seal_passport("passport-001", {"symbol": "SPY", "sealed": True})
    broker = MockBroker(journal)
    coordinator = PaperExecutionCoordinator(
        configured, journal, broker, broker, lambda: HealthState.HEALTHY
    )
    authority = OperatorAuthorityPath(
        configured, journal, coordinator, lambda: HealthState.HEALTHY
    )

    with pytest.raises(InvalidRefereeResultError, match="Referee result"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "REFEREE_RESULT_MISSING"


def test_malformed_referee_verdict_is_rejected(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    journal = Journal(configured.journal_path)
    journal.initialize()
    journal.create_passport("passport-001", {"symbol": "SPY"})
    journal.seal_passport("passport-001", {"symbol": "SPY", "sealed": True})
    journal.record_referee_result(
        passport_id="passport-001",
        verdict="MALFORMED",
        max_quantity=1,
        max_limit_price="4.25",
        reason_code="CORRUPT_FIXTURE",
        payload={"fixture": "malformed"},
    )
    broker = MockBroker(journal)
    coordinator = PaperExecutionCoordinator(
        configured, journal, broker, broker, lambda: HealthState.HEALTHY
    )
    authority = OperatorAuthorityPath(
        configured, journal, coordinator, lambda: HealthState.HEALTHY
    )

    with pytest.raises(InvalidRefereeResultError, match="Malformed"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "REFEREE_VERDICT_INVALID"


def test_entry_disabled_with_no_position_never_submits(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        entry_enabled=False,
        positions=[],
    )

    with pytest.raises(ExecutionDisabledError, match="New-entry authority is disabled"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "ENTRY_AUTHORITY_DISABLED"


def test_entry_disabled_with_verified_position_allows_deterministic_exit(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        entry_enabled=False,
    )
    authority.execute(
        passport_id="passport-001",
        candidate=candidate(
            quantity=1,
            client_order_id="cajnmnstr-entry-disabled-exit",
            side="sell",
            position_intent="sell_to_close",
        ),
    )
    assert len(broker.submissions) == 1
    assert last_transition(journal)["authority_granted"] == "POSITION_MANAGEMENT"


def test_position_management_disabled_blocks_exit_and_persists_critical_incident(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        position_management_enabled=False,
    )
    with pytest.raises(ExecutionDisabledError, match="Position-management authority"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-pm-disabled-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "POSITION_MANAGEMENT_DISABLED"
    with sqlite3.connect(journal.path) as connection:
        incident = connection.execute(
            """SELECT severity, component FROM health_incidents
            WHERE resolved_at IS NULL ORDER BY opened_at DESC LIMIT 1"""
        ).fetchone()
    assert incident == ("CRITICAL", "position_management_authority")


@pytest.mark.parametrize(
    ("verdict", "order_candidate"),
    [
        (RefereeVerdict.APPROVE, candidate()),
        (
            RefereeVerdict.EXIT,
            candidate(
                quantity=1,
                client_order_id="cajnmnstr-locked-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        ),
    ],
)
def test_hard_broker_lock_blocks_all_submissions(
    tmp_path: Path,
    verdict: RefereeVerdict,
    order_candidate: OrderCandidate,
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=verdict,
        max_quantity=1 if verdict is RefereeVerdict.EXIT else 2,
        broker_lock=True,
    )
    with pytest.raises(ExecutionDisabledError, match="broker lock is active"):
        authority.execute(passport_id="passport-001", candidate=order_candidate)
    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "BROKER_LOCK_ACTIVE"


def test_position_management_requires_verified_existing_position(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        entry_enabled=False,
        positions=[],
    )
    with pytest.raises(AuthorityDeniedError, match="verified existing long position"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-no-position-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []
    incidents = journal.list_events(EventType.INCIDENT)
    assert incidents[-1]["severity"] == "CRITICAL"
    assert incidents[-1]["payload"]["component"] == "position_management_position"


def test_position_management_cannot_sell_more_than_verified_position(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=3,
        entry_enabled=False,
        positions=[verified_position(quantity=Decimal("2"))],
    )
    with pytest.raises(AuthorityDeniedError, match="sufficient quantity"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=3,
                client_order_id="cajnmnstr-over-close-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []


def test_position_management_intent_cannot_use_an_exposure_increasing_side() -> None:
    with pytest.raises(ValueError, match="risk-valid"):
        candidate(
            quantity=1,
            client_order_id="cajnmnstr-invalid-close-side",
            side="buy",
            position_intent="sell_to_close",
        )


def test_direct_coordinator_call_without_operator_authorization_fails_closed(
    tmp_path: Path,
) -> None:
    journal, broker, coordinator, _ = authority_fixture(tmp_path)
    direct_intent = OrderIntent(
        symbol="SPY260918C00540000",
        quantity=1,
        side="buy",
        limit_price=Decimal("4.25"),
        client_order_id="cajnmnstr-direct-bypass",
        position_intent="buy_to_open",
        passport_id="passport-001",
        decision_bid=Decimal("4.20"),
        decision_ask=Decimal("4.30"),
        quote_at=datetime.now(UTC),
    )

    with pytest.raises(AuthorityDeniedError, match="durable authorization"):
        coordinator.submit(direct_intent)

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "COORDINATOR_AUTHORIZATION_MISSING"


def test_exit_allows_position_management_and_rejects_new_entry(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
    )

    with pytest.raises(AuthorityDeniedError, match="position management only"):
        authority.execute(passport_id="passport-001", candidate=candidate(quantity=1))

    authority.execute(
        passport_id="passport-001",
        candidate=candidate(
            quantity=1,
            client_order_id="cajnmnstr-mock-exit",
            side="sell",
            position_intent="sell_to_close",
        ),
    )

    assert len(broker.submissions) == 1
    assert broker.submissions[0].position_intent == "sell_to_close"
    assert last_transition(journal)["authority_granted"] == "POSITION_MANAGEMENT"


def test_fill_records_shadow_execution_quality_against_decision_quote(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)
    broker.fill_price = Decimal("4.28")

    authority.execute(passport_id="passport-001", candidate=candidate())

    record = journal.broker_order_records()[0]
    quality = record["payload"]["execution_quality"]
    assert quality["decision_time_bid"] == "4.20"
    assert quality["decision_time_ask"] == "4.30"
    assert quality["midpoint"] == "4.25"
    assert quality["submitted_limit_price"] == "4.25"
    assert quality["actual_fill_price"] == "4.28"
    assert Decimal(quality["fill_vs_midpoint"]) == Decimal("0.03")
    assert quality["pessimistic_reference"] == "ENTRY_AT_ASK"
    assert quality["official_competition_pnl_replacement"] is False


def test_eventual_fill_quality_is_added_during_reconciliation(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)
    authority.execute(passport_id="passport-001", candidate=candidate())
    original = broker.orders[0]
    broker.orders[0] = BrokerOrderSnapshot(
        broker_order_id=original.broker_order_id,
        client_order_id=original.client_order_id,
        symbol=original.symbol,
        status="filled",
        quantity=original.quantity,
        filled_quantity=original.quantity,
        filled_avg_price=Decimal("4.27"),
        limit_price=original.limit_price,
        submitted_at=original.submitted_at,
        updated_at=datetime.now(UTC),
    )

    report = BrokerReconciler(journal, broker).reconcile()

    assert report.matched
    record = journal.broker_order_records()[0]
    assert record["payload"]["execution_quality"]["actual_fill_price"] == "4.27"
    assert record["payload"]["execution_quality"]["pessimistic_reference"] == (
        "ENTRY_AT_ASK"
    )


def test_timeout_after_submit_is_unknown_and_cannot_be_blindly_retried(
    tmp_path: Path,
) -> None:
    journal, broker, _, authority = authority_fixture(tmp_path)
    broker.submit_error = TimeoutError("mock timeout after transport send")
    order_candidate = candidate(client_order_id="cajnmnstr-submit-timeout")

    with pytest.raises(TimeoutError):
        authority.execute(passport_id="passport-001", candidate=order_candidate)

    assert journal.broker_order_status(order_candidate.client_order_id) == "SUBMIT_UNKNOWN"
    record = journal.broker_order_records()[0]
    assert record["payload"]["reconciliation_required"] is True
    assert record["payload"]["blind_retry_allowed"] is False
    with pytest.raises(DuplicateOrderIdentityError):
        authority.execute(passport_id="passport-001", candidate=order_candidate)
    with pytest.raises(ExecutionDisabledError, match="reconciliation is required"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(client_order_id="cajnmnstr-after-submit-timeout"),
        )
    assert broker.submissions == []


def test_exit_stays_pending_until_reconciliation_proves_broker_flat(
    tmp_path: Path,
) -> None:
    journal, broker, coordinator, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        positions=[verified_position(quantity=Decimal("1"))],
    )
    exit_candidate = candidate(
        quantity=1,
        client_order_id="cajnmnstr-flat-proof-exit",
        side="sell",
        position_intent="sell_to_close",
    )
    authority.execute(passport_id="passport-001", candidate=exit_candidate)
    assert journal.broker_order_status(exit_candidate.client_order_id) == (
        "EXIT_PENDING_RECONCILIATION"
    )

    first = BrokerReconciler(journal, broker).reconcile()
    assert first.unverified_flat_client_ids == (exit_candidate.client_order_id,)
    assert journal.broker_order_status(exit_candidate.client_order_id) == (
        "EXIT_PENDING_RECONCILIATION"
    )

    journal.create_passport("passport-002", {"symbol": "SPY"})
    journal.seal_passport("passport-002", {"symbol": "SPY", "sealed": True})
    DeterministicReferee(journal).issue(
        passport_id="passport-002",
        verdict=RefereeVerdict.APPROVE,
        reason_code="MOCK_APPROVE_AFTER_EXIT",
        max_quantity=1,
        max_limit_price=Decimal("4.25"),
    )
    configured = settings(tmp_path)
    second_authority = OperatorAuthorityPath(
        configured,
        journal,
        coordinator,
        lambda: detailed_health(),
    )
    with pytest.raises(ExecutionDisabledError, match="reconciliation is required"):
        second_authority.execute(
            passport_id="passport-002",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-entry-before-flat",
            ),
        )

    broker.positions = []
    second = BrokerReconciler(journal, broker).reconcile()
    assert second.unverified_flat_client_ids == ()
    assert journal.broker_order_status(exit_candidate.client_order_id) == (
        "CLOSED_BROKER_FLAT"
    )
    third = BrokerReconciler(journal, broker).reconcile()
    assert third.unverified_flat_client_ids == ()
    assert journal.broker_order_status(exit_candidate.client_order_id) == (
        "CLOSED_BROKER_FLAT"
    )
    flat_events = [
        event
        for event in journal.list_events(EventType.RECONCILIATION)
        if event["payload"].get("reason_code") == "BROKER_FLAT_VERIFIED"
    ]
    assert len(flat_events) == 1
