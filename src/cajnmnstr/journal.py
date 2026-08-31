from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import EvidenceStoreError
from .models import EventType, InvalidationRule, PositionManagementPlan, RefereeResult
from .position_policy import validate_initial_position_plan

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS evidence_passports (
        passport_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        sealed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_events (
        event_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        source TEXT NOT NULL,
        passport_id TEXT,
        correlation_id TEXT,
        payload_json TEXT NOT NULL,
        protective_action TEXT,
        FOREIGN KEY (passport_id) REFERENCES evidence_passports(passport_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS referee_results (
        result_id TEXT PRIMARY KEY,
        passport_id TEXT NOT NULL UNIQUE,
        verdict TEXT NOT NULL,
        max_quantity INTEGER,
        max_limit_price TEXT,
        reason_code TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        FOREIGN KEY (passport_id) REFERENCES evidence_passports(passport_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS broker_orders (
        client_order_id TEXT PRIMARY KEY,
        passport_id TEXT NOT NULL,
        broker_order_id TEXT UNIQUE,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        FOREIGN KEY (passport_id) REFERENCES evidence_passports(passport_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS health_incidents (
        incident_id TEXT PRIMARY KEY,
        component TEXT NOT NULL,
        severity TEXT NOT NULL,
        state TEXT NOT NULL,
        message TEXT NOT NULL,
        protective_action TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_decisions (
        decision_id TEXT PRIMARY KEY,
        evidence_snapshot_hash TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        response_json TEXT,
        abstention INTEGER,
        latency_ms INTEGER,
        validation_status TEXT NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE (evidence_snapshot_hash, prompt_version, prompt_hash, model)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_decision_attempts (
        decision_id TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        occurred_at TEXT NOT NULL,
        transport_retry INTEGER NOT NULL,
        status TEXT NOT NULL,
        latency_ms INTEGER,
        PRIMARY KEY (decision_id, attempt_number),
        FOREIGN KEY (decision_id) REFERENCES ai_decisions(decision_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_lifecycles (
        plan_id TEXT PRIMARY KEY,
        entry_passport_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        broker_quantity TEXT,
        exit_client_order_id TEXT,
        plan_json TEXT NOT NULL,
        lifecycle_json TEXT NOT NULL,
        FOREIGN KEY (entry_passport_id) REFERENCES evidence_passports(passport_id),
        FOREIGN KEY (exit_client_order_id) REFERENCES broker_orders(client_order_id)
    )
    """,
    """CREATE INDEX IF NOT EXISTS idx_journal_events_occurred_at
    ON journal_events(occurred_at)""",
    """CREATE INDEX IF NOT EXISTS idx_journal_events_passport
    ON journal_events(passport_id, occurred_at)""",
    """CREATE INDEX IF NOT EXISTS idx_health_incidents_open
    ON health_incidents(component, opened_at) WHERE resolved_at IS NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_position_lifecycles_active_symbol
    ON position_lifecycles(symbol)
    WHERE state != 'CLOSED_BROKER_FLAT'""",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None = None) -> str:
    return (moment or utc_now()).astimezone(UTC).isoformat()


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise EvidenceStoreError(f"Evidence store failure at {self.path}: {exc}") from exc
        finally:
            if "connection" in locals():
                connection.close()

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EvidenceStoreError(
                f"Cannot create evidence directory {self.path.parent}: {exc}"
            ) from exc
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA optimize")

    def probe(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    def create_passport(self, passport_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evidence_passports
                (passport_id, created_at, state, payload_json)
                VALUES (?, ?, 'OPEN', ?)""",
                (passport_id, _iso(), json.dumps(payload, sort_keys=True, default=str)),
            )

    def seal_passport(self, passport_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evidence_passports
                SET state = 'SEALED', payload_json = ?, sealed_at = ?
                WHERE passport_id = ? AND state = 'OPEN'""",
                (json.dumps(payload, sort_keys=True, default=str), _iso(), passport_id),
            )
            if cursor.rowcount != 1:
                raise EvidenceStoreError(
                    f"Passport {passport_id} was missing, already sealed, or otherwise invalid"
                )

    def passport_state(self, passport_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM evidence_passports WHERE passport_id = ?",
                (passport_id,),
            ).fetchone()
        return None if row is None else str(row["state"])

    def get_passport(self, passport_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT passport_id, created_at, state, payload_json, sealed_at
                FROM evidence_passports WHERE passport_id = ?""",
                (passport_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "passport_id": str(row["passport_id"]),
            "created_at": str(row["created_at"]),
            "state": str(row["state"]),
            "payload": json.loads(str(row["payload_json"])),
            "sealed_at": None if row["sealed_at"] is None else str(row["sealed_at"]),
        }

    def record_referee_result(
        self,
        *,
        passport_id: str,
        verdict: str,
        max_quantity: int | None,
        max_limit_price: str | None,
        reason_code: str,
        payload: dict[str, Any],
    ) -> RefereeResult:
        result_id = str(uuid.uuid4())
        created_at = utc_now()
        with self._connect() as connection:
            passport = connection.execute(
                "SELECT state FROM evidence_passports WHERE passport_id = ?",
                (passport_id,),
            ).fetchone()
            if passport is None or passport["state"] != "SEALED":
                raise EvidenceStoreError(
                    f"Referee result requires sealed Passport {passport_id}"
                )
            try:
                connection.execute(
                    """INSERT INTO referee_results
                    (result_id, passport_id, verdict, max_quantity, max_limit_price,
                     reason_code, created_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result_id,
                        passport_id,
                        verdict,
                        max_quantity,
                        max_limit_price,
                        reason_code,
                        _iso(created_at),
                        json.dumps(payload, sort_keys=True, default=str),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise EvidenceStoreError(
                    f"Passport {passport_id} already has a Referee result"
                ) from exc
        return RefereeResult(
            result_id=result_id,
            passport_id=passport_id,
            verdict=verdict,
            max_quantity=max_quantity,
            max_limit_price=None if max_limit_price is None else Decimal(max_limit_price),
            reason_code=reason_code,
            created_at=created_at,
        )

    def get_referee_result(self, passport_id: str) -> RefereeResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT result_id, passport_id, verdict, max_quantity,
                max_limit_price, reason_code, created_at
                FROM referee_results WHERE passport_id = ?""",
                (passport_id,),
            ).fetchone()
        if row is None:
            return None
        return RefereeResult(
            result_id=str(row["result_id"]),
            passport_id=str(row["passport_id"]),
            verdict=str(row["verdict"]),
            max_quantity=(None if row["max_quantity"] is None else int(row["max_quantity"])),
            max_limit_price=(
                None if row["max_limit_price"] is None else Decimal(str(row["max_limit_price"]))
            ),
            reason_code=str(row["reason_code"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def append_event(
        self,
        event_type: EventType,
        *,
        source: str,
        payload: dict[str, Any],
        severity: str = "INFO",
        passport_id: str | None = None,
        correlation_id: str | None = None,
        protective_action: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO journal_events
                (event_id, occurred_at, event_type, severity, source, passport_id,
                 correlation_id, payload_json, protective_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    _iso(),
                    event_type.value,
                    severity,
                    source,
                    passport_id,
                    correlation_id,
                    json.dumps(payload, sort_keys=True, default=str),
                    protective_action,
                ),
            )
        return event_id

    def list_events(self, event_type: EventType | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM journal_events"
        parameters: tuple[str, ...] = ()
        if event_type is not None:
            query += " WHERE event_type = ?"
            parameters = (event_type.value,)
        query += " ORDER BY occurred_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def open_incident(
        self,
        *,
        component: str,
        severity: str,
        state: str,
        message: str,
        protective_action: str,
    ) -> str:
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT incident_id FROM health_incidents
                WHERE component = ? AND state = ? AND message = ? AND resolved_at IS NULL
                ORDER BY opened_at DESC LIMIT 1""",
                (component, state, message),
            ).fetchone()
            if existing is not None:
                return str(existing["incident_id"])
            connection.execute(
                """UPDATE health_incidents SET resolved_at = ?
                WHERE component = ? AND resolved_at IS NULL""",
                (_iso(), component),
            )
            incident_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO health_incidents
                (incident_id, component, severity, state, message,
                 protective_action, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    component,
                    severity,
                    state,
                    message,
                    protective_action,
                    _iso(),
                ),
            )
        return incident_id

    def resolve_incidents(self, component: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE health_incidents SET resolved_at = ?
                WHERE component = ? AND resolved_at IS NULL""",
                (_iso(), component),
            )

    def reserve_order_attempt(
        self,
        *,
        client_order_id: str,
        passport_id: str,
        payload: dict[str, Any],
    ) -> bool:
        now = _iso()
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO broker_orders
                    (client_order_id, passport_id, status, created_at, updated_at, payload_json)
                    VALUES (?, ?, 'ATTEMPT_RESERVED', ?, ?, ?)""",
                    (
                        client_order_id,
                        passport_id,
                        now,
                        now,
                        json.dumps(payload, sort_keys=True, default=str),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def authorize_order_attempt(
        self,
        *,
        client_order_id: str,
        passport_id: str,
        payload: dict[str, Any],
    ) -> bool:
        now = _iso()
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO broker_orders
                    (client_order_id, passport_id, status, created_at, updated_at, payload_json)
                    VALUES (?, ?, 'AUTHORITY_GRANTED', ?, ?, ?)""",
                    (
                        client_order_id,
                        passport_id,
                        now,
                        now,
                        json.dumps(payload, sort_keys=True, default=str),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def claim_authorized_order(self, *, client_order_id: str, passport_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE broker_orders
                SET status = 'SUBMISSION_PENDING', updated_at = ?
                WHERE client_order_id = ? AND passport_id = ?
                  AND status = 'AUTHORITY_GRANTED'""",
                (_iso(), client_order_id, passport_id),
            )
        return cursor.rowcount == 1

    def broker_order_status(self, client_order_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM broker_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return None if row is None else str(row["status"])

    def update_broker_order(
        self,
        *,
        client_order_id: str,
        broker_order_id: str | None,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM broker_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if existing is None:
                raise EvidenceStoreError(f"Unknown client order ID: {client_order_id}")
            merged_payload = {
                **json.loads(str(existing["payload_json"])),
                **payload,
            }
            cursor = connection.execute(
                """UPDATE broker_orders
                SET broker_order_id = COALESCE(?, broker_order_id), status = ?,
                    updated_at = ?, payload_json = ?
                WHERE client_order_id = ?""",
                (
                    broker_order_id,
                    status,
                    _iso(),
                    json.dumps(merged_payload, sort_keys=True, default=str),
                    client_order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise EvidenceStoreError(f"Unknown client order ID: {client_order_id}")

    def local_client_order_ids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT client_order_id FROM broker_orders").fetchall()
        return {str(row["client_order_id"]) for row in rows}

    def broker_order_records(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT client_order_id, passport_id, broker_order_id, status,
                created_at, updated_at, payload_json FROM broker_orders
                ORDER BY created_at, client_order_id"""
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def has_unverified_exit(self) -> bool:
        return any(
            record["status"]
            in {
                "AUTHORITY_GRANTED",
                "SUBMISSION_PENDING",
                "SUBMISSION_FAILED",
                "EXIT_PENDING_RECONCILIATION",
                "SUBMIT_UNKNOWN",
            }
            and record["payload"].get("intent", {}).get("position_intent")
            == "sell_to_close"
            for record in self.broker_order_records()
        )

    def has_broker_uncertainty(self) -> bool:
        return any(
            record["status"]
            in {
                "SUBMISSION_PENDING",
                "SUBMISSION_FAILED",
                "EXIT_PENDING_RECONCILIATION",
                "SUBMIT_UNKNOWN",
            }
            for record in self.broker_order_records()
        )

    def register_position_plan(self, plan: PositionManagementPlan) -> bool:
        passport = self.get_passport(plan.entry_passport_id)
        if passport is None or passport["state"] != "SEALED":
            raise EvidenceStoreError(
                "Position management plan requires a sealed entry Evidence Passport"
            )
        referee = self.get_referee_result(plan.entry_passport_id)
        if (
            referee is None
            or referee.verdict not in {"APPROVE", "REDUCE"}
            or referee.max_quantity is None
            or plan.maximum_quantity > referee.max_quantity
        ):
            raise EvidenceStoreError(
                "Position plan requires APPROVE/REDUCE authority and cannot exceed its quantity"
            )
        try:
            validate_initial_position_plan(plan, passport["payload"], referee)
        except ValueError as exc:
            raise EvidenceStoreError(str(exc)) from exc
        now = _iso()
        plan_payload = {
            "plan_id": plan.plan_id,
            "entry_passport_id": plan.entry_passport_id,
            "symbol": plan.symbol,
            "maximum_quantity": plan.maximum_quantity,
            "stop_loss_fraction": str(plan.stop_loss_fraction),
            "profit_target_fraction": (
                None
                if plan.profit_target_fraction is None
                else str(plan.profit_target_fraction)
            ),
            "invalidation": {
                "feature_name": plan.invalidation.feature_name,
                "comparison": plan.invalidation.comparison,
                "threshold": str(plan.invalidation.threshold),
            },
            "invalidation_formula_version": plan.invalidation_formula_version,
            "invalidation_inputs": [
                [name, str(value)] for name, value in plan.invalidation_inputs
            ],
            "direction": plan.direction,
            "entry_referee_verdict": plan.entry_referee_verdict,
            "time_stop_duration_minutes": plan.time_stop_duration_minutes,
            "forced_eod_at": plan.forced_eod_at.astimezone(UTC).isoformat(),
            "strategy_version": plan.strategy_version,
            "rationale": plan.rationale,
        }
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO position_lifecycles
                    (plan_id, entry_passport_id, symbol, state, created_at, updated_at,
                     plan_json, lifecycle_json)
                    VALUES (?, ?, ?, 'PLANNED', ?, ?, ?, ?)""",
                    (
                        plan.plan_id,
                        plan.entry_passport_id,
                        plan.symbol,
                        now,
                        now,
                        json.dumps(plan_payload, sort_keys=True),
                        json.dumps(
                            {
                                "broker_flat_verified": False,
                                "threshold_defaults_used": False,
                                "fill_confirmed_at": None,
                                "time_stop_at": None,
                            },
                            sort_keys=True,
                        ),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def position_lifecycle(self, *, symbol: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM position_lifecycles
                WHERE symbol = ? AND state != 'CLOSED_BROKER_FLAT'
                ORDER BY created_at DESC LIMIT 1""",
                (symbol,),
            ).fetchone()
        return None if row is None else self._position_lifecycle_row(row)

    def active_position_lifecycles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM position_lifecycles
                WHERE state != 'CLOSED_BROKER_FLAT'
                ORDER BY created_at, plan_id"""
            ).fetchall()
        return [self._position_lifecycle_row(row) for row in rows]

    def update_position_lifecycle(
        self,
        *,
        plan_id: str,
        state: str,
        broker_quantity: Decimal | None,
        exit_client_order_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT lifecycle_json FROM position_lifecycles
                WHERE plan_id = ?""",
                (plan_id,),
            ).fetchone()
            if existing is None:
                raise EvidenceStoreError(f"Unknown position plan ID: {plan_id}")
            merged = {
                **json.loads(str(existing["lifecycle_json"])),
                **(payload or {}),
            }
            cursor = connection.execute(
                """UPDATE position_lifecycles
                SET state = ?, updated_at = ?, broker_quantity = ?,
                    exit_client_order_id = COALESCE(?, exit_client_order_id),
                    lifecycle_json = ?
                WHERE plan_id = ?""",
                (
                    state,
                    _iso(),
                    None if broker_quantity is None else str(broker_quantity),
                    exit_client_order_id,
                    json.dumps(merged, sort_keys=True, default=str),
                    plan_id,
                ),
            )
            if cursor.rowcount != 1:
                raise EvidenceStoreError(f"Unknown position plan ID: {plan_id}")

    def bind_position_fill(
        self,
        *,
        plan: PositionManagementPlan,
        fill_confirmed_at: datetime,
        filled_quantity: Decimal,
        average_entry_price: Decimal,
        anchor_source: str,
    ) -> tuple[datetime, datetime]:
        """Bind the immutable time-stop anchor once, at the first confirmed broker fill."""
        if fill_confirmed_at.tzinfo is None:
            raise EvidenceStoreError("Confirmed fill timestamp requires a timezone")
        if filled_quantity <= 0 or average_entry_price <= 0:
            raise EvidenceStoreError("Confirmed fill quantity and average price must be positive")
        confirmed = fill_confirmed_at.astimezone(UTC)
        time_stop_at = confirmed + timedelta(minutes=plan.time_stop_duration_minutes)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT state, lifecycle_json FROM position_lifecycles
                WHERE plan_id = ?""",
                (plan.plan_id,),
            ).fetchone()
            if row is None:
                raise EvidenceStoreError(f"Unknown position plan ID: {plan.plan_id}")
            lifecycle = json.loads(str(row["lifecycle_json"]))
            existing_confirmed = lifecycle.get("fill_confirmed_at")
            existing_stop = lifecycle.get("time_stop_at")
            if existing_confirmed is not None or existing_stop is not None:
                if existing_confirmed is None or existing_stop is None:
                    raise EvidenceStoreError("Position fill anchor is incomplete")
                return (
                    datetime.fromisoformat(str(existing_confirmed)).astimezone(UTC),
                    datetime.fromisoformat(str(existing_stop)).astimezone(UTC),
                )
            lifecycle.update(
                {
                    "fill_confirmed_at": confirmed.isoformat(),
                    "time_stop_at": time_stop_at.isoformat(),
                    "fill_anchor_source": anchor_source,
                    "initial_confirmed_quantity": str(filled_quantity),
                    "initial_confirmed_average_entry_price": str(average_entry_price),
                    "fill_anchor_bound_at": _iso(),
                }
            )
            state = "OPEN" if str(row["state"]) in {"PLANNED", "OPEN"} else str(row["state"])
            connection.execute(
                """UPDATE position_lifecycles
                SET state = ?, updated_at = ?, broker_quantity = ?, lifecycle_json = ?
                WHERE plan_id = ?""",
                (
                    state,
                    _iso(),
                    str(filled_quantity),
                    json.dumps(lifecycle, sort_keys=True),
                    plan.plan_id,
                ),
            )
        return confirmed, time_stop_at

    def broker_order_record(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT client_order_id, passport_id, broker_order_id, status,
                created_at, updated_at, payload_json FROM broker_orders
                WHERE client_order_id = ?""",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "payload": json.loads(str(row["payload_json"]))}

    @staticmethod
    def _position_lifecycle_row(row: sqlite3.Row) -> dict[str, Any]:
        raw_plan = json.loads(str(row["plan_json"]))
        plan = PositionManagementPlan(
            plan_id=str(raw_plan["plan_id"]),
            entry_passport_id=str(raw_plan["entry_passport_id"]),
            symbol=str(raw_plan["symbol"]),
            maximum_quantity=int(raw_plan["maximum_quantity"]),
            stop_loss_fraction=Decimal(str(raw_plan["stop_loss_fraction"])),
            profit_target_fraction=(
                None
                if raw_plan["profit_target_fraction"] is None
                else Decimal(str(raw_plan["profit_target_fraction"]))
            ),
            invalidation=InvalidationRule(
                feature_name=str(raw_plan["invalidation"]["feature_name"]),
                comparison=str(raw_plan["invalidation"]["comparison"]),
                threshold=Decimal(str(raw_plan["invalidation"]["threshold"])),
            ),
            invalidation_formula_version=str(raw_plan["invalidation_formula_version"]),
            invalidation_inputs=tuple(
                (str(name), Decimal(str(value)))
                for name, value in raw_plan["invalidation_inputs"]
            ),
            direction=str(raw_plan["direction"]),
            entry_referee_verdict=str(raw_plan["entry_referee_verdict"]),
            time_stop_duration_minutes=int(raw_plan["time_stop_duration_minutes"]),
            forced_eod_at=datetime.fromisoformat(str(raw_plan["forced_eod_at"])),
            strategy_version=str(raw_plan["strategy_version"]),
            rationale=str(raw_plan["rationale"]),
        )
        return {
            **dict(row),
            "plan": plan,
            "broker_quantity": (
                None
                if row["broker_quantity"] is None
                else Decimal(str(row["broker_quantity"]))
            ),
            "lifecycle": json.loads(str(row["lifecycle_json"])),
        }

    def claim_ai_decision(
        self,
        *,
        decision_id: str,
        evidence_snapshot_hash: str,
        prompt_version: str,
        prompt_hash: str,
        model: str,
    ) -> bool:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO ai_decisions
                    (decision_id, evidence_snapshot_hash, prompt_version, prompt_hash,
                     model, created_at, validation_status)
                    VALUES (?, ?, ?, ?, ?, ?, 'IN_PROGRESS')""",
                    (
                        decision_id,
                        evidence_snapshot_hash,
                        prompt_version,
                        prompt_hash,
                        model,
                        _iso(),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def get_ai_decision(
        self,
        *,
        evidence_snapshot_hash: str,
        prompt_version: str,
        prompt_hash: str,
        model: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM ai_decisions
                WHERE evidence_snapshot_hash = ? AND prompt_version = ?
                  AND prompt_hash = ? AND model = ?""",
                (evidence_snapshot_hash, prompt_version, prompt_hash, model),
            ).fetchone()
        if row is None:
            return None
        return {
            **dict(row),
            "response": (
                None
                if row["response_json"] is None
                else json.loads(str(row["response_json"]))
            ),
        }

    def complete_ai_decision(
        self,
        *,
        decision_id: str,
        response: dict[str, Any],
        abstention: bool,
        latency_ms: int,
        validation_status: str,
        retry_count: int,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE ai_decisions
                SET completed_at = ?, response_json = ?, abstention = ?, latency_ms = ?,
                    validation_status = ?, retry_count = ?
                WHERE decision_id = ? AND validation_status = 'IN_PROGRESS'""",
                (
                    _iso(),
                    json.dumps(response, sort_keys=True, default=str),
                    int(abstention),
                    latency_ms,
                    validation_status,
                    retry_count,
                    decision_id,
                ),
            )
            if cursor.rowcount != 1:
                raise EvidenceStoreError(
                    f"AI decision {decision_id} was missing or already completed"
                )

    def record_ai_attempt(
        self,
        *,
        decision_id: str,
        attempt_number: int,
        transport_retry: bool,
        status: str,
        latency_ms: int | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ai_decision_attempts
                (decision_id, attempt_number, occurred_at, transport_retry, status, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    attempt_number,
                    _iso(),
                    int(transport_retry),
                    status,
                    latency_ms,
                ),
            )

    def ai_decision_attempts(self, decision_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT decision_id, attempt_number, occurred_at, transport_retry,
                status, latency_ms FROM ai_decision_attempts
                WHERE decision_id = ? ORDER BY attempt_number""",
                (decision_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def write_emergency_incident(path: Path, incident: dict[str, Any]) -> None:
    """Best-effort fallback when the primary evidence store itself is unavailable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"occurred_at": _iso(), **incident}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except OSError as exc:
        raise EvidenceStoreError(
            f"Primary and emergency incident persistence both failed: {exc}"
        ) from exc
