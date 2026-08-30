# OPRA entitlement checkpoint

Checked against Alpaca's public materials on August 29, 2026.

## Finding

For a Trading API account, Basic provides IEX equities and the indicative options feed. Current real-time OPRA options data is part of the $99/month Algo Trader Plus market-data subscription. Alpaca's documented dashboard path is **Plans & Features → Market Data → Upgrade to AlgoTrader Plus**.

Alpaca staff specifically clarified that the API error `OPRA agreement is not signed` can be misleading: for a Basic account requesting current OPRA data, it means the account lacks Algo Trader Plus. The public documentation does not describe a separate standalone OPRA-agreement action that enables real-time OPRA while remaining on Basic. Any required subscriber terms should be reviewed in the official upgrade flow, but subscription was neither started nor purchased during this checkpoint.

## CAJNMNSTR policy

- Keep `ALPACA_STOCK_FEED=iex` and `ALPACA_OPTIONS_FEED=indicative` unless the owner intentionally changes the subscription and a new authenticated read-only probe verifies the entitlement.
- Treat indicative options values as development/debug information, not live trading evidence.
- Never infer OPRA access from options trading approval level; trading permission and market-data entitlement are separate.
- An OPRA denial must fail loudly and cannot be worked around by relabeling indicative data.

## Sources

- [Alpaca Market Data API subscription plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Alpaca Market Data FAQ: AlgoTrader Plus dashboard flow](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca options guide: OPRA requires Algo Trader Plus](https://alpaca.markets/learn/how-to-trade-options-with-alpaca)
- [Alpaca staff clarification of the OPRA error](https://forum.alpaca.markets/t/error-opra-agreement-is-not-signed/18445)
