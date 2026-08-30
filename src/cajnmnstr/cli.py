from __future__ import annotations

import argparse
import json
import tomllib
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ai import TERRA_FIXTURE_INSTRUCTIONS, OpenAIResponsesAdapter
from .alpaca_adapter import AlpacaAdapter
from .config import Settings
from .decision_cycle import (
    FixtureAnalysisProvider,
    ReplayDecisionPipeline,
    results_summary,
)
from .health import HealthSupervisor, freshness_health
from .journal import Journal
from .models import EventType, HealthState
from .option_chain import parse_option_chain_payload


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _config_check(settings: Settings) -> int:
    print(_json(settings.redacted()))
    return 0


def _health(settings: Settings, *, live: bool) -> int:
    alpaca_probe = None
    ai_probe = None
    if live and settings.credentials_present:
        alpaca_probe = AlpacaAdapter(settings).probe
    if live and settings.ai_configured:
        ai_probe = OpenAIResponsesAdapter(settings).probe
    report = HealthSupervisor(
        settings,
        alpaca_probe=alpaca_probe,
        ai_probe=ai_probe,
    ).evaluate()
    print(_json(report.to_dict()))
    return 0 if report.state is HealthState.HEALTHY else 2


def _fixture_check(path: Path, feed: str) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    snapshots = parse_option_chain_payload(payload, feed=feed)
    print(
        _json(
            {
                "status": "ok",
                "feed": feed,
                "snapshot_count": len(snapshots),
                "symbols": [snapshot.symbol for snapshot in snapshots],
            }
        )
    )
    return 0


