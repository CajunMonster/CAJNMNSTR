from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .ai import (
    AnalysisResult,
    ProposalDirection,
    ProposalUncertainty,
    StructuredProposal,
    fail_closed_analysis,
    validate_proposal,
)
from .config import Settings
from .errors import ConfigurationError
from .journal import Journal
from .models import AuthorityGrant, EventType, OptionChainSnapshot, OrderCandidate, RefereeVerdict
from .option_chain import parse_option_chain_payload
from .ports import AnalysisProvider
from .services import DeterministicReferee

LOCKED_MAX_LIMIT_PRICE = Decimal("4.25")
LOCKED_APPROVE_QUANTITY = 2
LOCKED_REDUCE_QUANTITY = 1
MAX_REPLAY_QUOTE_AGE_SECONDS = Decimal("300")
MAX_SPREAD_RATIO = Decimal("0.10")
MIN_DTE = 7
MAX_DTE = 21
PREFERRED_MIN_DTE = 10
PREFERRED_MAX_DTE = 14
MIN_ABS_DELTA = Decimal("0.40")
MAX_ABS_DELTA = Decimal("0.55")
TARGET_ABS_DELTA = Decimal("0.50")

TERRA_REPLAY_INSTRUCTIONS = """You are the CAJNMNSTR Terra analyst.
Analyze only the supplied historical replay Evidence Passport. It is never live or actionable.
Choose LONG_CALL, LONG_PUT, or NO_TRADE for the INTRADAY horizon.
Cite only evidence IDs present in the Passport. State the strongest counterargument,
uncertainty, and a falsifiable structured invalidation supported by Passport evidence IDs.
Do not calculate features, select contracts, size positions, invoke tools, or claim order authority.
Stale, invalid, weak, or conflicting evidence should produce NO_TRADE when direction is unsupported.
"""
TERRA_REPLAY_PROMPT_VERSION = "terra-replay-v1"
TERRA_LIVE_INSTRUCTIONS = """You are the CAJNMNSTR Terra analyst.
Analyze only the supplied authenticated Alpaca PAPER Evidence Snapshot.
Choose LONG_CALL, LONG_PUT, or NO_TRADE for the INTRADAY horizon.
Cite only Evidence IDs present in the Snapshot. State the strongest counterargument,
uncertainty, and a falsifiable structured invalidation supported by Snapshot evidence IDs.
Do not calculate features, select contracts, size positions, invoke tools, or claim order authority.
Stale, closed-session, invalid, weak, or conflicting evidence must produce NO_TRADE.
You have no broker interface and the workflow always stops before submission.
"""
TERRA_LIVE_PROMPT_VERSION = "terra-live-v1"


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid replay numeric value: {value!r}") from exc


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Replay timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _ratio(current: Decimal, previous: Decimal) -> Decimal:
    if previous <= 0:
        raise ValueError("Return denominator must be positive")
    return (current / previous) - Decimal("1")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _analysis_payload(analysis: AnalysisResult) -> dict[str, Any]:
    return _json_safe(asdict(analysis))


def _analysis_from_payload(payload: dict[str, Any]) -> AnalysisResult:
    return AnalysisResult(
        provider=str(payload["provider"]),
        requested_model=str(payload["requested_model"]),
        resolved_model=str(payload["resolved_model"]),
        proposal=validate_proposal(payload["proposal"]),
        authority_disposition=str(payload["authority_disposition"]),
        failure_code=(
            None if payload.get("failure_code") is None else str(payload["failure_code"])
        ),
        failure_detail=(
            None if payload.get("failure_detail") is None else str(payload["failure_detail"])
        ),
        input_tokens=(
            None if payload.get("input_tokens") is None else int(payload["input_tokens"])
        ),
        output_tokens=(
            None if payload.get("output_tokens") is None else int(payload["output_tokens"])
        ),
    )


@dataclass(frozen=True, slots=True)
class ReplayBar:
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    scenario_id: str
    fixture_id: str
    symbol: str
    decision_at: datetime
    latest_market_at: datetime | None
    latest_option_at: datetime | None
    features: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    hard_failures: tuple[str, ...]
    stale_sources: tuple[str, ...]
    actionable_fresh: bool
    option_chain: tuple[OptionChainSnapshot, ...]
    market_set: str
    option_set: str
    source_mode: str
    source_provenance: dict[str, Any]

    @property
    def evidence_ids(self) -> set[str]:
        return {str(item["id"]) for item in self.evidence}

    def terra_payload(self) -> dict[str, Any]:
        return _json_safe(
            {
                "passport_mode": self.source_mode,
                "scenario_id": self.scenario_id,
                "symbol": self.symbol,
                "decision_at": self.decision_at,
                "provenance": self.source_provenance,
                "features": self.features,
                "evidence": self.evidence,
                "data_quality": {
                    "hard_failures": self.hard_failures,
                    "stale_sources": self.stale_sources,
                    "actionable_fresh": self.actionable_fresh,
                },
                "constraints": {
                    "execution_allowed": False,
                    "broker_access": False,
                    "feature_calculation": "deterministic",
                    "contract_selection": "deterministic",
                },
            }
        )


@dataclass(frozen=True, slots=True)
class RefereeDecision:
    verdict: RefereeVerdict
    reason_code: str
    support_count: int
    opposition_count: int
    max_quantity: int | None
    max_limit_price: Decimal | None


