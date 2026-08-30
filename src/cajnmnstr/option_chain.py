from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import OptionChainSnapshot


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid numeric option-chain value: {value!r}") from exc


def _datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _field(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return None


def parse_option_chain_payload(
    payload: dict[str, Any], *, feed: str
) -> list[OptionChainSnapshot]:
    """Normalize Alpaca raw JSON or alpaca-py model dumps into stable records."""
    snapshots = payload.get("snapshots", payload)
    if not isinstance(snapshots, dict):
        raise ValueError("Option-chain payload must contain a snapshots object")

    parsed: list[OptionChainSnapshot] = []
    for symbol, raw_snapshot in snapshots.items():
        if not isinstance(symbol, str) or not symbol.startswith("SPY"):
            raise ValueError(f"Unexpected option symbol outside SPY scope: {symbol!r}")
        if not isinstance(raw_snapshot, dict):
            raise ValueError(f"Snapshot for {symbol} must be an object")
        quote = _field(raw_snapshot, "latestQuote", "latest_quote") or {}
        trade = _field(raw_snapshot, "latestTrade", "latest_trade") or {}
        greeks = raw_snapshot.get("greeks") or {}
        nested_values = (quote, trade, greeks)
        if not all(isinstance(value, dict) for value in nested_values):
            raise ValueError(f"Malformed nested snapshot for {symbol}")
        parsed.append(
            OptionChainSnapshot(
                symbol=symbol,
                bid_price=_decimal(_field(quote, "bp", "bid_price")),
                ask_price=_decimal(_field(quote, "ap", "ask_price")),
                bid_size=_decimal(_field(quote, "bs", "bid_size")),
                ask_size=_decimal(_field(quote, "as", "ask_size")),
                quote_at=_datetime(_field(quote, "t", "timestamp")),
                trade_price=_decimal(_field(trade, "p", "price")),
                trade_at=_datetime(_field(trade, "t", "timestamp")),
                implied_volatility=_decimal(
                    _field(raw_snapshot, "impliedVolatility", "implied_volatility")
                ),
                delta=_decimal(greeks.get("delta")),
                gamma=_decimal(greeks.get("gamma")),
                rho=_decimal(greeks.get("rho")),
                theta=_decimal(greeks.get("theta")),
                vega=_decimal(greeks.get("vega")),
                feed=feed,
            )
        )
    return sorted(parsed, key=lambda item: item.symbol)
