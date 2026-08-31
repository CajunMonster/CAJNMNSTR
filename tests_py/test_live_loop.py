from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cajnmnstr.config import PAPER_API_URL, Settings
from cajnmnstr.journal import Journal
from cajnmnstr.live_loop import READ_ONLY_LOOP_CONFIRMATION, ContinuousDecisionLoop
from cajnmnstr.models import EventType, MarketClockSnapshot, StockBarSnapshot

DECISION_AT = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)


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
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-openai-key",
        },
        load_local_file=False,
    )


def collection(at: datetime, *, positions=(), stale=()):
    bar = StockBarSnapshot(
        symbol="SPY",
        timestamp=at - timedelta(minutes=5),
        timeframe_minutes=5,
        open=Decimal("500"),
        high=Decimal("501"),
        low=Decimal("499"),
        close=Decimal("500.5"),
        volume=Decimal("1000"),
        vwap=None,
        feed="sip",
    )
    return SimpleNamespace(
        completed_bars=(bar,),
        positions=tuple(positions),
        open_orders=(),
        reconciliation=SimpleNamespace(matched=True),
        snapshot=SimpleNamespace(hard_failures=(), stale_sources=tuple(stale)),
        clock=MarketClockSnapshot(
            timestamp=at,
            is_open=True,
            next_open=at + timedelta(days=1),
            next_close=at + timedelta(hours=5),
        ),
    )


class SequenceCollector:
    def __init__(self, items):
        self.items = list(items)
        self.index = 0

    def collect(self):
        item = self.items[min(self.index, len(self.items) - 1)]
        self.index += 1
        return item


class FakeRunner:
    def __init__(self, items, states):
        self.collector = SequenceCollector(items)
        self.states = list(states)
        self.calls = []

    def run_collection(self, item, *, dashboard_path=None, health_path=None):
        del dashboard_path, health_path
        self.calls.append(item)
        state = self.states[len(self.calls) - 1]
        return SimpleNamespace(
            decision=SimpleNamespace(
                ai_cached=False,
                passport_id=f"passport-{len(self.calls)}",
                operator_review=SimpleNamespace(state=state),
            )
        )


def test_loop_continues_after_non_actionable_decision_on_new_epoch(tmp_path) -> None:
    app = settings(tmp_path)
    runner = FakeRunner(
        [collection(DECISION_AT), collection(DECISION_AT + timedelta(minutes=5))],
        ["NOT_ELIGIBLE", "NOT_ELIGIBLE"],
    )
    result = ContinuousDecisionLoop(
        app,
        Journal(app.journal_path),
        runner,
        sleep=lambda _: None,
    ).run(
        confirmation=READ_ONLY_LOOP_CONFIRMATION,
        cadence_seconds=30,
        max_cycles=2,
    )

    assert result.cycles == 2
    assert result.canonical_decisions == 2
    assert result.terminal_state == "BOUNDED_RUN_COMPLETE"
    assert result.broker_submission_allowed is False
    assert len(runner.calls) == 2


def test_loop_does_not_repeat_terra_for_unchanged_bar_epoch(tmp_path) -> None:
    app = settings(tmp_path)
    same = collection(DECISION_AT)
    runner = FakeRunner([same, same], ["NOT_ELIGIBLE"])
    result = ContinuousDecisionLoop(
        app,
        Journal(app.journal_path),
        runner,
        sleep=lambda _: None,
    ).run(
        confirmation=READ_ONLY_LOOP_CONFIRMATION,
        cadence_seconds=30,
        max_cycles=2,
    )

    assert result.canonical_decisions == 1
    assert len(runner.calls) == 1


def test_candidate_pauses_for_operator_review_without_submission(tmp_path) -> None:
    app = settings(tmp_path)
    runner = FakeRunner([collection(DECISION_AT)], ["READY_FOR_OPERATOR_REVIEW"])
    result = ContinuousDecisionLoop(
        app,
        Journal(app.journal_path),
        runner,
        sleep=lambda _: None,
    ).run(
        confirmation=READ_ONLY_LOOP_CONFIRMATION,
        cadence_seconds=30,
    )

    assert result.terminal_state == "OPERATOR_REVIEW_PENDING"
    assert result.canonical_decisions == 1
    assert result.broker_submission_allowed is False


def test_verified_position_requires_deterministic_management_handler(tmp_path) -> None:
    app = settings(tmp_path)
    runner = FakeRunner([collection(DECISION_AT, positions=(object(),))], [])
    result = ContinuousDecisionLoop(
        app,
        Journal(app.journal_path),
        runner,
        sleep=lambda _: None,
    ).run(
        confirmation=READ_ONLY_LOOP_CONFIRMATION,
        cadence_seconds=30,
    )

    assert result.terminal_state == "POSITION_MANAGEMENT_HANDLER_REQUIRED"
    assert result.canonical_decisions == 0
    assert runner.calls == []
    events = Journal(app.journal_path).list_events(EventType.CONNECTION)
    assert any(
        item["payload"]["state"] == "POSITION_MANAGEMENT_HANDLER_REQUIRED"
        for item in events
    )


def test_loop_requires_explicit_read_only_confirmation(tmp_path) -> None:
    app = settings(tmp_path)
    runner = FakeRunner([collection(DECISION_AT)], ["NOT_ELIGIBLE"])
    with pytest.raises(ValueError, match=READ_ONLY_LOOP_CONFIRMATION):
        ContinuousDecisionLoop(
            app,
            Journal(app.journal_path),
            runner,
            sleep=lambda _: None,
        ).run(confirmation="", cadence_seconds=30, max_cycles=1)