@dataclass(frozen=True, slots=True)
class OptionSelectionResult:
    candidate: OrderCandidate | None
    eligible_quantity: int | None
    expiration: date | None
    dte: int | None
    absolute_delta: Decimal | None
    spread_ratio: Decimal | None
    reason_code: str
    rejection_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class OperatorReviewResult:
    state: str
    authority: AuthorityGrant
    broker_submission_allowed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class ReplayDecisionResult:
    scenario_id: str
    passport_id: str
    proposal: StructuredProposal
    ai_failure_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    referee: RefereeDecision
    selection: OptionSelectionResult
    operator_review: OperatorReviewResult
    decision_id: str
    evidence_snapshot_hash: str
    prompt_hash: str
    ai_latency_ms: int
    ai_cached: bool


class FixtureAnalysisProvider:
    """Deterministic Terra-shaped replay outputs for tests and offline distribution runs."""

    def __init__(self, proposals: dict[str, dict[str, Any]]) -> None:
        self._proposals = proposals

    @property
    def model_name(self) -> str:
        return "gpt-5.6-terra-replay-fixture"

    def analyze(self, *, instructions: str, evidence_json: str) -> AnalysisResult:
        del instructions
        evidence = json.loads(evidence_json)
        scenario_id = str(evidence["scenario_id"])
        proposal = validate_proposal(self._proposals[scenario_id])
        return AnalysisResult(
            provider="fixture",
            requested_model=self.model_name,
            resolved_model=self.model_name,
            proposal=proposal,
            authority_disposition=(
                "ABSTAIN"
                if proposal.direction is ProposalDirection.NO_TRADE
                else "PROPOSAL_ONLY"
            ),
            failure_code=None,
            failure_detail=None,
            input_tokens=None,
            output_tokens=None,
        )


