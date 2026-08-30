from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .config import Settings
from .models import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    MarketClockSnapshot,
    MarketQuote,
    OptionChainSnapshot,
    OptionContractSnapshot,
    OrderIntent,
    PositionSnapshot,
)
from .option_chain import parse_option_chain_payload


def _decimal(value: Any, default: str = "0") -> Decimal:
    return Decimal(str(default if value is None else value))


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return value
    raise TypeError(f"Cannot normalize Alpaca object of type {type(value).__name__}")


class AlpacaAdapter:
    """Authenticated read adapter plus an independently gated paper-order adapter."""

    def __init__(self, settings: Settings) -> None:
        settings.require_credentials()
        from alpaca.data.enums import DataFeed, OptionsFeed
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.settings = settings
        self._stock_feeds = {
            "iex": DataFeed.IEX,
            "sip": DataFeed.SIP,
            "delayed_sip": DataFeed.DELAYED_SIP,
        }
        self._options_feeds = {
            "indicative": OptionsFeed.INDICATIVE,
            "opra": OptionsFeed.OPRA,
        }
        self._trading = TradingClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            paper=True,
        )
        self._stock = StockHistoricalDataClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )
        self._options = OptionHistoricalDataClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )

    def probe(self) -> None:
        self._trading.get_account()

    def get_account(self) -> AccountSnapshot:
        account = self._trading.get_account()
        return AccountSnapshot(
            account_id=str(account.id),
            account_number=str(account.account_number),
            status=_value(account.status),
            equity=_decimal(account.equity),
            cash=_decimal(account.cash),
            buying_power=_decimal(account.buying_power),
            options_buying_power=(
                _decimal(account.options_buying_power)
                if account.options_buying_power is not None
                else None
            ),
            options_approved_level=account.options_approved_level,
            options_trading_level=account.options_trading_level,
            trading_blocked=bool(account.trading_blocked),
        )

    def get_clock(self) -> MarketClockSnapshot:
        clock = self._trading.get_clock()
        return MarketClockSnapshot(
            timestamp=clock.timestamp,
            is_open=bool(clock.is_open),
            next_open=clock.next_open,
            next_close=clock.next_close,
        )

    def get_spy_quote(self, *, feed: str | None = None) -> MarketQuote:
        from alpaca.data.requests import StockLatestQuoteRequest

        selected_feed = feed or self.settings.stock_feed
        quotes = self._stock.get_stock_latest_quote(
            StockLatestQuoteRequest(
                symbol_or_symbols=["SPY"], feed=self._stock_feeds[selected_feed]
            )
        )
        quote = quotes["SPY"]
        return MarketQuote(
            symbol="SPY",
            bid_price=_decimal(quote.bid_price),
            ask_price=_decimal(quote.ask_price),
            bid_size=_decimal(quote.bid_size),
            ask_size=_decimal(quote.ask_size),
            observed_at=quote.timestamp,
            feed=selected_feed,
        )

    def get_option_contracts(
        self,
        *,
        expiration_gte: date,
        expiration_lte: date,
        strike_gte: Decimal | None = None,
        strike_lte: Decimal | None = None,
    ) -> list[OptionContractSnapshot]:
        from alpaca.trading.enums import AssetStatus
        from alpaca.trading.requests import GetOptionContractsRequest

        response = self._trading.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=["SPY"],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte,
                strike_price_gte=str(strike_gte) if strike_gte is not None else None,
                strike_price_lte=str(strike_lte) if strike_lte is not None else None,
                limit=100,
            )
        )
        contracts = response.option_contracts or []
        return [
            OptionContractSnapshot(
                contract_id=str(contract.id),
                symbol=contract.symbol,
                underlying_symbol=contract.underlying_symbol,
                expiration_date=contract.expiration_date,
                contract_type=_value(contract.type),
                strike_price=_decimal(contract.strike_price),
                tradable=bool(contract.tradable),
                status=_value(contract.status),
            )
            for contract in contracts
        ]

    def get_option_chain(
        self,
        *,
        expiration_gte: date,
        expiration_lte: date,
        strike_gte: Decimal | None = None,
        strike_lte: Decimal | None = None,
        feed: str | None = None,
    ) -> list[OptionChainSnapshot]:
        from alpaca.data.requests import OptionChainRequest

        selected_feed = feed or self.settings.options_feed
        response = self._options.get_option_chain(
            OptionChainRequest(
                underlying_symbol="SPY",
                feed=self._options_feeds[selected_feed],
                expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte,
                strike_price_gte=float(strike_gte) if strike_gte is not None else None,
                strike_price_lte=float(strike_lte) if strike_lte is not None else None,
            )
        )
        payload = {"snapshots": {symbol: _dump(snapshot) for symbol, snapshot in response.items()}}
        return parse_option_chain_payload(payload, feed=selected_feed)

    def get_option_snapshot(self, symbol: str, *, feed: str) -> OptionChainSnapshot:
        from alpaca.data.requests import OptionSnapshotRequest

        response = self._options.get_option_snapshot(
            OptionSnapshotRequest(
                symbol_or_symbols=[symbol],
                feed=self._options_feeds[feed],
            )
        )
        snapshot = response[symbol]
        payload = {"snapshots": {symbol: _dump(snapshot)}}
        return parse_option_chain_payload(payload, feed=feed)[0]

    def submit_limit_order(self, intent: OrderIntent) -> BrokerOrderSnapshot:
        self.settings.require_order_authority(intent.position_intent)
        from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        order = self._trading.submit_order(
            LimitOrderRequest(
                symbol=intent.symbol,
                qty=intent.quantity,
                side={"buy": OrderSide.BUY, "sell": OrderSide.SELL}[intent.side],
                time_in_force=TimeInForce.DAY,
                limit_price=float(intent.limit_price),
                client_order_id=intent.client_order_id,
                position_intent={
                    "buy_to_open": PositionIntent.BUY_TO_OPEN,
                    "sell_to_close": PositionIntent.SELL_TO_CLOSE,
                }[intent.position_intent],
            )
        )
        return self._order(order)

    def get_order_by_id(self, broker_order_id: str) -> BrokerOrderSnapshot:
        return self._order(self._trading.get_order_by_id(broker_order_id))

    def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderSnapshot:
        return self._order(self._trading.get_order_by_client_id(client_order_id))

    def list_orders(self) -> list[BrokerOrderSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        )
        return [self._order(order) for order in orders]

    def list_open_orders(self) -> list[BrokerOrderSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        )
        return [self._order(order) for order in orders]

    def list_positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                symbol=position.symbol,
                quantity=_decimal(position.qty),
                side=_value(position.side),
                market_value=_decimal(position.market_value),
                average_entry_price=_decimal(position.avg_entry_price),
                unrealized_pl=_decimal(position.unrealized_pl),
            )
            for position in self._trading.get_all_positions()
        ]

    def cancel_order(self, broker_order_id: str) -> None:
        self.settings.require_position_management_armed()
        self._trading.cancel_order_by_id(broker_order_id)

    @staticmethod
    def _order(order: Any) -> BrokerOrderSnapshot:
        return BrokerOrderSnapshot(
            broker_order_id=str(order.id),
            client_order_id=order.client_order_id,
            symbol=order.symbol or "",
            status=_value(order.status),
            quantity=_decimal(order.qty),
            filled_quantity=_decimal(order.filled_qty),
            limit_price=_decimal(order.limit_price) if order.limit_price is not None else None,
            submitted_at=order.submitted_at,
            updated_at=order.updated_at,
        )
