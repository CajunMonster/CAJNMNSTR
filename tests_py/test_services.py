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
    RefereeVerdict,
)
from cajnmnstr.services import (
    DeterministicReferee,
    OperatorAuthorityPath,
    PaperExecutionCoordinator,
)


class MockBroker:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.submissions: list[OrderIntent] = []

    def submit_limit_order(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        assert self.journal.broker_order_status(intent.client_order_id) == "SUBMISSION_PENDING"
        self.submissions.append(intent)
        now = datetime.now(UTC)
        return BrokerOrderSnapshot(
            broker_order_id=f"mock-broker-{len(self.submissions):03d}",
            client_order_id=intent.client_order_id,
            symbol=intent.symbol,
            status="accepted",
            quantity=Decimal(intent.quantity),
            filled_quantity=Decimal("0"),
            limit_price=intent.limit_price,
            submitted_at=now,
            updated_at=now,
        )

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot:
        raise AssertionError(f"Duplicate lookup must not occur: {client_order_id}")


def settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "ALPACA_API_KEY": "mock-paper-key",
            "ALPACA_SECRET_KEY": "mock-paper-secret",
            "CAJNMNSTR_EXECUTION_ENABLED": "true" if enabled else "false",
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
    )


def authority_fixture(
    tmp_path: Path,
    *,
    verdict: RefereeVerdict = RefereeVerdict.APPROVE,
    max_quantity: int | None = 2,
    max_limit_price: Decimal | None = Decimal("4.25"),
    health: HealthReport | HealthState = HealthState.HEALTHY,
    execution_enabled: bool = True,
    referee_reason: str | None = None,
) -> tuple[Journal, MockBroker, PaperExecutionCoordinator, OperatorAuthorityPath]:
    configured = settings(tmp_path, enabled=execution_enabled)
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
    broker = MockBroker(journal)

    def current_health() -> HealthReport | HealthState:
        return health

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
        execution_armed=all(
            states[component] is HealthState.HEALTHY
            for component in ENTRY_CRITICAL_COMPONENTS
        ),
        position_management_armed=all(
            states[component] is HealthState.HEALTHY
            for component in EXIT_CRITICAL_COMPONENTS
        ),
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
        execution_armed=True,
        position_management_armed=True,
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


def test_execution_disabled_overrides_valid_approve(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        execution_enabled=False,
    )

    with pytest.raises(ExecutionDisabledError, match="execution is disabled"):
        authority.execute(passport_id="passport-001", candidate=candidate())

    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "EXECUTION_DISABLED"


def test_execution_disabled_remains_a_master_gate_for_exit(tmp_path: Path) -> None:
    journal, broker, _, authority = authority_fixture(
        tmp_path,
        verdict=RefereeVerdict.EXIT,
        max_quantity=1,
        execution_enabled=False,
    )
    with pytest.raises(ExecutionDisabledError, match="execution is disabled"):
        authority.execute(
            passport_id="passport-001",
            candidate=candidate(
                quantity=1,
                client_order_id="cajnmnstr-disabled-exit",
                side="sell",
                position_intent="sell_to_close",
            ),
        )
    assert broker.submissions == []
    assert last_transition(journal)["reason_code"] == "EXECUTION_DISABLED"


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