class EvidenceCalculator:
    def build(
        self,
        document: dict[str, Any],
        scenario: dict[str, Any],
        *,
        source_mode: str = "REPLAY_ONLY",
        source_provenance: dict[str, Any] | None = None,
    ) -> EvidenceSnapshot:
        scenario_id = str(scenario["scenario_id"])
        market_set_name = str(scenario["market_set"])
        option_set_name = str(scenario["option_set"])
        market = document["market_sets"][market_set_name]
        option_payload = document["option_sets"].get(option_set_name, {"snapshots": {}})
        decision_at = _datetime(scenario["decision_at"])
        symbol = str(market.get("symbol", ""))
        hard_failures: list[str] = []
        if symbol != "SPY":
            hard_failures.append("SYMBOL_NOT_SPY")

        bars: list[ReplayBar] = []
        for raw in market.get("bars", []):
            try:
                bar = ReplayBar(
                    observed_at=_datetime(raw["t"]),
                    open=_decimal(raw["o"]),
                    high=_decimal(raw["h"]),
                    low=_decimal(raw["l"]),
                    close=_decimal(raw["c"]),
                    volume=_decimal(raw["v"]),
                )
            except (KeyError, ValueError):
                hard_failures.append("BAR_MALFORMED")
                continue
            if bar.low > bar.high or not bar.low <= bar.close <= bar.high or bar.volume < 0:
                hard_failures.append("BAR_RANGE_INVALID")
            bars.append(bar)
        bars.sort(key=lambda item: item.observed_at)
        if len(bars) < 13:
            hard_failures.append("BARS_INSUFFICIENT")
        if len({bar.observed_at for bar in bars}) != len(bars):
            hard_failures.append("BAR_TIMESTAMPS_DUPLICATE")

        previous_close = _optional_decimal(market.get("previous_close"))
        if previous_close is None or previous_close <= 0:
            hard_failures.append("PREVIOUS_CLOSE_INVALID")

        chain: list[OptionChainSnapshot] = []
        try:
            chain = parse_option_chain_payload(option_payload, feed="opra")
        except ValueError:
            hard_failures.append("OPTION_CHAIN_MALFORMED")

        features: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []

        def add_feature(
            feature_id: str,
            name: str,
            value: Any,
            unit: str,
        ) -> None:
            if value is None:
                return
            features[name] = value
            evidence.append(
                {
                    "id": feature_id,
                    "kind": "deterministic_feature",
                    "name": name,
                    "value": value,
                    "unit": unit,
                }
            )

        if len(bars) >= 13:
            current = bars[-1].close
            add_feature("feature:return_5m", "return_5m", _ratio(current, bars[-2].close), "ratio")
            add_feature(
                "feature:return_15m",
                "return_15m",
                _ratio(current, bars[-4].close),
                "ratio",
            )
            add_feature(
                "feature:return_60m",
                "return_60m",
                _ratio(current, bars[-13].close),
                "ratio",
            )
            if previous_close is not None and previous_close > 0:
                add_feature(
                    "feature:previous_close_gap",
                    "previous_close_gap",
                    _ratio(bars[0].open, previous_close),
                    "ratio",
                )

            volume_total = sum((bar.volume for bar in bars), Decimal("0"))
            if volume_total > 0:
                price_volume = sum(
                    (
                        ((bar.high + bar.low + bar.close) / Decimal("3")) * bar.volume
                        for bar in bars
                    ),
                    Decimal("0"),
                )
                vwap = price_volume / volume_total
                relationship = "ABOVE" if current > vwap else "BELOW" if current < vwap else "AT"
                add_feature("feature:vwap", "vwap", vwap, "USD")
                add_feature(
                    "feature:vwap_relationship",
                    "vwap_relationship",
                    relationship,
                    "state",
                )

            opening_bars = bars[:6]
            opening_high = max(bar.high for bar in opening_bars)
            opening_low = min(bar.low for bar in opening_bars)
            opening_state = (
                "ABOVE"
                if current > opening_high
                else "BELOW"
                if current < opening_low
                else "INSIDE"
            )
            add_feature(
                "feature:opening_range_state",
                "opening_range_state",
                opening_state,
                "state",
            )
            add_feature(
                "feature:opening_range_high",
                "opening_range_high",
                opening_high,
                "USD",
            )
            add_feature(
                "feature:opening_range_low",
                "opening_range_low",
                opening_low,
                "USD",
            )

            day_high = max(bar.high for bar in bars)
            day_low = min(bar.low for bar in bars)
            day_span = day_high - day_low
            day_location = None if day_span <= 0 else (current - day_low) / day_span
            add_feature(
                "feature:day_range_location",
                "day_range_location",
                day_location,
                "ratio",
            )

            expected_volume = _optional_decimal(market.get("expected_volume_at_time"))
            relative_volume = (
                None
                if expected_volume is None or expected_volume <= 0
                else volume_total / expected_volume
            )
            add_feature(
                "feature:relative_volume",
                "relative_volume",
                relative_volume,
                "ratio",
            )

            log_returns = [
                math.log(float(bars[index].close / bars[index - 1].close))
                for index in range(1, len(bars))
                if bars[index - 1].close > 0 and bars[index].close > 0
            ]
            realized_volatility = (
                None
                if len(log_returns) < 2
                else Decimal(
                    str(statistics.pstdev(log_returns) * math.sqrt(19656))
                )
            )
            add_feature(
                "feature:realized_volatility",
                "realized_volatility",
                realized_volatility,
                "annualized_ratio",
            )
            add_feature("feature:underlying_price", "underlying_price", current, "USD")

            preferred = [
                item
                for item in chain
                if (parsed := parse_occ_symbol(item.symbol)) is not None
                and PREFERRED_MIN_DTE <= (parsed[0] - decision_at.date()).days <= PREFERRED_MAX_DTE
            ]
            add_feature(
                "feature:preferred_expiry_state",
                "preferred_expiry_contract_count",
                len(preferred),
                "count",
            )
            calls = [
                item
                for item in chain
                if (parsed := parse_occ_symbol(item.symbol)) is not None
                and parsed[1] == "call"
                and item.implied_volatility is not None
            ]
            puts = [
                item
                for item in chain
                if (parsed := parse_occ_symbol(item.symbol)) is not None
                and parsed[1] == "put"
                and item.implied_volatility is not None
            ]
            atm_call = min(
                calls,
                key=lambda item: abs(parse_occ_symbol(item.symbol)[2] - current),
                default=None,
            )
            atm_put = min(
                puts,
                key=lambda item: abs(parse_occ_symbol(item.symbol)[2] - current),
                default=None,
            )
            atm_values = [
                item.implied_volatility
                for item in (atm_call, atm_put)
                if item is not None and item.implied_volatility is not None
            ]
            atm_iv = (
                None
                if not atm_values
                else sum(atm_values, Decimal("0")) / Decimal(len(atm_values))
            )
            add_feature("feature:atm_iv", "atm_iv", atm_iv, "ratio")
            simple_skew = None
            if atm_call is not None and atm_put is not None:
                call_occ = parse_occ_symbol(atm_call.symbol)
                put_occ = parse_occ_symbol(atm_put.symbol)
                if (
                    call_occ is not None
                    and put_occ is not None
                    and call_occ[2] == put_occ[2]
                    and atm_call.implied_volatility is not None
                    and atm_put.implied_volatility is not None
                ):
                    simple_skew = atm_put.implied_volatility - atm_call.implied_volatility
            add_feature("feature:simple_skew", "simple_skew", simple_skew, "iv_difference")

        events = market.get("events")
        if events is None:
            event_state = "UNAVAILABLE"
        else:
            event_state = (
                "CLEAR"
                if not events
                else ", ".join(str(item["name"]) for item in events)
            )
        add_feature(
            "context:event_calendar",
            "event_calendar_state",
            event_state,
            "state",
        )
        for item in market.get("news", []):
            evidence.append(
                {
                    "id": str(item["id"]),
                    "kind": "news_context",
                    "summary": str(item["summary"]),
                }
            )

        latest_market_at = bars[-1].observed_at if bars else None
        quote_times = [item.quote_at for item in chain if item.quote_at is not None]
        latest_option_at = max(quote_times) if quote_times else None
        stale_sources: list[str] = []
        for name, observed_at in (
            ("SPY_BARS", latest_market_at),
            ("SPY_OPTIONS", latest_option_at),
        ):
            if observed_at is None:
                continue
            age = Decimal(str((decision_at - observed_at).total_seconds()))
            if age < 0 or age > MAX_REPLAY_QUOTE_AGE_SECONDS:
                stale_sources.append(name)
        actionable_fresh = not stale_sources
        evidence.append(
            {
                "id": "data:freshness",
                "kind": "data_health",
                "actionable_fresh": actionable_fresh,
                "stale_sources": stale_sources,
                "maximum_age_seconds": MAX_REPLAY_QUOTE_AGE_SECONDS,
            }
        )
        if hard_failures:
            evidence.append(
                {
                    "id": "data:hard_failures",
                    "kind": "data_health",
                    "failures": sorted(set(hard_failures)),
                }
            )

        return EvidenceSnapshot(
            scenario_id=scenario_id,
            fixture_id=str(document["fixture_id"]),
            symbol=symbol,
            decision_at=decision_at,
            latest_market_at=latest_market_at,
            latest_option_at=latest_option_at,
            features=features,
            evidence=tuple(evidence),
            hard_failures=tuple(sorted(set(hard_failures))),
            stale_sources=tuple(stale_sources),
            actionable_fresh=actionable_fresh,
            option_chain=tuple(chain),
            market_set=market_set_name,
            option_set=option_set_name,
            source_mode=source_mode,
            source_provenance=(
                source_provenance
                if source_provenance is not None
                else {
                    "fixture_id": str(document["fixture_id"]),
                    "market_set": market_set_name,
                    "option_set": option_set_name,
                }
            ),
        )


