from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .ai import AnalysisResult, ProposalDirection
from .config import PAPER_API_URL, Settings
from .decision_cycle import (
    TERRA_LIVE_INSTRUCTIONS,
    TERRA_LIVE_PROMPT_VERSION,
    EvidenceCalculator,
    EvidenceSnapshot,
    ReplayDecisionPipeline,
    ReplayDecisionResult,
    parse_occ_symbol,
)
from .health import (
    ENTRY_CRITICAL_COMPONENTS,
    EXIT_CRITICAL_COMPONENTS,
    ComponentHealth,
    HealthReport,
    freshness_health,
)
from .journal import Journal
from .models import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    EventType,
    HealthState,
    MarketClockSnapshot,
    MarketQuote,
    OptionChainSnapshot,
    PositionSnapshot,
    ReconciliationReport,
    RefereeVerdict,
    StockBarSnapshot,
)
from .ports import AnalysisProvider, BrokerReader, MarketDataReader
from .services import BrokerReconciler
from .session_risk import SessionRiskAuthority, SessionRiskSnapshot

NEW_YORK = ZoneInfo("America/New_York")
LIVE_QUOTE_MAX_AGE = timedelta(seconds=30)
LIVE_OPTION_MAX_AGE = timedelta(seconds=30)
FIVE_MINUTES = timedelta(minutes=5)


