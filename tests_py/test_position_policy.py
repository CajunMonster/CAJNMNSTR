from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cajnmnstr.models import (
    ExitReason,
    OptionChainSnapshot,
    PositionSnapshot,
    RefereeResult,
)
from cajnmnstr.position_management import DeterministicPositionManager
from cajnmnstr.position_policy import (
    INITIAL_POLICY_VERSION,
    STRUCTURAL_FORMULA_VERSION,
    build_initial_position_plan,
    derive_structural_invalidation,
)

NOW = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
CALL = "SPY260918C00540000"
PUT = "SPY260918P00540000"


def payload(
    direction: str,
    symbol: str,
    *,
    price: str = "540",
    vwap: str = "539",
    opening_low: str = "538",
    opening_high: str = "541",
) -> dict[str, object]:
    return {
        "evidence_snapshot": {
            "decision_at": NOW.isoformat(),
            "features": {
                "underlying_price": price,
                "vwap": vwap,
                "opening_range_low": opening_low,
                "opening_range_high": opening_high,
            },
        },
        "terra": {"proposal": {"direction": direction}},
        "option_selection": {"candidate": {"symbol": symbol}},
        "broker_submission_allowed": False,
    }


def referee() -> RefereeResult:
    return RefereeResult(
        result_id="fixture-result",
        passport_id="fixture-passport",
        verdict="APPROVE",
        max_quantity=1,
        max_limit_price=Decimal("4.25"),
        reason_code="FIXTURE",
        created_at=NOW,
    )


@pytest.mark.parametrize(
    ("direction", "symbol", "evidence", "expected_comparison", "expected_level"),
    [
        ("LONG_CALL", CALL, payload("LONG_CALL", CALL), "lte", Decimal("539")),
        (
            "LONG_PUT",
            PUT,
            payload(
                "LONG_PUT",
                PUT,
                vwap="541",
                opening_low="539",
                opening_high="542",
            ),
            "gte",
            Decimal("541"),
        ),
    ],
)
def test_structural_invalidation_is_nearest_level_on_invalidating_side(
    direction, symbol, evidence, expected_comparison, expected_level
) -> None:
    result = derive_structural_invalidation(evidence, symbol=symbol)
    assert result.direction == direction
    assert result.rule.feature_name == "underlying_price"
    assert result.rule.comparison == expected_comparison
    assert result.rule.threshold == expected_level


@pytest.mark.parametrize(
    ("direction", "symbol", "kwargs"),
    [
        (
            "LONG_CALL",
            CALL,
            {"vwap": "541", "opening_low": "542", "opening_high": "543"},
        ),
        (
            "LONG_PUT",
            PUT,
            {"vwap": "539", "opening_low": "537", "opening_high": "540"},
        ),
    ],
)
def test_missing_structural_level_blocks_position_plan(direction, symbol, kwargs) -> None:
    with pytest.raises(ValueError, match="No sealed VWAP/opening"):
        build_initial_position_plan(
            payload(direction, symbol, **kwargs),
            referee(),
            plan_id="cajnmnstr-plan-blocked",
            entry_passport_id="fixture-passport",
            symbol=symbol,
            maximum_quantity=1,
            strategy_version="competition-v1",
            rationale="Owner-approved initial paper policy.",
        )


def test_initial_policy_is_exact_and_forced_eod_is_335_et() -> None:
    plan = build_initial_position_plan(
        payload("LONG_CALL", CALL),
        referee(),
        plan_id="cajnmnstr-plan-policy",
        entry_passport_id="fixture-passport",
        symbol=CALL,
        maximum_quantity=1,
        strategy_version=INITIAL_POLICY_VERSION,
        rationale="Owner-approved initial paper policy.",
    )
    assert plan.stop_loss_fraction == Decimal("0.25")
    assert plan.profit_target_fraction == Decimal("0.35")
    assert plan.time_stop_duration_minutes == 75
    assert plan.forced_eod_at.timetz().replace(tzinfo=None) == time(15, 35)
    assert plan.invalidation_formula_version == STRUCTURAL_FORMULA_VERSION


@pytest.mark.parametrize(
    ("direction", "symbol", "invalidation_price"),
    [("LONG_CALL", CALL, Decimal("539")), ("LONG_PUT", PUT, Decimal("541"))],
)
def test_call_and_put_structural_levels_drive_deterministic_exit(
    direction, symbol, invalidation_price
) -> None:
    evidence = (
        payload(direction, symbol)
        if direction == "LONG_CALL"
        else payload(
            direction,
            symbol,
            vwap="541",
            opening_low="539",
            opening_high="542",
        )
    )
    plan = build_initial_position_plan(
        evidence,
        referee(),
        plan_id=f"cajnmnstr-plan-{direction.lower()}",
        entry_passport_id="fixture-passport",
        symbol=symbol,
        maximum_quantity=1,
        strategy_version="competition-v1",
        rationale="Owner-approved initial paper policy.",
    )
    quote = OptionChainSnapshot(
        symbol=symbol,
        bid_price=Decimal("4.00"),
        ask_price=Decimal("4.10"),
        bid_size=Decimal("10"),
        ask_size=Decimal("10"),
        quote_at=NOW,
        trade_price=Decimal("4.00"),
        trade_at=NOW,
        implied_volatility=Decimal("0.20"),
        delta=Decimal("0.50") if direction == "LONG_CALL" else Decimal("-0.50"),
        gamma=Decimal("0.03"),
        rho=Decimal("0.02"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.15"),
        feed="opra",
    )
    collection = SimpleNamespace(
        option_chain=(quote,),
        snapshot=SimpleNamespace(
            decision_at=NOW,
            features={"underlying_price": invalidation_price},
        ),
    )
    position = PositionSnapshot(
        symbol=symbol,
        quantity=Decimal("1"),
        side="long",
        market_value=Decimal("400"),
        average_entry_price=Decimal("4.00"),
        unrealized_pl=Decimal("0"),
    )
    manager = object.__new__(DeterministicPositionManager)
    evaluation = manager._evaluate(
        plan,
        position,
        collection,
        fill_confirmed_at=NOW - timedelta(minutes=1),
        time_stop_at=NOW + timedelta(minutes=74),
    )
    assert evaluation.reason is ExitReason.THESIS_INVALIDATION