class ReplayRefereePolicy:
    def evaluate(
        self,
        snapshot: EvidenceSnapshot,
        analysis: AnalysisResult,
    ) -> RefereeDecision:
        if snapshot.hard_failures:
            return RefereeDecision(
                verdict=RefereeVerdict.BLOCK,
                reason_code="HARD_DATA_INVALID",
                support_count=0,
                opposition_count=0,
                max_quantity=None,
                max_limit_price=None,
            )
        if not snapshot.actionable_fresh:
            return RefereeDecision(
                verdict=RefereeVerdict.BLOCK,
                reason_code="STALE_EVIDENCE",
                support_count=0,
                opposition_count=0,
                max_quantity=None,
                max_limit_price=None,
            )
        if analysis.failure_code is not None:
            return RefereeDecision(
                verdict=RefereeVerdict.ABSTAIN,
                reason_code=f"AI_{analysis.failure_code}",
                support_count=0,
                opposition_count=0,
                max_quantity=None,
                max_limit_price=None,
            )
        proposal = analysis.proposal
        if proposal.direction is ProposalDirection.NO_TRADE:
            return RefereeDecision(
                verdict=RefereeVerdict.ABSTAIN,
                reason_code="AI_NO_TRADE",
                support_count=0,
                opposition_count=0,
                max_quantity=None,
                max_limit_price=None,
            )

        support, opposition = self._direction_counts(snapshot, proposal.direction)
        if support < 3:
            return RefereeDecision(
                verdict=RefereeVerdict.ABSTAIN,
                reason_code="DIRECTION_UNSUPPORTED",
                support_count=support,
                opposition_count=opposition,
                max_quantity=None,
                max_limit_price=None,
            )
        if (
            support >= 5
            and opposition == 0
            and proposal.uncertainty is not ProposalUncertainty.HIGH
        ):
            return RefereeDecision(
                verdict=RefereeVerdict.APPROVE,
                reason_code="DIRECTION_STRONGLY_CONFIRMED",
                support_count=support,
                opposition_count=opposition,
                max_quantity=LOCKED_APPROVE_QUANTITY,
                max_limit_price=LOCKED_MAX_LIMIT_PRICE,
            )
        return RefereeDecision(
            verdict=RefereeVerdict.REDUCE,
            reason_code="DIRECTION_CONFIRMED_WITH_SOFT_CONFLICT",
            support_count=support,
            opposition_count=opposition,
            max_quantity=LOCKED_REDUCE_QUANTITY,
            max_limit_price=LOCKED_MAX_LIMIT_PRICE,
        )

    @staticmethod
    def _direction_counts(
        snapshot: EvidenceSnapshot,
        direction: ProposalDirection,
    ) -> tuple[int, int]:
        bullish: list[int] = []
        for name in ("return_5m", "return_15m", "return_60m"):
            value = snapshot.features.get(name)
            if isinstance(value, Decimal):
                bullish.append(1 if value > 0 else -1 if value < 0 else 0)
        bullish.append(
            1
            if snapshot.features.get("vwap_relationship") == "ABOVE"
            else -1
            if snapshot.features.get("vwap_relationship") == "BELOW"
            else 0
        )
        bullish.append(
            1
            if snapshot.features.get("opening_range_state") == "ABOVE"
            else -1
            if snapshot.features.get("opening_range_state") == "BELOW"
            else 0
        )
        location = snapshot.features.get("day_range_location")
        if isinstance(location, Decimal):
            bullish.append(
                1
                if location >= Decimal("0.60")
                else -1
                if location <= Decimal("0.40")
                else 0
            )
        directional = (
            bullish
            if direction is ProposalDirection.LONG_CALL
            else [-item for item in bullish]
        )
        return sum(item > 0 for item in directional), sum(item < 0 for item in directional)


def parse_occ_symbol(symbol: str) -> tuple[date, str, Decimal] | None:
    match = re.fullmatch(r"SPY(\d{6})([CP])(\d{8})", symbol)
    if match is None:
        return None
    expiration = datetime.strptime(match.group(1), "%y%m%d").date()
    contract_type = "call" if match.group(2) == "C" else "put"
    strike = Decimal(match.group(3)) / Decimal("1000")
    return expiration, contract_type, strike


