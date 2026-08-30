import json
from decimal import Decimal
from pathlib import Path

import pytest

from cajnmnstr.option_chain import parse_option_chain_payload


def test_official_shape_fixture_parses() -> None:
    path = Path("fixtures/alpaca/option-chain.json")
    values = parse_option_chain_payload(json.loads(path.read_text()), feed="indicative")
    assert len(values) == 2
    assert values[0].symbol == "SPY260918C00540000"
    assert values[0].bid_price == Decimal("4.2")
    assert values[0].ask_price == Decimal("4.3")
    assert values[0].delta == Decimal("0.4621")
    assert values[0].gamma == Decimal("0.031")
    assert values[0].rho is None
    assert values[0].theta == Decimal("-0.083")
    assert values[0].vega == Decimal("0.112")
    assert values[0].quote_at is not None


def test_scope_violation_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside SPY scope"):
        parse_option_chain_payload({"snapshots": {"QQQ260918C00540000": {}}}, feed="indicative")