def _verify_terra(settings: Settings, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Terra fixture must be a JSON object")
    constraints = payload.get("constraints")
    if (
        payload.get("replay") is not True
        or not isinstance(constraints, dict)
        or constraints.get("market_data_mode") != "fixture"
        or constraints.get("broker_access") is not False
        or constraints.get("execution_enabled") is not False
    ):
        raise ValueError("Terra verification accepts only non-actionable fixture evidence")

    journal = Journal(settings.journal_path)
    journal.initialize()
    journal.probe()
    result = OpenAIResponsesAdapter(settings).analyze(
        instructions=TERRA_FIXTURE_INSTRUCTIONS,
        evidence_json=_json(payload),
    )
    passed = result.failure_code is None and result.resolved_model.startswith(
        settings.openai_model
    )
    event_payload = {
        "status": "PASS" if passed else "FAIL_CLOSED",
        "fixture_id": payload.get("fixture_id"),
        "provider": result.provider,
        "requested_model": result.requested_model,
        "resolved_model": result.resolved_model,
        "direction": result.proposal.direction.value,
        "uncertainty": result.proposal.uncertainty.value,
        "evidence_ids": list(result.proposal.evidence_ids),
        "authority_disposition": result.authority_disposition,
        "failure_code": result.failure_code,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "fixture_only": True,
        "entry_enabled": settings.entry_enabled,
        "entry_armed": settings.entry_armed,
        "position_management_enabled": settings.position_management_enabled,
        "position_management_armed": settings.position_management_armed,
        "broker_lock_active": settings.broker_lock,
    }
    journal.append_event(
        EventType.PROPOSAL,
        source="terra_fixture_verification",
        severity="INFO" if passed else "WARNING",
        payload=event_payload,
        protective_action=(
            None if passed else "ABSTAIN; do not create an actionable proposal."
        ),
    )
    print(_json(event_payload))
    return 0 if passed else 4


def _replay_cycle(settings: Settings, path: Path, *, live_terra: bool) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Decision-cycle fixture must be a JSON object")
    if live_terra:
        provider = OpenAIResponsesAdapter(settings)
        analysis_mode = "LIVE_TERRA_REPLAY_ONLY"
    else:
        scenarios = document.get("scenarios", [])
        if not isinstance(scenarios, list):
            raise ValueError("Decision-cycle scenarios must be a list")
        proposals = {
            str(item["scenario_id"]): item["fixture_proposal"]
            for item in scenarios
        }
        provider = FixtureAnalysisProvider(proposals)
        analysis_mode = "CHECKED_IN_TERRA_SHAPED_FIXTURES"

    results = ReplayDecisionPipeline(
        settings,
        Journal(settings.journal_path),
        provider,
    ).run_document(document)
    summary = results_summary(results)
    ai_failures = sum(item.ai_failure_code is not None for item in results)
    safe_stop = (
        summary["broker_submission_count"] == 0
        and not settings.entry_enabled
        and not settings.entry_armed
        and all(not item.operator_review.broker_submission_allowed for item in results)
    )
    status = "PASS" if safe_stop and ai_failures == 0 else "FAIL_CLOSED"
    print(
        _json(
            {
                "status": status,
                "analysis_mode": analysis_mode,
                "fixture_id": document.get("fixture_id"),
                "entry_enabled": settings.entry_enabled,
                "entry_armed": settings.entry_armed,
                "position_management_enabled": settings.position_management_enabled,
                "position_management_armed": settings.position_management_armed,
                "broker_lock_active": settings.broker_lock,
                "ai_failure_count": ai_failures,
                **summary,
            }
        )
    )
    return 0 if status == "PASS" else 4


def _mcp_config_check(path: Path) -> int:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".toml":
        payload = tomllib.loads(raw)
        servers = payload.get("mcp_servers")
    else:
        payload = json.loads(raw)
        servers = payload.get("mcpServers")
    if not isinstance(servers, dict) or len(servers) != 1:
        raise ValueError("Expected exactly one MCP server entry")
    server = next(iter(servers.values()))
    if server.get("command") != "uvx":
        raise ValueError("Alpaca MCP must launch through uvx")
    env = server.get("env", {})
    if env.get("ALPACA_PAPER_TRADE") != "true":
        raise ValueError("Alpaca MCP must be locked to paper mode")
    enabled = {part.strip() for part in env.get("ALPACA_TOOLSETS", "").split(",") if part}
    required = {"assets", "stock-data", "options-data", "news"}
    forbidden = {"account", "trading", "watchlists", "locates"}
    if enabled != required:
        raise ValueError(f"Read-only toolsets must equal {sorted(required)}")
    if enabled & forbidden:
        raise ValueError(f"Mutation-capable toolsets found: {sorted(enabled & forbidden)}")
    if path.suffix.lower() == ".toml":
        forwarded = set(server.get("env_vars", []))
        required_secrets = {"ALPACA_API_KEY", "ALPACA_SECRET_KEY"}
        if forwarded != required_secrets:
            raise ValueError("Codex must forward exactly the two Alpaca credential variables")
        if required_secrets & set(env):
            raise ValueError("Credential values must not appear in the checked-in Codex config")
    print(
        _json(
            {
                "status": "ok",
                "format": path.suffix.lower().lstrip("."),
                "paper": True,
                "toolsets": sorted(enabled),
            }
        )
    )
    return 0


def _redacted_account(account: Any) -> dict[str, Any]:
    return {
        "account_id_present": bool(account.account_id),
        "account_number_present": bool(account.account_number),
        "status": account.status,
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "options_buying_power": account.options_buying_power,
        "options_approved_level": account.options_approved_level,
        "options_trading_level": account.options_trading_level,
        "trading_blocked": account.trading_blocked,
    }


def _chain_coverage(chain: list[Any]) -> dict[str, Any]:
    greek_fields = ("delta", "gamma", "rho", "theta", "vega")
    return {
        "snapshot_count": len(chain),
        "bid_ask_count": sum(
            item.bid_price is not None and item.ask_price is not None for item in chain
        ),
        "quote_timestamp_count": sum(item.quote_at is not None for item in chain),
        "trade_timestamp_count": sum(item.trade_at is not None for item in chain),
        "implied_volatility_count": sum(
            item.implied_volatility is not None for item in chain
        ),
        "greeks": {
            field: sum(getattr(item, field) is not None for item in chain)
            for field in greek_fields
        },
    }


def _feed_failure(exc: Exception) -> dict[str, Any]:
    return {
        "authorized": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _verify_alpaca(settings: Settings, require_equity: Decimal) -> int:
    journal = Journal(settings.journal_path)
    stage = "journal_initialization"
    journal_ready = False
    try:
        journal.initialize()
        journal.probe()
        journal_ready = True

        stage = "paper_authentication"
        adapter = AlpacaAdapter(settings)
        account = adapter.get_account()

        stage = "account_positions"
        positions = adapter.list_positions()

        stage = "account_open_orders"
        open_orders = adapter.list_open_orders()

        stage = "market_clock"
        clock = adapter.get_clock()

        stage = "spy_equity_quote"
        quote = adapter.get_spy_quote()

        midpoint = (quote.bid_price + quote.ask_price) / Decimal("2")
        strike_gte = max(Decimal("0"), midpoint - Decimal("25"))
        strike_lte = midpoint + Decimal("25")
        today = date.today()
        expiration_gte = today + timedelta(days=7)
        expiration_lte = today + timedelta(days=45)

        stage = "spy_option_contracts"
        contracts = adapter.get_option_contracts(
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
        )

        stage = "spy_option_chain"
        chain = adapter.get_option_chain(
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
        )

        maximum_quote_age = timedelta(minutes=5) if clock.is_open else timedelta(hours=24)
        checked_at = datetime.now(UTC)
        quote_health = freshness_health(
            component="spy_quote",
            observed_at=quote.observed_at,
            maximum_age=maximum_quote_age,
            now=checked_at,
        )
        option_quote_times = [item.quote_at for item in chain if item.quote_at is not None]
        newest_option_quote = max(option_quote_times) if option_quote_times else None
        option_health = freshness_health(
            component="spy_option_chain",
            observed_at=newest_option_quote,
            maximum_age=maximum_quote_age,
            now=checked_at,
        )

        equity_feeds: dict[str, dict[str, Any]] = {
            settings.stock_feed: {
                "authorized": True,
                "observed_at": quote.observed_at,
            }
        }
        for feed in ("iex", "delayed_sip", "sip"):
            if feed in equity_feeds:
                continue
            try:
                feed_quote = adapter.get_spy_quote(feed=feed)
                equity_feeds[feed] = {
                    "authorized": True,
                    "observed_at": feed_quote.observed_at,
                }
            except Exception as exc:
                equity_feeds[feed] = _feed_failure(exc)

        options_feeds: dict[str, dict[str, Any]] = {
            settings.options_feed: {
                "authorized": True,
                "snapshot_count": len(chain),
                "newest_quote_at": newest_option_quote,
            }
        }
        probe_symbol = chain[0].symbol if chain else (contracts[0].symbol if contracts else None)
        for feed in ("indicative", "opra"):
            if feed in options_feeds:
                continue
            if probe_symbol is None:
                options_feeds[feed] = {
                    "authorized": False,
                    "error_type": "NoOptionContract",
                    "error": "No SPY option symbol was available for the feed probe.",
                }
                continue
            try:
                feed_snapshot = adapter.get_option_snapshot(probe_symbol, feed=feed)
                options_feeds[feed] = {
                    "authorized": True,
                    "quote_at": feed_snapshot.quote_at,
                }
            except Exception as exc:
                options_feeds[feed] = _feed_failure(exc)

        contract_metadata_count = sum(
            bool(item.symbol)
            and item.expiration_date is not None
            and item.strike_price is not None
            and item.contract_type in {"call", "put"}
            for item in contracts
        )
        coverage = _chain_coverage(chain)
        chain_by_symbol = {item.symbol: item for item in chain}
        sample_contract = next(
            (item for item in contracts if item.symbol in chain_by_symbol),
            None,
        )
        sample = None
        if sample_contract is not None:
            sample_snapshot = chain_by_symbol[sample_contract.symbol]
            sample = {
                "contract_symbol": sample_contract.symbol,
                "expiration": sample_contract.expiration_date,
                "strike": sample_contract.strike_price,
                "call_put": sample_contract.contract_type,
                "bid": sample_snapshot.bid_price,
                "ask": sample_snapshot.ask_price,
                "quote_at": sample_snapshot.quote_at,
                "trade_at": sample_snapshot.trade_at,
                "implied_volatility": sample_snapshot.implied_volatility,
                "greeks": {
                    "delta": sample_snapshot.delta,
                    "gamma": sample_snapshot.gamma,
                    "rho": sample_snapshot.rho,
                    "theta": sample_snapshot.theta,
                    "vega": sample_snapshot.vega,
                },
            }

        account_summary = _redacted_account(account)
        account_checks = {
            "equity_exact": account.equity == require_equity,
            "cash_exact": account.cash == require_equity,
            "expected_equity_and_cash": require_equity,
            "zero_positions": len(positions) == 0,
            "position_count": len(positions),
            "zero_open_orders": len(open_orders) == 0,
            "open_order_count": len(open_orders),
        }
        selector_fields = {
            "contract_metadata_available": contract_metadata_count > 0,
            "bid_ask_available": coverage["bid_ask_count"] > 0,
            "timestamps_available": coverage["quote_timestamp_count"] > 0,
            "greeks_available_where_provided": any(coverage["greeks"].values()),
        }

        data_health_components = (quote_health, option_health)
        if any(component.state is HealthState.PAUSED for component in data_health_components):
            operational_health_state = HealthState.PAUSED
        elif any(
            component.state is HealthState.DEGRADED for component in data_health_components
        ):
            operational_health_state = HealthState.DEGRADED
        else:
            operational_health_state = HealthState.HEALTHY

        stage = "live_health"
        live_health = HealthSupervisor(
            settings,
            alpaca_probe=adapter.probe,
            ai_probe=None,
        ).evaluate()

        checks = {
            "paper_mode": settings.paper_mode,
            "paper_endpoint": settings.alpaca_api_base_url,
            "authenticated": True,
            "account": account_summary,
            "account_checks": account_checks,
            "market_clock": asdict(clock),
            "spy_quote": asdict(quote),
            "spy_quote_health": quote_health.to_dict(),
            "spy_option_health": option_health.to_dict(),
            "quote_maximum_age_seconds": maximum_quote_age.total_seconds(),
            "option_contract_count": len(contracts),
            "option_contract_metadata_count": contract_metadata_count,
            "option_chain_coverage": coverage,
            "option_selector_fields": selector_fields,
            "option_sample": sample,
            "expiration_window": {
                "gte": expiration_gte,
                "lte": expiration_lte,
                "strike_gte": strike_gte,
                "strike_lte": strike_lte,
            },
            "equity_feed": {
                "configured_and_served": settings.stock_feed,
                "authenticated_feed_probes": equity_feeds,
            },
            "options_feed": {
                "configured_and_served": settings.options_feed,
                "authenticated_feed_probes": options_feeds,
            },
            "data_entitlement_recorded_before_probe": settings.data_entitlement,
            "live_health": live_health.to_dict(),
            "operational_data_health_state": operational_health_state.value,
            "entry_enabled": settings.entry_enabled,
            "entry_armed": settings.entry_armed,
            "position_management_enabled": settings.position_management_enabled,
            "position_management_armed": settings.position_management_armed,
            "broker_lock_active": settings.broker_lock,
        }
        passed = (
            settings.paper_mode
            and settings.alpaca_api_base_url == "https://paper-api.alpaca.markets"
            and account.status.upper() == "ACTIVE"
            and all(
                account_checks[name]
                for name in ("equity_exact", "cash_exact", "zero_positions", "zero_open_orders")
            )
            and bool(contracts)
            and bool(chain)
            and all(selector_fields.values())
            and quote_health.state is not HealthState.PAUSED
            and option_health.state is not HealthState.PAUSED
            and not settings.entry_enabled
            and not settings.entry_armed
            and not settings.broker_lock
        )

        for component in data_health_components:
            if component.state is not HealthState.HEALTHY:
                journal.append_event(
                    EventType.DATA_HEALTH_FAILURE,
                    source="owner_verification",
                    severity="CRITICAL" if component.state is HealthState.PAUSED else "WARNING",
                    payload=component.to_dict(),
                    protective_action=component.protective_action,
                )
                journal.open_incident(
                    component=component.component,
                    severity="CRITICAL" if component.state is HealthState.PAUSED else "WARNING",
                    state=component.state.value,
                    message=component.message,
                    protective_action=component.protective_action,
                )
            else:
                journal.resolve_incidents(component.component)

        journal.append_event(
            EventType.CONNECTION,
            source="owner_verification",
            severity="INFO" if passed else "CRITICAL",
            payload={"status": "PASS" if passed else "FAIL", **checks},
            protective_action=(
                None if passed else "Keep execution disabled and resolve failed checks."
            ),
        )
        print(
            _json(
                {
                    "status": "PASS" if passed else "FAIL",
                    **checks,
                    "journal": {
                        "connection_event_recorded": True,
                        "data_health_failure_events": sum(
                            component.state is not HealthState.HEALTHY
                            for component in data_health_components
                        ),
                        "credentials_recorded": False,
                        "account_identifiers_recorded": False,
                    },
                }
            )
        )
        return 0 if passed else 3
    except Exception as exc:
        failure = {
            "status": "FAIL",
            "stage": stage,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "paper_mode": settings.paper_mode,
            "paper_endpoint": settings.alpaca_api_base_url,
            "entry_enabled": settings.entry_enabled,
            "entry_armed": settings.entry_armed,
            "position_management_enabled": settings.position_management_enabled,
            "position_management_armed": settings.position_management_armed,
            "broker_lock_active": settings.broker_lock,
        }
        if journal_ready:
            try:
                journal.append_event(
                    EventType.CONNECTION,
                    source="owner_verification",
                    severity="CRITICAL",
                    payload=failure,
                    protective_action="Keep execution disabled; no order action is permitted.",
                )
                failure["journal_event_recorded"] = True
            except Exception as journal_exc:
                failure["journal_event_recorded"] = False
                failure["journal_error_type"] = type(journal_exc).__name__
                failure["journal_error"] = str(journal_exc)
        else:
            failure["journal_event_recorded"] = False
        print(_json(failure))
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cajnmnstr", description="CAJNMNSTR safe local operator commands"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "config-check", help="Validate configuration and print only redacted state"
    )
    health = subparsers.add_parser(
        "health", help="Evaluate health; provider network calls require --live"
    )
    health.add_argument("--live", action="store_true")

    fixture = subparsers.add_parser("fixture-check", help="Validate the option-chain fixture")
    fixture.add_argument(
        "--path", type=Path, default=Path("fixtures/alpaca/option-chain.json")
    )
    fixture.add_argument("--feed", default="indicative")

    terra = subparsers.add_parser(
        "verify-terra", help="Verify Terra with fixture/replay evidence only"
    )
    terra.add_argument(
        "--path", type=Path, default=Path("fixtures/ai/spy-weekend-replay.json")
    )

    replay = subparsers.add_parser(
        "replay-cycle",
        help="Run the SPY decision cycle and stop before broker submission",
    )
    replay.add_argument(
        "--path",
        type=Path,
        default=Path("fixtures/replay/spy-decision-cycle.json"),
    )
    replay.add_argument(
        "--live-terra",
        action="store_true",
        help="Invoke Terra with replay-only evidence; never contacts Alpaca",
    )

    mcp = subparsers.add_parser("mcp-config-check", help="Validate the read-only MCP example")
    mcp.add_argument(
        "--path", type=Path, default=Path("config/codex-mcp.example.toml")
    )

    verify = subparsers.add_parser(
        "verify-alpaca", help="Run authenticated read-only paper-account verification"
    )
    verify.add_argument("--require-equity", type=Decimal, default=Decimal("100000"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "config-check":
        return _config_check(settings)
    if args.command == "health":
        return _health(settings, live=args.live)
    if args.command == "fixture-check":
        return _fixture_check(args.path, args.feed)
    if args.command == "verify-terra":
        return _verify_terra(settings, args.path)
    if args.command == "replay-cycle":
        return _replay_cycle(settings, args.path, live_terra=args.live_terra)
    if args.command == "mcp-config-check":
        return _mcp_config_check(args.path)
    if args.command == "verify-alpaca":
        return _verify_alpaca(settings, args.require_equity)
    parser.error(f"Unknown command: {args.command}")
    return 64