class ReplayOptionSelector:
    def select(
        self,
        *,
        scenario_id: str,
        snapshot: EvidenceSnapshot,
        direction: ProposalDirection,
        referee: RefereeDecision,
    ) -> OptionSelectionResult:
        if referee.verdict not in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}:
            return OptionSelectionResult(
                candidate=None,
                eligible_quantity=None,
                expiration=None,
                dte=None,
                absolute_delta=None,
                spread_ratio=None,
                reason_code=f"VERDICT_{referee.verdict.value}",
                rejection_counts={},
            )
        expected_type = "call" if direction is ProposalDirection.LONG_CALL else "put"
        underlying = snapshot.features.get("underlying_price")
        if not isinstance(underlying, Decimal):
            return self._none("UNDERLYING_PRICE_MISSING", {})

        rejection_counts: dict[str, int] = {}
        eligible: list[tuple[tuple[Any, ...], OptionChainSnapshot, date, int, Decimal]] = []
        for item in snapshot.option_chain:
            parsed = parse_occ_symbol(item.symbol)
            reason: str | None = None
            if parsed is None:
                reason = "SYMBOL_INVALID"
            else:
                expiration, contract_type, strike = parsed
                dte = (expiration - snapshot.decision_at.date()).days
                if contract_type != expected_type:
                    continue
                if not MIN_DTE <= dte <= MAX_DTE:
                    reason = "DTE_OUT_OF_RANGE"
                elif any(
                    value is None
                    for value in (item.delta, item.gamma, item.theta, item.vega, item.rho)
                ):
                    reason = "MISSING_GREEKS"
                elif any(
                    not value.is_finite()
                    for value in (item.delta, item.gamma, item.theta, item.vega, item.rho)
                    if value is not None
                ) or (item.gamma is not None and item.gamma < 0) or (
                    item.vega is not None and item.vega < 0
                ):
                    reason = "INVALID_GREEKS"
                elif (
                    item.implied_volatility is None
                    or not item.implied_volatility.is_finite()
                    or item.implied_volatility <= 0
                ):
                    reason = "IV_INVALID"
                elif (
                    item.bid_price is None
                    or item.ask_price is None
                    or not item.bid_price.is_finite()
                    or not item.ask_price.is_finite()
                    or item.bid_price <= 0
                    or item.ask_price < item.bid_price
                ):
                    reason = "QUOTE_INVALID"
                elif (
                    item.bid_size is None
                    or item.ask_size is None
                    or not item.bid_size.is_finite()
                    or not item.ask_size.is_finite()
                    or item.bid_size <= 0
                    or item.ask_size <= 0
                ):
                    reason = "QUOTE_LIQUIDITY_INVALID"
                elif item.quote_at is None:
                    reason = "QUOTE_TIMESTAMP_MISSING"
                elif item.quote_at.tzinfo is None:
                    reason = "QUOTE_TIMESTAMP_INVALID"
                else:
                    quote_age = Decimal(
                        str((snapshot.decision_at - item.quote_at.astimezone(UTC)).total_seconds())
                    )
                    if quote_age < 0 or quote_age > MAX_REPLAY_QUOTE_AGE_SECONDS:
                        reason = "QUOTE_STALE"
                    elif item.feed != "opra":
                        reason = "FEED_NOT_OPRA"
                    elif item.delta is None or not (
                        MIN_ABS_DELTA <= abs(item.delta) <= MAX_ABS_DELTA
                    ):
                        reason = "DELTA_OUT_OF_RANGE"
                    else:
                        midpoint = (item.bid_price + item.ask_price) / Decimal("2")
                        spread_ratio = (item.ask_price - item.bid_price) / midpoint
                        if spread_ratio > MAX_SPREAD_RATIO:
                            reason = "SPREAD_TOO_WIDE"
                        elif (
                            referee.max_limit_price is None
                            or item.ask_price > referee.max_limit_price
                        ):
                            reason = "PREMIUM_LIMIT_EXCEEDED"
                        else:
                            preferred_penalty = (
                                0 if PREFERRED_MIN_DTE <= dte <= PREFERRED_MAX_DTE else 1
                            )
                            score = (
                                preferred_penalty,
                                abs(dte - 12),
                                abs(abs(item.delta) - TARGET_ABS_DELTA),
                                spread_ratio,
                                abs(strike - underlying),
                                item.symbol,
                            )
                            eligible.append((score, item, expiration, dte, spread_ratio))
            if reason is not None:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        if not eligible:
            return self._none("NO_SUITABLE_CONTRACT", rejection_counts)
        _, selected, expiration, dte, spread_ratio = min(eligible, key=lambda item: item[0])
        assert selected.ask_price is not None
        assert selected.bid_price is not None
        assert selected.quote_at is not None
        assert selected.delta is not None
        assert referee.max_quantity is not None
        preview_mode = "replay" if snapshot.source_mode == "REPLAY_ONLY" else "live-preview"
        candidate = OrderCandidate(
            symbol=selected.symbol,
            quantity=referee.max_quantity,
            side="buy",
            limit_price=selected.ask_price,
            client_order_id=f"cajnmnstr-{preview_mode}-{scenario_id}",
            position_intent="buy_to_open",
            decision_bid=selected.bid_price,
            decision_ask=selected.ask_price,
            quote_at=selected.quote_at,
        )
        return OptionSelectionResult(
            candidate=candidate,
            eligible_quantity=referee.max_quantity,
            expiration=expiration,
            dte=dte,
            absolute_delta=abs(selected.delta),
            spread_ratio=spread_ratio,
            reason_code="SELECTED",
            rejection_counts=rejection_counts,
        )

    @staticmethod
    def _none(reason_code: str, counts: dict[str, int]) -> OptionSelectionResult:
        return OptionSelectionResult(
            candidate=None,
            eligible_quantity=None,
            expiration=None,
            dte=None,
            absolute_delta=None,
            spread_ratio=None,
            reason_code=reason_code,
            rejection_counts=counts,
        )


