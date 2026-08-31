from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .models import InvalidationRule, PositionManagementPlan, RefereeResult

NEW_YORK = ZoneInfo("America/New_York")

INITIAL_POLICY_VERSION = "competition-paper-position-policy-v1"
STRUCTURAL_FORMULA_VERSION = "nearest-sealed-vwap-opening-boundary-v1"
PREMIUM_STOP_FRACTION = Decimal("0.25")
PROFIT_TARGET_FRACTION = Decimal("0.35")
TIME_STOP_DURATION_MINUTES = 75
FORCED_EOD_TIME = time(15, 35)


@dataclass(frozen=True, slots=True)
class StructuralInvalidation:
    rule: InvalidationRule
    direction: str
    inputs: tuple[tuple[str, Decimal], ...]


def _decimal_feature(features: dict[str, Any], name: str) -> Decimal:
    try:
        value = Decimal(str(features[name]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"Sealed evidence is missing valid structural feature {name}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Sealed structural feature {name} must be finite and positive")
    return value


def _entry_context(payload: dict[str, Any], symbol: str) -> tuple[str, dict[str, Any], datetime]:
    try:
        snapshot = payload["evidence_snapshot"]
        features = snapshot["features"]
        decision_at = datetime.fromisoformat(str(snapshot["decision_at"]))
        direction = str(payload["terra"]["proposal"]["direction"])
        candidate = payload["option_selection"]["candidate"]
        selected_symbol = str(candidate["symbol"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "Sealed entry Passport lacks complete decision/selection evidence"
        ) from exc
    if decision_at.tzinfo is None:
        raise ValueError("Sealed entry decision timestamp requires a timezone")
    if direction not in {"LONG_CALL", "LONG_PUT"}:
        raise ValueError("Position authority requires a LONG_CALL or LONG_PUT entry decision")
    if selected_symbol != symbol:
        raise ValueError("Position plan contract must match the sealed deterministic selection")
    match = re.fullmatch(r"SPY\d{6}([CP])\d{8}", symbol)
    expected_kind = "C" if direction == "LONG_CALL" else "P"
    if match is None or match.group(1) != expected_kind:
        raise ValueError("Selected OCC contract type must match the sealed entry direction")
    if not isinstance(features, dict):
        raise ValueError("Sealed entry features must be a mapping")
    return direction, features, decision_at


def derive_structural_invalidation(
    payload: dict[str, Any],
    *,
    symbol: str,
) -> StructuralInvalidation:
    """Freeze the nearest sealed VWAP/opening boundary on the invalidation side."""
    direction, features, _ = _entry_context(payload, symbol)
    decision_price = _decimal_feature(features, "underlying_price")
    vwap = _decimal_feature(features, "vwap")
    opening_low = _decimal_feature(features, "opening_range_low")
    opening_high = _decimal_feature(features, "opening_range_high")
    inputs = (
        ("underlying_price", decision_price),
        ("vwap", vwap),
        ("opening_range_low", opening_low),
        ("opening_range_high", opening_high),
    )
    structural_levels = (vwap, opening_low, opening_high)
    if direction == "LONG_CALL":
        candidates = tuple(level for level in structural_levels if level < decision_price)
        if not candidates:
            raise ValueError("No sealed VWAP/opening support exists below the call decision price")
        threshold = max(candidates)
        comparison = "lte"
    else:
        candidates = tuple(level for level in structural_levels if level > decision_price)
        if not candidates:
            raise ValueError(
                "No sealed VWAP/opening resistance exists above the put decision price"
            )
        threshold = min(candidates)
        comparison = "gte"
    return StructuralInvalidation(
        rule=InvalidationRule(
            feature_name="underlying_price",
            comparison=comparison,
            threshold=threshold,
        ),
        direction=direction,
        inputs=inputs,
    )


def build_initial_position_plan(
    payload: dict[str, Any],
    referee: RefereeResult,
    *,
    plan_id: str,
    entry_passport_id: str,
    symbol: str,
    maximum_quantity: int,
    strategy_version: str,
    rationale: str,
) -> PositionManagementPlan:
    invalidation = derive_structural_invalidation(payload, symbol=symbol)
    _, _, decision_at = _entry_context(payload, symbol)
    decision_date = decision_at.astimezone(NEW_YORK).date()
    forced_eod_at = datetime.combine(decision_date, FORCED_EOD_TIME, tzinfo=NEW_YORK)
    return PositionManagementPlan(
        plan_id=plan_id,
        entry_passport_id=entry_passport_id,
        symbol=symbol,
        maximum_quantity=maximum_quantity,
        stop_loss_fraction=PREMIUM_STOP_FRACTION,
        profit_target_fraction=PROFIT_TARGET_FRACTION,
        invalidation=invalidation.rule,
        invalidation_formula_version=STRUCTURAL_FORMULA_VERSION,
        invalidation_inputs=invalidation.inputs,
        direction=invalidation.direction,
        entry_referee_verdict=referee.verdict,
        time_stop_duration_minutes=TIME_STOP_DURATION_MINUTES,
        forced_eod_at=forced_eod_at,
        strategy_version=strategy_version,
        rationale=rationale,
    )


def validate_initial_position_plan(
    plan: PositionManagementPlan,
    payload: dict[str, Any],
    referee: RefereeResult,
) -> None:
    expected = build_initial_position_plan(
        payload,
        referee,
        plan_id=plan.plan_id,
        entry_passport_id=plan.entry_passport_id,
        symbol=plan.symbol,
        maximum_quantity=plan.maximum_quantity,
        strategy_version=plan.strategy_version,
        rationale=plan.rationale,
    )
    if plan != expected:
        raise ValueError("Position plan does not match the owner-approved initial policy")
