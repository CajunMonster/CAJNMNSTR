from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


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


class ExitReason(StrEnum):
    THESIS_INVALIDATION = "THESIS_INVALIDATION"
    RISK_STOP = "RISK_STOP"
    PROFIT_TARGET = "PROFIT_TARGET"
    TIME_STOP = "TIME_STOP"
    FORCED_EOD = "FORCED_EOD"


@dataclass(frozen=True, slots=True)
class InvalidationRule:
    """Owner-approved deterministic feature condition; never parsed from AI prose."""

    feature_name: str
    comparison: str
    threshold: Decimal

    def __post_init__(self) -> None:
        if not self.feature_name.strip():
            raise ValueError("Invalidation feature name is required")
        if self.comparison not in {"lt", "lte", "gt", "gte"}:
            raise ValueError("Invalidation comparison must be lt, lte, gt, or gte")
        if not self.threshold.is_finite():
            raise ValueError("Invalidation threshold must be finite")


@dataclass(frozen=True, slots=True)
class PositionManagementPlan:
    """Immutable per-entry plan; all numerical values require explicit owner approval."""

    plan_id: str
    entry_passport_id: str
    symbol: str
    maximum_quantity: int
    stop_loss_fraction: Decimal
    profit_target_fraction: Decimal | None
    invalidation: InvalidationRule
    time_stop_at: datetime
    forced_eod_at: datetime
    strategy_version: str
    rationale: str

    def __post_init__(self) -> None:
        if not self.plan_id.startswith("cajnmnstr-plan-"):
            raise ValueError("Position plan ID must use the cajnmnstr-plan- namespace")
        if not self.entry_passport_id.strip():
            raise ValueError("Entry Passport ID is required")
        if not re.fullmatch(r"SPY\d{6}[CP]\d{8}", self.symbol):
            raise ValueError("Position management accepts only OCC-formatted SPY options")
        if self.maximum_quantity <= 0:
            raise ValueError("Position plan quantity must be positive")
        if (
            not self.stop_loss_fraction.is_finite()
            or self.stop_loss_fraction <= 0
            or self.stop_loss_fraction >= 1
        ):
            raise ValueError("Stop-loss fraction must be finite and between zero and one")
        if self.profit_target_fraction is not None and (
            not self.profit_target_fraction.is_finite()
            or self.profit_target_fraction <= 0
        ):
            raise ValueError("Profit-target fraction must be positive when supplied")
        if self.time_stop_at.tzinfo is None or self.forced_eod_at.tzinfo is None:
            raise ValueError("Time-stop and forced-EOD timestamps require timezones")
        time_stop_local = self.time_stop_at.astimezone(NEW_YORK)
        forced_eod_local = self.forced_eod_at.astimezone(NEW_YORK)
        if time_stop_local.date() != forced_eod_local.date():
            raise ValueError("Time-stop and forced-EOD timestamps must be for one session")
        if self.time_stop_at > self.forced_eod_at:
            raise ValueError("Time stop cannot occur after forced EOD")
        if not time(9, 30) <= forced_eod_local.time() < time(16, 0):
            raise ValueError("Forced EOD must be inside the regular session and before close")
        if not self.strategy_version.strip() or not self.rationale.strip():
            raise ValueError("Strategy version and owner rationale are required")


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
class StockBarSnapshot:
    symbol: str
    timestamp: datetime
    timeframe_minutes: int | None
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None
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