class OperatorReviewGate:
    """Confirms review eligibility without reserving identity or invoking a coordinator."""

    def __init__(self, settings: Settings, journal: Journal) -> None:
        if settings.entry_enabled or settings.entry_armed:
            raise ConfigurationError(
                "Decision review requires entry authority disabled and unarmed"
            )
        self.settings = settings
        self.journal = journal

    def preview(
        self,
        decision: RefereeDecision,
        selection: OptionSelectionResult,
        *,
        source_mode: str = "REPLAY_ONLY",
    ) -> OperatorReviewResult:
        if (
            decision.verdict not in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}
            or selection.candidate is None
            or selection.eligible_quantity is None
        ):
            return OperatorReviewResult(
                state="NOT_ELIGIBLE",
                authority=AuthorityGrant.NONE,
                broker_submission_allowed=False,
                reason_code=(
                    selection.reason_code
                    if decision.verdict in {RefereeVerdict.APPROVE, RefereeVerdict.REDUCE}
                    else f"VERDICT_{decision.verdict.value}"
                ),
            )
        authority = (
            AuthorityGrant.ENTRY_FULL
            if decision.verdict is RefereeVerdict.APPROVE
            else AuthorityGrant.ENTRY_REDUCED
        )
        return OperatorReviewResult(
            state="READY_FOR_OPERATOR_REVIEW",
            authority=authority,
            broker_submission_allowed=False,
            reason_code=(
                "REPLAY_STOP_BEFORE_BROKER"
                if source_mode == "REPLAY_ONLY"
                else "LIVE_STOP_BEFORE_BROKER"
            ),
        )

    def confirm(
        self,
        *,
        passport_id: str,
        decision: RefereeDecision,
        selection: OptionSelectionResult,
        source_mode: str = "REPLAY_ONLY",
    ) -> OperatorReviewResult:
        result = self.preview(decision, selection, source_mode=source_mode)
        stored_referee = self.journal.get_referee_result(passport_id)
        if self.journal.passport_state(passport_id) != "SEALED" or stored_referee is None:
            result = OperatorReviewResult(
                state="NOT_ELIGIBLE",
                authority=AuthorityGrant.NONE,
                broker_submission_allowed=False,
                reason_code="SEALED_AUTHORITY_MISSING",
            )
        self.journal.append_event(
            EventType.AUTHORITY_TRANSITION,
            source=(
                "replay_operator_review_gate"
                if source_mode == "REPLAY_ONLY"
                else "live_operator_review_gate"
            ),
            passport_id=passport_id,
            correlation_id=(
                None
                if selection.candidate is None
                else selection.candidate.client_order_id
            ),
            severity="INFO" if result.state == "READY_FOR_OPERATOR_REVIEW" else "WARNING",
            payload={
                "passport_id": passport_id,
                "verdict": decision.verdict.value,
                "authority_granted": result.authority.value,
                "execution_allowed": False,
                "reason_code": result.reason_code,
                "mock_broker_result": None,
            },
            protective_action="Stop before broker submission; owner review is required.",
        )
        return result


