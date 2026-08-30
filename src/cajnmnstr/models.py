from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    PAUSED = "PAUSED"


class EventType(StrEnum):
    CONNECTION = "connection"
    DATA_HEALTH_FAILURE = "data_health_failure"
    PROPOSAL = "proposal"
    REFEREE_VERDICT = "referee_verdict"
    AUTHORITY_TRANSITION = "authority_transition"
    ORDER_ATTEMPT = "order_attempt"
    BROKER_LIFECYCLE = "broker_lifecycle"
    RECONCILIATION = "reconciliation"
    INCIDENT = "incident"


class RefereeVerdict(StrEnum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    ABSTAIN = "ABSTAIN"
    BLOCK = "BLOCK"
    EXIT = "EXIT"


class AuthorityGrant(StrEnum):
    NONE = "NONE"
    ENTRY_FULL = "ENTRY_FULL"
    ENTRY_REDUCED = "ENTRY_REDUCED"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    account_number: str
    status: str
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    options_buying_power: Decimal | None
    options_approved_level: int | None
    options_trading_level: int | None
    trading_blocked: bool


@dataclass(frozen=True, slots=True)
class MarketClockSnapshot:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True, slots=True)
class MarketQuote:
    symbol: str
    bid_price: Decimal
    ask_price: Decimal
    bid_size: Decimal
    ask_size: Decimal
    observed_at: datetime
    feed: str


@dataclass(frozen=True, slots=True)
class OptionContractSnapshot:
    contract_id: str
    symbol: str
    underlying_symbol: str
    expiration_date: date
    contract_type: str
    strike_price: Decimal
    tradable: bool
    status: str


@dataclass(frozen=True, slots=True)
class OptionChainSnapshot:
    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    quote_at: datetime | None
    trade_price: Decimal | None
    trade_at: datetime | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    rho: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    feed: str


@dataclass(frozen=True, slots=True)
class OrderCandidate:
    """Non-executable option candidate awaiting deterministic authority."""

    symbol: str
    quantity: int
    side: str
    limit_price: Decimal
    client_order_id: str
    position_intent: str
    decision_bid: Decimal
    decision_ask: Decimal
    quote_at: datetime

    def __post_init__(self) -> None:
        if not re.fullmatch(r"SPY\d{6}[CP]\d{8}", self.symbol):
            raise ValueError("Only OCC-formatted SPY option symbols are accepted")
        if self.quantity <= 0:
            raise ValueError("Quantity must be a positive whole number")
        if self.limit_price <= 0:
            raise ValueError("Limit price must be positive")
        if not self.decision_bid.is_finite() or not self.decision_ask.is_finite():
            raise ValueError("Decision quote prices must be finite")
        if self.decision_bid <= 0 or self.decision_ask <= self.decision_bid:
            raise ValueError("Decision quote must be positive and uncrossed")
        if self.quote_at.tzinfo is None:
            raise ValueError("Decision quote timestamp must include a timezone")
        if self.side not in {"buy", "sell"}:
            raise ValueError("Side must be buy or sell")
        if self.position_intent not in {"buy_to_open", "sell_to_close"}:
            raise ValueError("Only buy-to-open and sell-to-close candidates are accepted")
        if (self.side, self.position_intent) not in {
            ("buy", "buy_to_open"),
            ("sell", "sell_to_close"),
        }:
            raise ValueError("Position intent must match a risk-valid opening or closing side")
        if not self.client_order_id.startswith("cajnmnstr-"):
            raise ValueError("Client order ID must use the cajnmnstr- namespace")


@dataclass(frozen=True, slots=True)
class RefereeResult:
    result_id: str
    passport_id: str
    verdict: str
    max_quantity: int | None
    max_limit_price: Decimal | None
    reason_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    quantity: int
    side: str
    limit_price: Decimal
    client_order_id: str
    position_intent: str
    passport_id: str
    decision_bid: Decimal
    decision_ask: Decimal
    quote_at: datetime

    def __post_init__(self) -> None:
        if not re.fullmatch(r"SPY\d{6}[CP]\d{8}", self.symbol):
            raise ValueError("Only OCC-formatted SPY option symbols are accepted")
        if self.quantity <= 0:
            raise ValueError("Quantity must be a positive whole number")
        if self.limit_price <= 0:
            raise ValueError("Limit price must be positive")
        if not self.decision_bid.is_finite() or not self.decision_ask.is_finite():
            raise ValueError("Decision quote prices must be finite")
        if self.decision_bid <= 0 or self.decision_ask <= self.decision_bid:
            raise ValueError("Decision quote must be positive and uncrossed")
        if self.quote_at.tzinfo is None:
            raise ValueError("Decision quote timestamp must include a timezone")
        if self.side not in {"buy", "sell"}:
            raise ValueError("Side must be buy or sell")
        if self.position_intent not in {"buy_to_open", "sell_to_close"}:
            raise ValueError("Only buy-to-open and sell-to-close option intents are accepted")
        if (self.side, self.position_intent) not in {
            ("buy", "buy_to_open"),
            ("sell", "sell_to_close"),
        }:
            raise ValueError("Position intent must match a risk-valid opening or closing side")
        if not self.client_order_id.startswith("cajnmnstr-"):
            raise ValueError("Client order ID must use the cajnmnstr- namespace")


@dataclass(frozen=True, slots=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    filled_avg_price: Decimal | None
    limit_price: Decimal | None
    submitted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    quantity: Decimal
    side: str
    market_value: Decimal
    average_entry_price: Decimal
    unrealized_pl: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    checked_at: datetime
    broker_order_count: int
    broker_position_count: int
    unknown_broker_client_ids: tuple[str, ...] = field(default_factory=tuple)
    missing_broker_client_ids: tuple[str, ...] = field(default_factory=tuple)
    unverified_flat_client_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        return (
            not self.unknown_broker_client_ids
            and not self.missing_broker_client_ids
            and not self.unverified_flat_client_ids
        )
