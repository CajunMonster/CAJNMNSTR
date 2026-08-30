from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from .models import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    MarketClockSnapshot,
    MarketQuote,
    OptionChainSnapshot,
    OptionContractSnapshot,
    OrderIntent,
    PositionSnapshot,
    StockBarSnapshot,
)


class BrokerReader(Protocol):
    def get_account(self) -> AccountSnapshot: ...

    def get_clock(self) -> MarketClockSnapshot: ...

    def get_option_contracts(
        self,
        *,
        expiration_gte: date,
        expiration_lte: date,
        strike_gte: Decimal | None = None,
        strike_lte: Decimal | None = None,
    ) -> list[OptionContractSnapshot]: ...

    def get_order_by_id(self, broker_order_id: str) -> BrokerOrderSnapshot: ...

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot: ...

    def list_orders(self) -> list[BrokerOrderSnapshot]: ...

    def list_open_orders(self) -> list[BrokerOrderSnapshot]: ...

    def list_positions(self) -> list[PositionSnapshot]: ...


class MarketDataReader(Protocol):
    def get_spy_quote(self, *, feed: str | None = None) -> MarketQuote: ...

    def get_option_chain(
        self,
        *,
        expiration_gte: date,
        expiration_lte: date,
        strike_gte: Decimal | None = None,
        strike_lte: Decimal | None = None,
        feed: str | None = None,
    ) -> list[OptionChainSnapshot]: ...

    def get_option_snapshot(self, symbol: str, *, feed: str) -> OptionChainSnapshot: ...

    def get_spy_bars(
        self,
        *,
        start: datetime,
        end: datetime,
        timeframe_minutes: int,
        feed: str | None = None,
    ) -> list[StockBarSnapshot]: ...

    def get_spy_daily_bars(
        self,
        *,
        start: datetime,
        end: datetime,
        feed: str | None = None,
    ) -> list[StockBarSnapshot]: ...


class PaperExecutor(Protocol):
    def submit_limit_order(self, intent: OrderIntent) -> BrokerOrderSnapshot: ...

    def cancel_order(self, broker_order_id: str) -> None: ...


class AnalysisProvider(Protocol):
    def analyze(self, *, instructions: str, evidence_json: str) -> object: ...
