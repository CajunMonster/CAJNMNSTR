from datetime import UTC, datetime
from decimal import Decimal

from cajnmnstr.cli import _chain_coverage, _redacted_account
from cajnmnstr.models import AccountSnapshot, OptionChainSnapshot


def test_account_verification_summary_never_contains_identifiers() -> None:
    account = AccountSnapshot(
        account_id="sensitive-account-id",
        account_number="sensitive-account-number",
        status="ACTIVE",
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=Decimal("100000"),
        options_buying_power=Decimal("100000"),
        options_approved_level=2,
        options_trading_level=2,
        trading_blocked=False,
    )

    summary = _redacted_account(account)

    rendered = str(summary)
    assert "sensitive-account-id" not in rendered
    assert "sensitive-account-number" not in rendered
    assert summary["account_id_present"] is True
    assert summary["account_number_present"] is True


def test_chain_coverage_reports_selector_fields_without_market_values() -> None:
    observed_at = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
    chain = [
        OptionChainSnapshot(
            symbol="SPY260918C00540000",
            bid_price=Decimal("4.20"),
            ask_price=Decimal("4.30"),
            bid_size=Decimal("2"),
            ask_size=Decimal("3"),
            quote_at=observed_at,
            trade_price=Decimal("4.25"),
            trade_at=observed_at,
            implied_volatility=Decimal("0.18"),
            delta=Decimal("0.46"),
            gamma=Decimal("0.03"),
            rho=None,
            theta=Decimal("-0.08"),
            vega=Decimal("0.11"),
            feed="indicative",
        )
    ]

    coverage = _chain_coverage(chain)

    assert coverage["snapshot_count"] == 1
    assert coverage["bid_ask_count"] == 1
    assert coverage["quote_timestamp_count"] == 1
    assert coverage["greeks"] == {
        "delta": 1,
        "gamma": 1,
        "rho": 0,
        "theta": 1,
        "vega": 1,
    }