class LiveEvidenceReader(BrokerReader, MarketDataReader, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class LiveEvidenceCollection:
    account: AccountSnapshot
    clock: MarketClockSnapshot
    quote: MarketQuote
    completed_bars: tuple[StockBarSnapshot, ...]
    daily_bars: tuple[StockBarSnapshot, ...]
    option_chain: tuple[OptionChainSnapshot, ...]
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[BrokerOrderSnapshot, ...]
    reconciliation: ReconciliationReport
    snapshot: EvidenceSnapshot


@dataclass(frozen=True, slots=True)
class LiveDecisionOutcome:
    collection: LiveEvidenceCollection
    decision: ReplayDecisionResult
    health: HealthReport
    dashboard: dict[str, Any]
    session_risk: SessionRiskSnapshot


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(UTC).isoformat()


def _option_payload(chain: list[OptionChainSnapshot]) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    for item in chain:
        snapshots[item.symbol] = {
            "latestQuote": {
                "bp": item.bid_price,
                "ap": item.ask_price,
                "bs": item.bid_size,
                "as": item.ask_size,
                "t": item.quote_at,
            },
            "latestTrade": {
                "p": item.trade_price,
                "t": item.trade_at,
            },
            "impliedVolatility": item.implied_volatility,
            "greeks": {
                "delta": item.delta,
                "gamma": item.gamma,
                "rho": item.rho,
                "theta": item.theta,
                "vega": item.vega,
            },
        }
    return {"snapshots": snapshots}


def _regular_completed_bars(
    bars: list[StockBarSnapshot],
    *,
    decision_at: datetime,
) -> list[StockBarSnapshot]:
    completed: list[StockBarSnapshot] = []
    for bar in bars:
        local = bar.timestamp.astimezone(NEW_YORK)
        if not time(9, 30) <= local.time() < time(16, 0):
            continue
        if bar.timeframe_minutes != 5 or bar.timestamp + FIVE_MINUTES > decision_at:
            continue
        completed.append(bar)
    if not completed:
        return []
    latest_session = max(bar.timestamp.astimezone(NEW_YORK).date() for bar in completed)
    return sorted(
        (bar for bar in completed if bar.timestamp.astimezone(NEW_YORK).date() == latest_session),
        key=lambda item: item.timestamp,
    )


def _previous_close(
    daily_bars: list[StockBarSnapshot],
    session_date: date | None,
) -> Decimal | None:
    if session_date is None:
        return None
    candidates = [
        bar for bar in daily_bars if bar.timestamp.astimezone(NEW_YORK).date() < session_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.timestamp).close


class LiveEvidenceCollector:
    """Authenticated read-only Alpaca inputs normalized through EvidenceCalculator."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        reader: LiveEvidenceReader,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.reader = reader
        self.calculator = EvidenceCalculator()
        self._now = now or (lambda: datetime.now(UTC))

    def collect(self) -> LiveEvidenceCollection:
        self.settings.validate_static_safety()
        if self.settings.stock_feed != "sip" or self.settings.options_feed != "opra":
            raise ValueError("Live decision snapshots require configured SIP and OPRA feeds")
        self.journal.initialize()
        self.journal.probe()

        account = self.reader.get_account()
        positions = self.reader.list_positions()
        open_orders = self.reader.list_open_orders()
        reconciliation = BrokerReconciler(self.journal, self.reader).reconcile()
        clock = self.reader.get_clock()
        clock_at = clock.timestamp.astimezone(UTC)
        query_end_at = max(self._now().astimezone(UTC), clock_at)
        quote = self.reader.get_spy_quote(feed="sip")
        bars = self.reader.get_spy_bars(
            start=query_end_at - timedelta(days=10),
            end=query_end_at,
            timeframe_minutes=5,
            feed="sip",
        )
        daily_bars = self.reader.get_spy_daily_bars(
            start=query_end_at - timedelta(days=20),
            end=query_end_at,
            feed="sip",
        )
        request_date = query_end_at.astimezone(NEW_YORK).date()
        option_chain = self.reader.get_option_chain(
            expiration_gte=request_date + timedelta(days=7),
            expiration_lte=request_date + timedelta(days=21),
            feed="opra",
        )

        # The Evidence Snapshot timestamp is the completion time of the read set, not the
        # earlier market-clock response. Market-data timestamps can legitimately be newer than
        # that first response by milliseconds; using the pre-read clock falsely classified fresh
        # SIP and OPRA observations as future/stale.
        decision_at = max(self._now().astimezone(UTC), clock_at)
        completed_bars = _regular_completed_bars(bars, decision_at=decision_at)
        session_date = (
            None if not completed_bars else completed_bars[-1].timestamp.astimezone(NEW_YORK).date()
        )
        previous_close = _previous_close(daily_bars, session_date)

        latest_bar_completion = (
            None if not completed_bars else completed_bars[-1].timestamp + FIVE_MINUTES
        )
        latest_option_at = max(
            (item.quote_at for item in option_chain if item.quote_at is not None),
            default=None,
        )
        source_id = f"alpaca-paper-{decision_at.strftime('%Y%m%dT%H%M%SZ')}"
        document = {
            "fixture_id": source_id,
            "market_sets": {
                "alpaca_sip": {
                    "symbol": "SPY",
                    "previous_close": previous_close,
                    "bars": [
                        {
                            "t": bar.timestamp + FIVE_MINUTES,
                            "o": bar.open,
                            "h": bar.high,
                            "l": bar.low,
                            "c": bar.close,
                            "v": bar.volume,
                        }
                        for bar in completed_bars
                    ],
                }
            },
            "option_sets": {"alpaca_opra": _option_payload(option_chain)},
        }
        scenario = {
            "scenario_id": f"live-{decision_at.strftime('%Y%m%dT%H%M%SZ')}",
            "market_set": "alpaca_sip",
            "option_set": "alpaca_opra",
            "decision_at": decision_at,
        }
        provenance = {
            "source": "ALPACA_AUTHENTICATED_PAPER_READ_ONLY",
            "paper_endpoint": self.settings.alpaca_api_base_url,
            "stock_feed": "sip",
            "options_feed": "opra",
            "market_clock_at": _iso(clock_at),
            "snapshot_captured_at": _iso(decision_at),
            "spy_quote_at": _iso(quote.observed_at),
            "latest_completed_bar_at": _iso(latest_bar_completion),
            "latest_option_quote_at": _iso(latest_option_at),
            "event_calendar": "UNAVAILABLE_NOT_INVENTED",
            "news": "UNAVAILABLE_NOT_INVENTED",
        }
        snapshot = self.calculator.build(
            document,
            scenario,
            source_mode="LIVE_PAPER_READ_ONLY",
            source_provenance=provenance,
        )
        stale_sources = list(snapshot.stale_sources)
        sip_age = decision_at - quote.observed_at
        if sip_age < timedelta(0) or sip_age > LIVE_QUOTE_MAX_AGE:
            stale_sources.append("SPY_SIP_QUOTE")
        option_age = None if latest_option_at is None else decision_at - latest_option_at
        if option_age is None or option_age < timedelta(0) or option_age > LIVE_OPTION_MAX_AGE:
            stale_sources.append("SPY_OPRA_OPTIONS")
        if not clock.is_open:
            stale_sources.append("MARKET_SESSION_CLOSED")
        if stale_sources:
            snapshot = replace(
                snapshot,
                stale_sources=tuple(sorted(set(stale_sources))),
                actionable_fresh=False,
            )
        broker_failures: list[str] = []
        if account.status.upper() != "ACTIVE" or account.trading_blocked:
            broker_failures.append("BROKER_ACCOUNT_UNSAFE")
        if positions:
            broker_failures.append("BROKER_POSITION_NOT_FLAT")
        if open_orders:
            broker_failures.append("BROKER_OPEN_ORDERS_PRESENT")
        if not reconciliation.matched:
            broker_failures.append("BROKER_RECONCILIATION_MISMATCH")
        if broker_failures:
            snapshot = replace(
                snapshot,
                hard_failures=tuple(sorted(set((*snapshot.hard_failures, *broker_failures)))),
            )
        return LiveEvidenceCollection(
            account=account,
            clock=clock,
            quote=quote,
            completed_bars=tuple(completed_bars),
            daily_bars=tuple(daily_bars),
            option_chain=tuple(option_chain),
            positions=tuple(positions),
            open_orders=tuple(open_orders),
            reconciliation=reconciliation,
            snapshot=snapshot,
        )


def _component(
    name: str,
    state: HealthState,
    message: str,
    checked_at: datetime,
    protective_action: str = "No protective action required.",
) -> ComponentHealth:
    return ComponentHealth(name, state, message, protective_action, checked_at)


def build_live_health(
    settings: Settings,
    collection: LiveEvidenceCollection,
    analysis: AnalysisResult | None,
    session_risk: SessionRiskSnapshot,
) -> HealthReport:
    checked_at = datetime.now(UTC)
    safe_stop = "Keep new entries blocked and preserve the current broker state."
    broker_known = (
        collection.account.status.upper() == "ACTIVE"
        and not collection.account.trading_blocked
        and not collection.positions
        and not collection.open_orders
    )
    snapshot_valid = not collection.snapshot.hard_failures and not collection.snapshot.stale_sources
    components = [
        _component(
            "configuration",
            (
                HealthState.HEALTHY
                if settings.paper_mode
                and settings.alpaca_api_base_url == PAPER_API_URL
                and settings.stock_feed == "sip"
                and settings.options_feed == "opra"
                else HealthState.PAUSED
            ),
            "Paper endpoint with SIP and OPRA is configured.",
            checked_at,
            safe_stop,
        ),
        _component(
            "evidence_store",
            HealthState.HEALTHY,
            "Evidence journal initialized and probed.",
            checked_at,
        ),
        _component(
            "alpaca",
            HealthState.HEALTHY,
            "Authenticated paper account reads completed.",
            checked_at,
        ),
        _component(
            "broker_state",
            HealthState.HEALTHY if broker_known else HealthState.PAUSED,
            (
                "Broker account is active, flat, and has no open orders."
                if broker_known
                else "Broker account, positions, or open orders do not match flat expectations."
            ),
            checked_at,
            safe_stop,
        ),
        _component(
            "broker_reconciliation",
            (HealthState.HEALTHY if collection.reconciliation.matched else HealthState.PAUSED),
            (
                "Durable and Alpaca broker identities reconcile."
                if collection.reconciliation.matched
                else "Durable and Alpaca broker identities do not reconcile."
            ),
            checked_at,
            safe_stop,
        ),
        _component(
            "market_session",
            HealthState.HEALTHY if collection.clock.is_open else HealthState.PAUSED,
            "Regular market session is open." if collection.clock.is_open else "Market is closed.",
            checked_at,
            "Wait for an executable regular session.",
        ),
        freshness_health(
            component="spy_quote",
            observed_at=collection.quote.observed_at,
            maximum_age=LIVE_QUOTE_MAX_AGE,
            now=checked_at,
        ),
        freshness_health(
            component="option_quote",
            observed_at=max(
                (item.quote_at for item in collection.option_chain if item.quote_at is not None),
                default=None,
            ),
            maximum_age=LIVE_OPTION_MAX_AGE,
            now=checked_at,
        ),
        _component(
            "risk_limits",
            HealthState.HEALTHY if broker_known else HealthState.PAUSED,
            "Risk thresholds are unchanged and the account is flat.",
            checked_at,
            safe_stop,
        ),
        _component(
            "session_risk",
            HealthState.HEALTHY if session_risk.entry_allowed else HealthState.PAUSED,
            session_risk.detail,
            checked_at,
            "Block new entry only; preserve deterministic position management.",
        ),
        _component(
            "ai_provider",
            (
                HealthState.HEALTHY
                if analysis is None and settings.ai_configured
                else HealthState.PAUSED
                if analysis is None
                else HealthState.HEALTHY
                if analysis.failure_code is None
                else HealthState.DEGRADED
            ),
            (
                "Terra is configured and was intentionally not invoked for this cycle."
                if analysis is None and settings.ai_configured
                else "Terra is not configured."
                if analysis is None
                else "Terra returned a schema-valid proposal."
                if analysis.failure_code is None
                else f"Terra failed closed: {analysis.failure_code}."
            ),
            checked_at,
            "ABSTAIN and keep broker submission prohibited.",
        ),
        _component(
            "news",
            HealthState.HEALTHY,
            "No approved live news input was available; none was invented.",
            checked_at,
        ),
        _component(
            "event_calendar",
            HealthState.HEALTHY,
            "No approved live event-calendar input was available; state is UNAVAILABLE.",
            checked_at,
        ),
        _component(
            "evidence_snapshot",
            HealthState.HEALTHY if snapshot_valid else HealthState.PAUSED,
            (
                "Normalized Evidence Snapshot is complete and fresh."
                if snapshot_valid
                else "Normalized Evidence Snapshot is stale or incomplete."
            ),
            checked_at,
            safe_stop,
        ),
    ]
    component_states = {item.component: item.state for item in components}
    aggregate = (
        HealthState.PAUSED
        if HealthState.PAUSED in component_states.values()
        else (
            HealthState.DEGRADED
            if HealthState.DEGRADED in component_states.values()
            else HealthState.HEALTHY
        )
    )
    return HealthReport(
        state=aggregate,
        components=tuple(components),
        checked_at=checked_at,
        entry_armed=(
            settings.entry_armed
            and all(
                component_states.get(name) is HealthState.HEALTHY
                for name in ENTRY_CRITICAL_COMPONENTS
            )
            and snapshot_valid
        ),
        position_management_armed=(
            settings.position_management_armed
            and all(
                component_states.get(name) is HealthState.HEALTHY
                for name in EXIT_CRITICAL_COMPONENTS
            )
        ),
        broker_lock_active=settings.broker_lock,
    )


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _dashboard_state(
    settings: Settings,
    collection: LiveEvidenceCollection,
    decision: ReplayDecisionResult,
    health: HealthReport,
) -> dict[str, Any]:
    snapshot = collection.snapshot
    components = {item.component: item for item in health.components}
    quote_midpoint = (collection.quote.bid_price + collection.quote.ask_price) / Decimal("2")
    previous_close = snapshot.features.get("previous_close")
    if not isinstance(previous_close, Decimal):
        previous_close = _previous_close(
            list(collection.daily_bars),
            (
                None
                if not collection.completed_bars
                else collection.completed_bars[-1].timestamp.astimezone(NEW_YORK).date()
            ),
        )
    change = None if previous_close is None else quote_midpoint - previous_close
    change_percent = (
        None
        if previous_close is None or previous_close == 0
        else change / previous_close * Decimal("100")
    )
    selected = decision.selection.candidate
    parsed = None if selected is None else parse_occ_symbol(selected.symbol)
    risk_amount = (
        None
        if selected is None
        else selected.limit_price * Decimal(selected.quantity) * Decimal("100")
    )
    risk_percent = (
        None
        if risk_amount is None or collection.account.equity == 0
        else risk_amount / collection.account.equity * Decimal("100")
    )
    open_pl = sum((item.unrealized_pl for item in collection.positions), Decimal("0"))
    latest_option_at = max(
        (item.quote_at for item in collection.option_chain if item.quote_at is not None),
        default=None,
    )
    atm_iv = snapshot.features.get("atm_iv")
    skew = snapshot.features.get("simple_skew")
    surface_items = sorted(
        (
            item
            for item in collection.option_chain
            if item.implied_volatility is not None and item.delta is not None
        ),
        key=lambda item: (abs(abs(item.delta) - Decimal("0.50")), item.symbol),
    )[:4]
    blockers = [
        item.component for item in health.components if item.state is not HealthState.HEALTHY
    ]
    reasons = [decision.referee.reason_code]
    reasons.extend(snapshot.hard_failures[:2])
    reasons.extend(f"STALE_{item}" for item in snapshot.stale_sources[:2])
    reasons.extend(f"HEALTH_{item.upper()}" for item in blockers[:2])
    session = "MARKET OPEN" if collection.clock.is_open else "MARKET CLOSED"
    data_state = (
        "LIVE"
        if components["spy_quote"].state is HealthState.HEALTHY
        and components["option_quote"].state is HealthState.HEALTHY
        else "STALE"
    )
    direction = decision.proposal.direction
    regime_state = (
        "BULLISH"
        if direction is ProposalDirection.LONG_CALL
        and decision.referee.verdict in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}
        else (
            "BEARISH"
            if direction is ProposalDirection.LONG_PUT
            and decision.referee.verdict in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}
            else "NEUTRAL"
        )
    )
    now_iso = health.checked_at.isoformat()
    return {
        "schema_version": 1,
        "mode": "PAPER",
        "operational_state": health.state.value,
        "truth_label": (
            "PAPER · FRESH SIP/OPRA · STOP BEFORE BROKER"
            if health.state is HealthState.HEALTHY
            else f"PAPER · {session} · NON-ACTIONABLE SAFE STOP"
        ),
        "updated_at": now_iso,
        "controls": {
            "entry_enabled": settings.entry_enabled,
            "entry_armed": settings.entry_armed,
            "position_management_enabled": settings.position_management_enabled,
            "position_management_armed": settings.position_management_armed,
            "broker_lock_active": settings.broker_lock,
            "broker_submission_allowed": False,
        },
        "connections": [
            {
                "id": "alpaca",
                "label": "ALPACA",
                "value": "PAPER AUTH",
                "state": "verified",
                "detail": "Authenticated read-only account and broker state",
            },
            {
                "id": "sip",
                "label": "SIP MARKET DATA",
                "value": data_state,
                "state": "verified"
                if components["spy_quote"].state is HealthState.HEALTHY
                else "paused",
                "detail": components["spy_quote"].message,
            },
            {
                "id": "opra",
                "label": "OPRA OPTIONS",
                "value": data_state,
                "state": "verified"
                if components["option_quote"].state is HealthState.HEALTHY
                else "paused",
                "detail": components["option_quote"].message,
            },
            {
                "id": "terra",
                "label": "TERRA AI",
                "value": "VALID" if decision.ai_failure_code is None else "ABSTAIN",
                "state": "verified" if decision.ai_failure_code is None else "paused",
                "detail": "Structured proposal only; no broker tools",
            },
            {
                "id": "referee",
                "label": "REFEREE",
                "value": decision.referee.verdict.value,
                "state": "verified",
                "detail": "Deterministic authority intact",
            },
        ],
        "account": {
            "equity": _number(collection.account.equity),
            "buying_power": _number(collection.account.buying_power),
            "options_buying_power": _number(collection.account.options_buying_power),
            "day_pl": None,
            "open_pl": _number(open_pl),
            "position_count": len(collection.positions),
            "open_order_count": len(collection.open_orders),
            "as_of": now_iso,
            "source": "Authenticated Alpaca PAPER read-only",
        },
        "market": {
            "symbol": "SPY",
            "price": _number(quote_midpoint),
            "previous_close": _number(previous_close),
            "change": _number(change),
            "change_percent": _number(change_percent),
            "last_update": _iso(collection.quote.observed_at),
            "session": session,
            "data_state": data_state,
            "feed": "ALPACA SIP",
            "candles": [
                {
                    "t": bar.timestamp.astimezone(NEW_YORK).strftime("%H:%M"),
                    "o": _number(bar.open),
                    "h": _number(bar.high),
                    "l": _number(bar.low),
                    "c": _number(bar.close),
                }
                for bar in collection.completed_bars[-13:]
            ],
        },
        "regime": {
            "state": regime_state,
            "support": decision.referee.support_count,
            "opposition": decision.referee.opposition_count,
            "session": session,
            "detail": "Deterministic live features; authority remains subject to health.",
        },
        "options": {
            "feed": "OPRA",
            "status": "AUTHORIZED · " + data_state,
            "chain_health": "VALID" if not snapshot.hard_failures else "INVALID",
            "atm_iv": _number(atm_iv * Decimal("100") if isinstance(atm_iv, Decimal) else None),
            "skew": _number(skew * Decimal("100") if isinstance(skew, Decimal) else None),
            "skew_reason": "Same-strike skew"
            if isinstance(skew, Decimal)
            else "No valid same-strike skew",
            "last_update": _iso(latest_option_at) or now_iso,
            "surface": [
                {
                    "label": item.symbol[-9:],
                    "value": float(item.implied_volatility * Decimal("100")),
                }
                for item in surface_items
                if item.implied_volatility is not None
            ],
        },
        "proposal": {
            "direction": direction.value,
            "time_horizon": decision.proposal.time_horizon.value,
            "thesis": decision.proposal.thesis,
            "counterargument": decision.proposal.counterargument,
            "uncertainty": decision.proposal.uncertainty.value,
            "evidence_count": len(decision.proposal.evidence_ids),
            "invalidation": decision.proposal.invalidation.condition,
        },
        "decision": {
            "verdict": decision.referee.verdict.value,
            "state": (
                decision.operator_review.state
                if health.state is HealthState.HEALTHY
                else "NOT_ELIGIBLE"
            ),
            "symbol": None if selected is None else selected.symbol,
            "contract_label": (None if parsed is None else f"SPY {parsed[2]} {parsed[1].upper()}"),
            "expiration": None if parsed is None else parsed[0].isoformat(),
            "dte": None if parsed is None else (parsed[0] - snapshot.decision_at.date()).days,
            "quantity_authority": None if selected is None else selected.quantity,
            "limit_price": None if selected is None else _number(selected.limit_price),
            "authority_max_debit": _number(risk_amount),
            "risk_amount": _number(risk_amount),
            "risk_percent": _number(risk_percent),
            "uncertainty": decision.proposal.uncertainty.value,
            "reasons": reasons or ["NO_ACTIONABLE_AUTHORITY"],
        },
        "passport": {
            "id": decision.passport_id,
            "fixture_id": snapshot.fixture_id,
            "sealed": True,
            "source": "Authenticated Alpaca SIP/OPRA Evidence Snapshot",
        },
        "execution": [
            {
                "stage": "PROPOSED",
                "status": "COMPLETE",
                "detail": "Terra structured proposal validated",
            },
            {
                "stage": "REFEREE",
                "status": decision.referee.verdict.value,
                "detail": decision.referee.reason_code,
            },
            {
                "stage": "SUBMITTED",
                "status": "STOPPED",
                "detail": "Entry disabled; broker path absent",
            },
            {"stage": "FILLED", "status": "NOT STARTED", "detail": "No broker order"},
            {"stage": "EXIT", "status": "NOT STARTED", "detail": "No lifecycle change"},
        ],
        "activity": [
            {
                "time": health.checked_at.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "STOP",
                "text": "Sealed Passport · broker submission false",
                "mode": "PAPER",
            },
            {
                "time": snapshot.decision_at.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "REFEREE",
                "text": f"{decision.referee.verdict.value} · {decision.referee.reason_code}",
                "mode": "PAPER",
            },
            {
                "time": snapshot.decision_at.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "TERRA",
                "text": f"{direction.value} · {decision.proposal.uncertainty.value}",
                "mode": "PAPER",
            },
            {
                "time": snapshot.decision_at.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "MARKET",
                "text": "Authenticated SIP/OPRA snapshot normalized",
                "mode": "PAPER",
            },
        ],
        "systems": [
            {
                "id": item.component,
                "label": item.component.replace("_", " ").upper(),
                "state": item.state.value,
                "detail": item.message,
            }
            for item in health.components
            if item.component
            in {
                "spy_quote",
                "option_quote",
                "alpaca",
                "ai_provider",
                "evidence_store",
                "broker_reconciliation",
            }
        ],
    }


def _monitor_dashboard_state(
    settings: Settings,
    collection: LiveEvidenceCollection,
    health: HealthReport,
) -> dict[str, Any]:
    """Represent a monitoring-only cycle without fabricating a proposal or Passport."""
    snapshot = collection.snapshot
    components = {item.component: item for item in health.components}
    midpoint = (collection.quote.bid_price + collection.quote.ask_price) / Decimal("2")
    session_date = (
        None
        if not collection.completed_bars
        else collection.completed_bars[-1].timestamp.astimezone(NEW_YORK).date()
    )
    previous_close = _previous_close(list(collection.daily_bars), session_date)
    change = None if previous_close is None else midpoint - previous_close
    change_percent = (
        None
        if previous_close is None or previous_close == 0
        else change / previous_close * Decimal("100")
    )
    latest_option_at = max(
        (item.quote_at for item in collection.option_chain if item.quote_at is not None),
        default=None,
    )
    open_pl = sum((item.unrealized_pl for item in collection.positions), Decimal("0"))
    session = "MARKET OPEN" if collection.clock.is_open else "MARKET CLOSED"
    data_state = (
        "LIVE"
        if components["spy_quote"].state is HealthState.HEALTHY
        and components["option_quote"].state is HealthState.HEALTHY
        else "STALE"
    )
    now_iso = health.checked_at.isoformat()
    blockers = [
        item.component for item in health.components if item.state is not HealthState.HEALTHY
    ]
    atm_iv = snapshot.features.get("atm_iv")
    skew = snapshot.features.get("simple_skew")
    surface = sorted(
        (
            item
            for item in collection.option_chain
            if item.implied_volatility is not None and item.delta is not None
        ),
        key=lambda item: (abs(abs(item.delta) - Decimal("0.50")), item.symbol),
    )[:4]
    return {
        "schema_version": 1,
        "mode": "PAPER",
        "operational_state": health.state.value,
        "truth_label": f"PAPER · {session} · MONITORING · NO DECISION",
        "updated_at": now_iso,
        "controls": {
            "entry_enabled": settings.entry_enabled,
            "entry_armed": settings.entry_armed,
            "position_management_enabled": settings.position_management_enabled,
            "position_management_armed": settings.position_management_armed,
            "broker_lock_active": settings.broker_lock,
            "broker_submission_allowed": False,
        },
        "connections": [
            {
                "id": "alpaca",
                "label": "ALPACA",
                "value": "PAPER AUTH",
                "state": "verified",
                "detail": "Authenticated read-only account and broker state",
            },
            {
                "id": "sip",
                "label": "SIP MARKET DATA",
                "value": data_state,
                "state": (
                    "verified"
                    if components["spy_quote"].state is HealthState.HEALTHY
                    else "paused"
                ),
                "detail": components["spy_quote"].message,
            },
            {
                "id": "opra",
                "label": "OPRA OPTIONS",
                "value": data_state,
                "state": (
                    "verified"
                    if components["option_quote"].state is HealthState.HEALTHY
                    else "paused"
                ),
                "detail": components["option_quote"].message,
            },
            {
                "id": "terra",
                "label": "TERRA AI",
                "value": "STANDBY",
                "state": "paused",
                "detail": components["ai_provider"].message,
            },
            {
                "id": "referee",
                "label": "REFEREE",
                "value": "STANDBY",
                "state": "paused",
                "detail": "No actionable evidence epoch was presented.",
            },
        ],
        "account": {
            "equity": _number(collection.account.equity),
            "buying_power": _number(collection.account.buying_power),
            "options_buying_power": _number(collection.account.options_buying_power),
            "day_pl": None,
            "open_pl": _number(open_pl),
            "position_count": len(collection.positions),
            "open_order_count": len(collection.open_orders),
            "as_of": now_iso,
            "source": "Authenticated Alpaca PAPER read-only",
        },
        "market": {
            "symbol": "SPY",
            "price": _number(midpoint),
            "previous_close": _number(previous_close),
            "change": _number(change),
            "change_percent": _number(change_percent),
            "last_update": _iso(collection.quote.observed_at),
            "session": session,
            "data_state": data_state,
            "feed": "ALPACA SIP",
            "candles": [
                {
                    "t": bar.timestamp.astimezone(NEW_YORK).strftime("%H:%M"),
                    "o": _number(bar.open),
                    "h": _number(bar.high),
                    "l": _number(bar.low),
                    "c": _number(bar.close),
                }
                for bar in collection.completed_bars[-13:]
            ],
        },
        "regime": {
            "state": "NEUTRAL",
            "support": 0,
            "opposition": 0,
            "session": session,
            "detail": "No Referee decision ran for this monitoring-only cycle.",
        },
        "options": {
            "feed": "OPRA",
            "status": "AUTHORIZED · " + data_state,
            "chain_health": "VALID" if not snapshot.hard_failures else "INVALID",
            "atm_iv": _number(atm_iv * Decimal("100") if isinstance(atm_iv, Decimal) else None),
            "skew": _number(skew * Decimal("100") if isinstance(skew, Decimal) else None),
            "skew_reason": (
                "Same-strike skew" if isinstance(skew, Decimal) else "No valid same-strike skew"
            ),
            "last_update": _iso(latest_option_at) or now_iso,
            "surface": [
                {
                    "label": item.symbol[-9:],
                    "value": float(item.implied_volatility * Decimal("100")),
                }
                for item in surface
                if item.implied_volatility is not None
            ],
        },
        "proposal": {
            "direction": "NOT_EVALUATED",
            "time_horizon": "INTRADAY",
            "thesis": "Terra was not invoked because the cycle was non-actionable.",
            "counterargument": "No directional proposal was requested.",
            "uncertainty": "NOT_EVALUATED",
            "evidence_count": 0,
            "invalidation": "A fresh regular-session evidence epoch is required.",
        },
        "decision": {
            "verdict": "NOT_EVALUATED",
            "state": (
                "MONITORING"
                if health.state is HealthState.HEALTHY
                else "MONITORING_PAUSED"
            ),
            "symbol": None,
            "contract_label": None,
            "expiration": None,
            "dte": None,
            "quantity_authority": None,
            "limit_price": None,
            "authority_max_debit": None,
            "risk_amount": None,
            "risk_percent": None,
            "uncertainty": "NOT_EVALUATED",
            "reasons": blockers or ["NO_ACTIONABLE_EVIDENCE_EPOCH"],
        },
        "passport": {
            "id": None,
            "fixture_id": snapshot.fixture_id,
            "sealed": False,
            "source": "No Passport created for this monitoring-only cycle",
        },
        "execution": [
            {"stage": "PROPOSED", "status": "NOT STARTED", "detail": "Terra not invoked"},
            {
                "stage": "REFEREE",
                "status": "NOT STARTED",
                "detail": "No actionable evidence epoch",
            },
            {
                "stage": "SUBMITTED",
                "status": "STOPPED",
                "detail": "Entry disabled; broker path absent",
            },
            {"stage": "FILLED", "status": "NOT STARTED", "detail": "No broker order"},
            {"stage": "EXIT", "status": "NOT STARTED", "detail": "No lifecycle change"},
        ],
        "activity": [
            {
                "time": health.checked_at.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                "kind": "MONITOR",
                "text": (
                    f"{session} · broker reconciled · no decision"
                    if collection.reconciliation.matched
                    else f"{session} · reconciliation required · no decision"
                ),
                "mode": "PAPER",
            }
        ],
        "systems": [
            {
                "id": item.component,
                "label": item.component.replace("_", " ").upper(),
                "state": item.state.value,
                "detail": item.message,
            }
            for item in health.components
            if item.component
            in {
                "spy_quote",
                "option_quote",
                "alpaca",
                "ai_provider",
                "evidence_store",
                "broker_reconciliation",
            }
        ],
    }


def write_dashboard_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_health_state(
    path: Path,
    settings: Settings,
    health: HealthReport,
) -> None:
    payload = {
        **health.to_dict(),
        "mode": "PAPER_READ_ONLY",
        "entry_enabled": settings.entry_enabled,
        "position_management_enabled": settings.position_management_enabled,
        "broker_submission_allowed": False,
        "stock_feed": settings.stock_feed,
        "options_feed": settings.options_feed,
        "data_entitlement": settings.data_entitlement,
    }
    write_dashboard_state(path, payload)


class LiveDecisionRunner:
    """Read-only live path that deliberately has no execution coordinator dependency."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        reader: LiveEvidenceReader,
        analysis_provider: AnalysisProvider,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.collector = LiveEvidenceCollector(settings, journal, reader)
        self.pipeline = ReplayDecisionPipeline(settings, journal, analysis_provider)
        self.session_risk = SessionRiskAuthority(settings, journal)

    def run(
        self,
        *,
        dashboard_path: Path | None = None,
        health_path: Path | None = None,
    ) -> LiveDecisionOutcome:
        collection = self.collector.collect()
        return self.run_collection(
            collection,
            dashboard_path=dashboard_path,
            health_path=health_path,
        )

    def run_collection(
        self,
        collection: LiveEvidenceCollection,
        *,
        dashboard_path: Path | None = None,
        health_path: Path | None = None,
    ) -> LiveDecisionOutcome:
        """Process one previously collected read-only snapshot without re-reading Alpaca."""
        decision = self.pipeline.run_snapshot(
            collection.snapshot,
            instructions=TERRA_LIVE_INSTRUCTIONS,
            prompt_version=TERRA_LIVE_PROMPT_VERSION,
        )
        session_risk = self.session_risk.evaluate(collection.clock)
        health = build_live_health(
            self.settings,
            collection,
            self._analysis(decision),
            session_risk,
        )
        self._journal_health(health)
        dashboard = _dashboard_state(self.settings, collection, decision, health)
        dashboard["session_risk"] = session_risk.to_dict()
        if dashboard_path is not None:
            write_dashboard_state(dashboard_path, dashboard)
        if health_path is not None:
            write_health_state(health_path, self.settings, health)
        return LiveDecisionOutcome(collection, decision, health, dashboard, session_risk)

    def publish_monitor_state(
        self,
        collection: LiveEvidenceCollection,
        *,
        dashboard_path: Path | None = None,
        health_path: Path | None = None,
    ) -> None:
        """Publish a truthful monitoring state without invoking the AI decision path."""
        session_risk = self.session_risk.evaluate(collection.clock)
        health = build_live_health(self.settings, collection, None, session_risk)
        self._journal_health(health)
        dashboard = _monitor_dashboard_state(self.settings, collection, health)
        dashboard["session_risk"] = session_risk.to_dict()
        if dashboard_path is not None:
            write_dashboard_state(dashboard_path, dashboard)
        if health_path is not None:
            write_health_state(health_path, self.settings, health)

    def _analysis(self, decision: ReplayDecisionResult) -> AnalysisResult:
        return AnalysisResult(
            provider="openai",
            requested_model=self.settings.openai_model,
            resolved_model=self.settings.openai_model,
            proposal=decision.proposal,
            authority_disposition=(
                "ABSTAIN"
                if decision.proposal.direction is ProposalDirection.NO_TRADE
                else "PROPOSAL_ONLY"
            ),
            failure_code=decision.ai_failure_code,
            failure_detail=None,
            input_tokens=decision.input_tokens,
            output_tokens=decision.output_tokens,
        )

    def _journal_health(self, health: HealthReport) -> None:
        self.journal.append_event(
            EventType.CONNECTION,
            source="live_evidence_snapshot",
            severity="INFO" if health.state is HealthState.HEALTHY else "WARNING",
            payload={
                **health.to_dict(),
                "credentials_recorded": False,
                "account_identifiers_recorded": False,
                "broker_submission_allowed": False,
            },
            protective_action=(
                None
                if health.state is HealthState.HEALTHY
                else "Keep new entries blocked; live evidence is non-actionable."
            ),
        )
        for component in health.components:
            if component.state is HealthState.HEALTHY:
                self.journal.resolve_incidents(component.component)
            else:
                self.journal.open_incident(
                    component=component.component,
                    severity=("CRITICAL" if component.state is HealthState.PAUSED else "WARNING"),
                    state=component.state.value,
                    message=component.message,
                    protective_action=component.protective_action,
                )


def outcome_summary(outcome: LiveDecisionOutcome) -> dict[str, Any]:
    snapshot = outcome.collection.snapshot
    return {
        "status": (
            "READY_FOR_OPERATOR_REVIEW"
            if outcome.decision.operator_review.state == "READY_FOR_OPERATOR_REVIEW"
            and outcome.health.state is HealthState.HEALTHY
            else "SAFE_STOP"
        ),
        "mode": "PAPER_READ_ONLY",
        "market_open": outcome.collection.clock.is_open,
        "health": outcome.health.state.value,
        "entry_enabled": False,
        "entry_armed": False,
        "position_management_enabled": outcome.dashboard["controls"]["position_management_enabled"],
        "position_management_armed": outcome.dashboard["controls"]["position_management_armed"],
        "broker_lock_active": outcome.health.broker_lock_active,
        "broker_reconciled": outcome.collection.reconciliation.matched,
        "position_count": len(outcome.collection.positions),
        "open_order_count": len(outcome.collection.open_orders),
        "stock_feed": snapshot.source_provenance["stock_feed"],
        "options_feed": snapshot.source_provenance["options_feed"],
        "completed_five_minute_bar_count": len(outcome.collection.completed_bars),
        "option_snapshot_count": len(outcome.collection.option_chain),
        "hard_failures": snapshot.hard_failures,
        "stale_sources": snapshot.stale_sources,
        "passport_id": outcome.decision.passport_id,
        "passport_sealed": True,
        "terra_direction": outcome.decision.proposal.direction.value,
        "referee_verdict": outcome.decision.referee.verdict.value,
        "operator_review_state": outcome.decision.operator_review.state,
        "broker_submission_allowed": False,
        "dashboard_updated": True,
    }
