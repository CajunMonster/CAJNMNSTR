import sqlite3
from pathlib import Path

from cajnmnstr.journal import Journal
from cajnmnstr.models import EventType


def test_passport_events_and_order_idempotency_are_durable(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal" / "test.sqlite3")
    journal.initialize()
    journal.probe()
    journal.create_passport("passport-001", {"symbol": "SPY"})
    event_id = journal.append_event(
        EventType.PROPOSAL,
        source="test",
        passport_id="passport-001",
        payload={"direction": "abstain"},
    )
    assert event_id
    assert journal.reserve_order_attempt(
        client_order_id="cajnmnstr-test-001",
        passport_id="passport-001",
        payload={"quantity": 1},
    )
    assert not journal.reserve_order_attempt(
        client_order_id="cajnmnstr-test-001",
        passport_id="passport-001",
        payload={"quantity": 1},
    )
    journal.update_broker_order(
        client_order_id="cajnmnstr-test-001",
        broker_order_id="broker-001",
        status="accepted",
        payload={"status": "accepted"},
    )
    assert journal.local_client_order_ids() == {"cajnmnstr-test-001"}
    journal.seal_passport("passport-001", {"verdict": "ABSTAIN"})


def test_sqlite_uses_wal_and_full_synchronous_durability(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal" / "durable.sqlite3")
    journal.initialize()
    with sqlite3.connect(journal.path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
    assert str(journal_mode).lower() == "wal"
    assert synchronous == 2