class ReplayDecisionPipeline:
    """Execution-free normalized decision slice with no broker coordinator dependency."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        analysis_provider: AnalysisProvider,
    ) -> None:
        if settings.entry_enabled or settings.entry_armed:
            raise ConfigurationError(
                "Replay pipeline requires entry authority disabled and unarmed"
            )
        self.settings = settings
        self.journal = journal
        self.analysis_provider = analysis_provider
        self.calculator = EvidenceCalculator()
        self.referee_policy = ReplayRefereePolicy()
        self.selector = ReplayOptionSelector()
        self.review_gate = OperatorReviewGate(settings, journal)

    def run_document(self, document: dict[str, Any]) -> list[ReplayDecisionResult]:
        if document.get("replay") is not True or document.get("execution_allowed") is not False:
            raise ValueError("Decision cycle accepts only execution-disabled replay fixtures")
        self.journal.initialize()
        self.journal.probe()
        return [self.run_scenario(document, scenario) for scenario in document["scenarios"]]

    def run_scenario(
        self,
        document: dict[str, Any],
        scenario: dict[str, Any],
    ) -> ReplayDecisionResult:
        snapshot = self.calculator.build(document, scenario)
        return self.run_snapshot(
            snapshot,
            instructions=TERRA_REPLAY_INSTRUCTIONS,
            prompt_version=TERRA_REPLAY_PROMPT_VERSION,
        )

    def run_snapshot(
        self,
        snapshot: EvidenceSnapshot,
        *,
        instructions: str,
        prompt_version: str,
    ) -> ReplayDecisionResult:
        """Run one normalized snapshot through Terra, Referee, selector, and safe review."""
        self.journal.initialize()
        self.journal.probe()
        mode_label = "replay" if snapshot.source_mode == "REPLAY_ONLY" else "live"
        passport_id = f"{mode_label}-{snapshot.scenario_id}-{uuid.uuid4().hex[:10]}"
        open_payload = {
            "passport_version": "1.0",
            "state": "OPEN",
            "mode": snapshot.source_mode,
            "scenario_id": snapshot.scenario_id,
            "fixture_id": snapshot.fixture_id,
            "symbol": snapshot.symbol,
            "provenance": snapshot.source_provenance,
            "entry_enabled": self.settings.entry_enabled,
            "entry_armed": self.settings.entry_armed,
            "position_management_enabled": self.settings.position_management_enabled,
            "position_management_armed": self.settings.position_management_armed,
            "broker_lock_active": self.settings.broker_lock,
        }
        self.journal.create_passport(passport_id, open_payload)
        if snapshot.hard_failures or snapshot.stale_sources:
            self.journal.append_event(
                EventType.DATA_HEALTH_FAILURE,
                source=f"{mode_label}_feature_calculator",
                passport_id=passport_id,
                severity="CRITICAL",
                payload={
                    "mode": snapshot.source_mode,
                    "scenario_id": snapshot.scenario_id,
                    "hard_failures": snapshot.hard_failures,
                    "stale_sources": snapshot.stale_sources,
                    "execution_allowed": False,
                },
                protective_action="BLOCK actionable authority and stop before broker submission.",
            )

        analysis, decision_meta = self._canonical_analysis(
            snapshot,
            instructions=instructions,
            prompt_version=prompt_version,
        )
        proposal_payload = _json_safe(asdict(analysis.proposal))
        self.journal.append_event(
            EventType.PROPOSAL,
            source=f"terra_{mode_label}",
            passport_id=passport_id,
            severity="INFO" if analysis.failure_code is None else "WARNING",
            payload={
                "provider": analysis.provider,
                "requested_model": analysis.requested_model,
                "resolved_model": analysis.resolved_model,
                "proposal": proposal_payload,
                "authority_disposition": analysis.authority_disposition,
                "failure_code": analysis.failure_code,
                "input_tokens": analysis.input_tokens,
                "output_tokens": analysis.output_tokens,
                **decision_meta,
                "source_mode": snapshot.source_mode,
            },
            protective_action=(
                None
                if analysis.failure_code is None
                else "ABSTAIN; do not create actionable authority."
            ),
        )

        referee = self.referee_policy.evaluate(snapshot, analysis)
        selection = self.selector.select(
            scenario_id=snapshot.scenario_id,
            snapshot=snapshot,
            direction=analysis.proposal.direction,
            referee=referee,
        )
        preview = self.review_gate.preview(
            referee,
            selection,
            source_mode=snapshot.source_mode,
        )
        self.journal.append_event(
            EventType.PROPOSAL,
            source="deterministic_option_selector",
            passport_id=passport_id,
            severity="INFO" if selection.candidate is not None else "WARNING",
            payload={
                "selection": _json_safe(asdict(selection)),
                "broker_submission_allowed": False,
            },
            protective_action="Stop before broker submission.",
        )

        sealed_payload = {
            **open_payload,
            "state": "SEALED",
            "evidence_snapshot": snapshot.terra_payload(),
            "option_chain": _json_safe([asdict(item) for item in snapshot.option_chain]),
            "terra": {
                "provider": analysis.provider,
                "requested_model": analysis.requested_model,
                "resolved_model": analysis.resolved_model,
                "proposal": proposal_payload,
                "failure_code": analysis.failure_code,
                "input_tokens": analysis.input_tokens,
                "output_tokens": analysis.output_tokens,
                "decision_id": decision_meta["decision_id"],
                "evidence_snapshot_hash": decision_meta["evidence_snapshot_hash"],
                "prompt_version": decision_meta["prompt_version"],
                "prompt_hash": decision_meta["prompt_hash"],
                "latency_ms": decision_meta["latency_ms"],
                "validation_status": decision_meta["validation_status"],
                "retry_count": decision_meta["retry_count"],
                "cached": decision_meta["cached"],
            },
            "referee": _json_safe(asdict(referee)),
            "option_selection": _json_safe(asdict(selection)),
            "operator_review": _json_safe(asdict(preview)),
            "broker_submission_allowed": False,
        }
        self.journal.seal_passport(passport_id, sealed_payload)
        DeterministicReferee(self.journal).issue(
            passport_id=passport_id,
            verdict=referee.verdict,
            reason_code=referee.reason_code,
            max_quantity=referee.max_quantity,
            max_limit_price=referee.max_limit_price,
        )
        confirmed = self.review_gate.confirm(
            passport_id=passport_id,
            decision=referee,
            selection=selection,
            source_mode=snapshot.source_mode,
        )
        if confirmed != preview:
            raise RuntimeError("Sealed operator review did not match the pre-seal preview")
        return ReplayDecisionResult(
            scenario_id=snapshot.scenario_id,
            passport_id=passport_id,
            proposal=analysis.proposal,
            ai_failure_code=analysis.failure_code,
            input_tokens=analysis.input_tokens,
            output_tokens=analysis.output_tokens,
            referee=referee,
            selection=selection,
            operator_review=confirmed,
            decision_id=str(decision_meta["decision_id"]),
            evidence_snapshot_hash=str(decision_meta["evidence_snapshot_hash"]),
            prompt_hash=str(decision_meta["prompt_hash"]),
            ai_latency_ms=int(decision_meta["latency_ms"]),
            ai_cached=bool(decision_meta["cached"]),
        )

    def _canonical_analysis(
        self,
        snapshot: EvidenceSnapshot,
        *,
        instructions: str,
        prompt_version: str,
    ) -> tuple[AnalysisResult, dict[str, Any]]:
        terra_payload = snapshot.terra_payload()
        evidence_json = json.dumps(terra_payload, sort_keys=True, separators=(",", ":"))
        epoch_payload = dict(terra_payload)
        epoch_payload.pop("scenario_id", None)
        epoch_payload.pop("decision_at", None)
        # Provenance is persisted and sent to Terra, but source labels do not make
        # otherwise identical normalized evidence a new decision epoch.
        epoch_payload.pop("provenance", None)
        epoch_json = json.dumps(epoch_payload, sort_keys=True, separators=(",", ":"))
        evidence_snapshot_hash = hashlib.sha256(epoch_json.encode("utf-8")).hexdigest()
        prompt_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        model = str(
            getattr(
                self.analysis_provider,
                "model_name",
                type(self.analysis_provider).__name__,
            )
        )
        decision_key = "|".join(
            (
                evidence_snapshot_hash,
                prompt_version,
                prompt_hash,
                model,
            )
        )
        decision_id = f"terra-{hashlib.sha256(decision_key.encode('utf-8')).hexdigest()[:32]}"
        claimed = self.journal.claim_ai_decision(
            decision_id=decision_id,
            evidence_snapshot_hash=evidence_snapshot_hash,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            model=model,
        )
        if claimed:
            started = time.perf_counter_ns()
            try:
                analysis = self.analysis_provider.analyze(
                    instructions=instructions,
                    evidence_json=evidence_json,
                )
            except Exception as exc:
                analysis = fail_closed_analysis(
                    model=model,
                    failure_code="AI_PROVIDER_ERROR",
                    failure_detail=type(exc).__name__,
                )
            latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
            analysis = self._validate_citations(snapshot, analysis)
            validation_status = (
                "FAIL_CLOSED"
                if analysis.failure_code is not None
                else (
                    "VALID_ABSTAIN"
                    if analysis.proposal.direction is ProposalDirection.NO_TRADE
                    else "VALID"
                )
            )
            self.journal.record_ai_attempt(
                decision_id=decision_id,
                attempt_number=1,
                transport_retry=False,
                status=analysis.failure_code or "COMPLETED",
                latency_ms=latency_ms,
            )
            self.journal.complete_ai_decision(
                decision_id=decision_id,
                response=_analysis_payload(analysis),
                abstention=(
                    analysis.failure_code is not None
                    or analysis.proposal.direction is ProposalDirection.NO_TRADE
                ),
                latency_ms=latency_ms,
                validation_status=validation_status,
                retry_count=0,
            )
            cached = False
        else:
            stored = self.journal.get_ai_decision(
                evidence_snapshot_hash=evidence_snapshot_hash,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                model=model,
            )
            if stored is None or stored["response"] is None:
                analysis = fail_closed_analysis(
                    model=model,
                    failure_code="AI_DECISION_IN_PROGRESS",
                    failure_detail="Canonical decision has no completed response",
                )
                latency_ms = 0
                validation_status = "FAIL_CLOSED_IN_PROGRESS"
            else:
                analysis = _analysis_from_payload(stored["response"])
                latency_ms = int(stored["latency_ms"] or 0)
                validation_status = str(stored["validation_status"])
            cached = True

        return analysis, {
            "decision_id": decision_id,
            "evidence_snapshot_id": decision_id,
            "evidence_snapshot_hash": evidence_snapshot_hash,
            "prompt_version": prompt_version,
            "prompt_hash": prompt_hash,
            "model": model,
            "timestamp": snapshot.decision_at.isoformat(),
            "latency_ms": latency_ms,
            "validation_status": validation_status,
            "abstention": (
                analysis.failure_code is not None
                or analysis.proposal.direction is ProposalDirection.NO_TRADE
            ),
            "retry_count": 0,
            "cached": cached,
        }

    @staticmethod
    def _validate_citations(
        snapshot: EvidenceSnapshot,
        analysis: AnalysisResult,
    ) -> AnalysisResult:
        if analysis.failure_code is not None:
            return analysis
        cited = set(analysis.proposal.evidence_ids) | set(
            analysis.proposal.invalidation.evidence_ids
        )
        if cited <= snapshot.evidence_ids:
            return analysis
        return fail_closed_analysis(
            model=analysis.requested_model,
            failure_code="CITATION_INVALID",
            failure_detail="Proposal cited evidence outside the sealed replay snapshot",
            input_tokens=analysis.input_tokens,
            output_tokens=analysis.output_tokens,
        )


def replay_distribution(results: list[ReplayDecisionResult]) -> dict[str, int]:
    distribution = {
        verdict.value: 0
        for verdict in RefereeVerdict
        if verdict is not RefereeVerdict.EXIT
    }
    for result in results:
        distribution[result.referee.verdict.value] += 1
    return distribution


def results_summary(results: list[ReplayDecisionResult]) -> dict[str, Any]:
    return {
        "scenario_count": len(results),
        "verdict_distribution": replay_distribution(results),
        "broker_submission_count": 0,
        "results": [
            _json_safe(
                {
                    "scenario_id": result.scenario_id,
                    "passport_id": result.passport_id,
                    "terra_direction": result.proposal.direction.value,
                    "terra_uncertainty": result.proposal.uncertainty.value,
                    "terra_failure_code": result.ai_failure_code,
                    "decision_id": result.decision_id,
                    "evidence_snapshot_hash": result.evidence_snapshot_hash,
                    "prompt_hash": result.prompt_hash,
                    "ai_latency_ms": result.ai_latency_ms,
                    "ai_cached": result.ai_cached,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "referee_verdict": result.referee.verdict.value,
                    "referee_reason": result.referee.reason_code,
                    "support_count": result.referee.support_count,
                    "opposition_count": result.referee.opposition_count,
                    "selected_symbol": (
                        None
                        if result.selection.candidate is None
                        else result.selection.candidate.symbol
                    ),
                    "eligible_quantity": result.selection.eligible_quantity,
                    "selection_reason": result.selection.reason_code,
                    "selection_rejections": result.selection.rejection_counts,
                    "operator_state": result.operator_review.state,
                    "broker_submission_allowed": result.operator_review.broker_submission_allowed,
                }
            )
            for result in results
        ],
    }
