# OPRA entitlement checkpoint

Public requirements were checked on August 29, 2026. The dedicated paper account was subsequently verified through authenticated read-only requests on the same date.

## Current verified entitlement

- Algo Trader Plus is active.
- Recent SIP equity data is authorized and served when `feed=sip` is requested.
- OPRA option-chain data is authorized and served when `feed=opra` is requested.
- The SPY OPRA probe returned bid/ask, quote and trade timestamps, implied volatility, delta, gamma, theta, vega, and rho.
- The probe occurred while the market was closed. OPRA timestamps exceeded the 24-hour freshness policy, so operational authority correctly remained `PAUSED`.
- Execution remained disabled and unarmed.

## Finding

For a Trading API account, Basic provides IEX equities and the indicative options feed. Current real-time OPRA options data is part of the $99/month Algo Trader Plus market-data subscription. Alpaca's documented dashboard path is **Plans & Features → Market Data → Upgrade to AlgoTrader Plus**.

Alpaca staff specifically clarified that the API error `OPRA agreement is not signed` can be misleading: for a Basic account requesting current OPRA data, it means the account lacks Algo Trader Plus. The public documentation does not describe a separate standalone OPRA-agreement action that enables real-time OPRA while remaining on Basic.

## CAJNMNSTR policy

- The verified local configuration uses `ALPACA_STOCK_FEED=sip`, `ALPACA_OPTIONS_FEED=opra`, and `ALPACA_DATA_ENTITLEMENT=algo_trader_plus`.
- Reject SIP or OPRA configuration unless the verified Plus entitlement is recorded locally.
- Treat indicative options values as development/debug information if used as an explicit fallback, not live trading evidence.
- Never infer OPRA access from options trading approval level; trading permission and market-data entitlement are separate.
- An OPRA denial must fail loudly and cannot be worked around by relabeling indicative data.

## Sources

- [Alpaca Market Data API subscription plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Alpaca Market Data FAQ: AlgoTrader Plus dashboard flow](https://docs.alpaca.markets/us/docs/market-data-faq)
- [Alpaca options guide: OPRA requires Algo Trader Plus](https://alpaca.markets/learn/how-to-trade-options-with-alpaca)
- [Alpaca staff clarification of the OPRA error](https://forum.alpaca.markets/t/error-opra-agreement-is-not-signed/18445)
