import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from cajnmnstr.ai import AnalysisResult, ProposalDirection, validate_proposal
from cajnmnstr.config import PAPER_API_URL, Settings
from cajnmnstr.decision_cycle import (
    TERRA_LIVE_INSTRUCTIONS,
    TERRA_LIVE_PROMPT_VERSION,
    EvidenceCalculator,
    ReplayDecisionPipeline,
)
from cajnmnstr.journal import Journal
from cajnmnstr.live_snapshot import LiveDecisionRunner, LiveEvidenceCollector
from cajnmnstr.models import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    HealthState,
    MarketClockSnapshot,
    MarketQuote,
    OptionChainSnapshot,
    RefereeVerdict,
    StockBarSnapshot,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "replay" / "spy-decision-cycle.json"
DECISION_AT = datetime(2026, 8, 31, 14, 35, tzinfo=UTC)


def app_settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path / "data"),
            "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "ALPACA_API_KEY": "fixture-key",
            "ALPACA_SECRET_KEY": "fixture-secret",
            "ALPACA_STOCK_FEED": "sip",
            "ALPACA_OPTIONS_FEED": "opra",
            "ALPACA_DATA_ENTITLEMENT": "algo_trader_plus",
            "CAJNMNSTR_ENTRY_ENABLED": "false",
            "CAJNMNSTR_POSITION_MANAGEMENT_ENABLED": "true",
            "CAJNMNSTR_BROKER_LOCK": "false",
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-openai-key",
            "CAJNMNSTR_SESSION_LOSS_LIMIT_USD": "1000",
        },
        load_local_file=False,
    )


def bars(decision_at: datetime = DECISION_AT) -> list[StockBarSnapshot]:
    start = decision_at - timedelta(minutes=65)
    result = []
    for index in range(13):
        opening = Decimal("500.00") + Decimal(index) * Decimal("0.30")
        closing = opening + Decimal("0.20")
        result.append(
            StockBarSnapshot(
                symbol="SPY",
                timestamp=start + timedelta(minutes=index * 5),
                timeframe_minutes=5,
                open=opening,
                high=closing + Decimal("0.10"),
                low=opening - Decimal("0.10"),
                close=closing,
                volume=Decimal(1000 + index * 25),
                vwap=None,
                feed="sip",
            )
        )
    return result


def daily_bars() -> list[StockBarSnapshot]:
    return [
        StockBarSnapshot(
            symbol="SPY",
            timestamp=datetime(2026, 8, 28, 4, 0, tzinfo=UTC),
            timeframe_minutes=None,
            open=Decimal("498"),
            high=Decimal("500"),
            low=Decimal("497"),
            close=Decimal("499"),
            volume=Decimal("70000000"),
            vwap=None,
            feed="sip",
        )
    ]


def option_chain(decision_at: datetime = DECISION_AT) -> list[OptionChainSnapshot]:
    common = {
        "bid_size": Decimal("80"),
        "ask_size": Decimal("90"),
        "quote_at": decision_at - timedelta(seconds=5),
        "trade_price": Decimal("4.15"),
        "trade_at": decision_at - timedelta(seconds=6),
        "gamma": Decimal("0.03"),
        "rho": Decimal("0.02"),
        "theta": Decimal("-0.12"),
        "vega": Decimal("0.18"),
        "feed": "opra",
    }
    return [
        OptionChainSnapshot(
            symbol="SPY260911C00504000",
            bid_price=Decimal("4.10"),
            ask_price=Decimal("4.20"),
            implied_volatility=Decimal("0.205"),
            delta=Decimal("0.50"),
            **common,
        ),
        OptionChainSnapshot(
            symbol="SPY260911P00504000",
            bid_price=Decimal("3.95"),
            ask_price=Decimal("4.05"),
            implied_volatility=Decimal("0.215"),
            delta=Decimal("-0.48"),
            **common,
        ),
    ]


class FakeLiveReader:
    def __init__(self) -> None:
        self.decision_at = DECISION_AT
        self.quote = MarketQuote(
            symbol="SPY",
            bid_price=Decimal("503.99"),
            ask_price=Decimal("504.01"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
            observed_at=self.decision_at - timedelta(seconds=5),
            feed="sip",
        )
        self.bars = bars(self.decision_at)
        self.daily = daily_bars()
        self.chain = option_chain(self.decision_at)
        self.positions = []
        self.open_orders = []
        self.all_orders = []
        self.submit_calls = 0

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id="fixture-account",
            account_number="fixture-number",
            status="ACTIVE",
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            buying_power=Decimal("100000"),
            options_buying_power=Decimal("100000"),
            options_approved_level=3,
            options_trading_level=3,
            trading_blocked=False,
        )

    def get_clock(self) -> MarketClockSnapshot:
        return MarketClockSnapshot(
            timestamp=self.decision_at,
            is_open=True,
            next_open=self.decision_at + timedelta(days=1),
            next_close=self.decision_at + timedelta(hours=5),
        )

    def get_spy_quote(self, *, feed=None):
        assert feed == "sip"
        return self.quote

    def get_spy_bars(self, *, start, end, timeframe_minutes, feed=None):
        assert start < end and timeframe_minutes == 5 and feed == "sip"
        return self.bars

    def get_spy_daily_bars(self, *, start, end, feed=None):
        assert start < end and feed == "sip"
        return self.daily

    def get_option_chain(
        self,
        *,
        expiration_gte,
        expiration_lte,
        strike_gte=None,
        strike_lte=None,
        feed=None,
    ):
        assert expiration_gte < expiration_lte and feed == "opra"
        assert strike_gte is None and strike_lte is None
        return self.chain

    def list_positions(self):
        return self.positions

    def list_open_orders(self):
        return self.open_orders

    def list_orders(self):
        return self.all_orders

    def submit_limit_order(self, intent):
        del intent
        self.submit_calls += 1
        raise AssertionError("The live Evidence Snapshot path must never submit")


class CapturingProvider:
    model_name = "gpt-5.6-terra-live-fixture"

    def __init__(self) -> None:
        self.payloads = []
        self.instructions = []

    def analyze(self, *, instructions: str, evidence_json: str) -> AnalysisResult:
        self.instructions.append(instructions)
        payload = json.loads(evidence_json)
        self.payloads.append(payload)
        proposal = validate_proposal(
            {
                "direction": "LONG_CALL",
                "time_horizon": "INTRADAY",
                "thesis": "Positive returns, VWAP, and opening range align.",
                "counterargument": "A fast reversal could invalidate the directional evidence.",
                "uncertainty": "LOW",
                "evidence_ids": [
                    "feature:return_5m",
                    "feature:vwap_relationship",
                    "feature:opening_range_state",
                ],
                "invalidation": {
                    "condition": "SPY loses VWAP and the opening range.",
                    "evidence_ids": [
                        "feature:vwap_relationship",
                        "feature:opening_range_state",
                    ],
                },
            }
        )
        return AnalysisResult(
            provider="fixture",
            requested_model=self.model_name,
            resolved_model=self.model_name,
            proposal=proposal,
            authority_disposition="PROPOSAL_ONLY",
            failure_code=None,
            failure_detail=None,
            input_tokens=100,
            output_tokens=50,
        )


def collect(tmp_path: Path, reader: FakeLiveReader | None = None):
    app = app_settings(tmp_path)
    fake = reader or FakeLiveReader()
    journal = Journal(app.journal_path)
    return (
        app,
        fake,
        journal,
        LiveEvidenceCollector(
            app,
            journal,
            fake,
            now=lambda: fake.decision_at,
        ).collect(),
    )


def test_live_source_normalizes_to_the_replay_snapshot_contract(tmp_path: Path) -> None:
    _, _, _, live = collect(tmp_path)
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    scenario = document["scenarios"][0]
    replay = EvidenceCalculator().build(document, scenario)

    assert set(live.snapshot.terra_payload()) == set(replay.terra_payload())
    assert live.snapshot.source_mode == "LIVE_PAPER_READ_ONLY"
    assert live.snapshot.source_provenance["stock_feed"] == "sip"
    assert live.snapshot.source_provenance["options_feed"] == "opra"
    assert live.snapshot.actionable_fresh is True
    assert live.snapshot.hard_failures == ()
    for feature in (
        "return_5m",
        "return_15m",
        "return_60m",
        "previous_close_gap",
        "vwap_relationship",
        "opening_range_state",
        "day_range_location",
        "realized_volatility",
        "preferred_expiry_contract_count",
        "atm_iv",
        "simple_skew",
    ):
        assert feature in live.snapshot.features
    assert "relative_volume" not in live.snapshot.features
    assert live.snapshot.features["event_calendar_state"] == "UNAVAILABLE"


def test_live_snapshot_reaches_shared_terra_referee_selector_contract(tmp_path: Path) -> None:
    app, _, journal, collection = collect(tmp_path)
    provider = CapturingProvider()
    pipeline = ReplayDecisionPipeline(app, journal, provider)
    decision = pipeline.run_snapshot(
        collection.snapshot,
        instructions=TERRA_LIVE_INSTRUCTIONS,
        prompt_version=TERRA_LIVE_PROMPT_VERSION,
    )
    repeated = pipeline.run_snapshot(
        replace(
            collection.snapshot,
            scenario_id="live-repeated-same-evidence",
            decision_at=collection.snapshot.decision_at + timedelta(seconds=10),
        ),
        instructions=TERRA_LIVE_INSTRUCTIONS,
        prompt_version=TERRA_LIVE_PROMPT_VERSION,
    )

    assert provider.payloads[0]["passport_mode"] == "LIVE_PAPER_READ_ONLY"
    assert len(provider.payloads) == 1
    assert repeated.decision_id == decision.decision_id
    assert repeated.ai_cached is True
    assert provider.instructions == [TERRA_LIVE_INSTRUCTIONS]
    assert decision.proposal.direction is ProposalDirection.LONG_CALL
    assert decision.referee.verdict is RefereeVerdict.APPROVE
    assert decision.selection.candidate is not None
    assert decision.operator_review.state == "READY_FOR_OPERATOR_REVIEW"
    assert decision.operator_review.broker_submission_allowed is False
    assert journal.get_passport(decision.passport_id)["state"] == "SEALED"


def test_stale_sip_and_stale_opra_fail_actionable_freshness(tmp_path: Path) -> None:
    sip_reader = FakeLiveReader()
    sip_reader.quote = replace(
        sip_reader.quote,
        observed_at=sip_reader.decision_at - timedelta(minutes=2),
    )
    _, _, _, sip = collect(tmp_path / "sip", sip_reader)
    assert "SPY_SIP_QUOTE" in sip.snapshot.stale_sources
    assert sip.snapshot.actionable_fresh is False

    opra_reader = FakeLiveReader()
    opra_reader.chain = [
        replace(item, quote_at=opra_reader.decision_at - timedelta(minutes=2))
        for item in opra_reader.chain
    ]
    _, _, _, opra = collect(tmp_path / "opra", opra_reader)
    assert "SPY_OPRA_OPTIONS" in opra.snapshot.stale_sources
    assert opra.snapshot.actionable_fresh is False


def test_snapshot_timestamp_is_captured_after_market_reads(tmp_path: Path) -> None:
    reader = FakeLiveReader()
    clock_at = reader.decision_at
    reader.quote = replace(reader.quote, observed_at=clock_at + timedelta(milliseconds=100))
    reader.chain = [
        replace(item, quote_at=clock_at + timedelta(milliseconds=800))
        for item in reader.chain
    ]
    captured_at = clock_at + timedelta(seconds=1)
    app = app_settings(tmp_path)
    collection = LiveEvidenceCollector(
        app,
        Journal(app.journal_path),
        reader,
        now=lambda: captured_at,
    ).collect()

    assert collection.snapshot.decision_at == captured_at
    assert collection.snapshot.actionable_fresh is True
    assert collection.snapshot.stale_sources == ()
    assert collection.snapshot.source_provenance["market_clock_at"] == clock_at.isoformat()
    assert collection.snapshot.source_provenance["snapshot_captured_at"] == captured_at.isoformat()


def test_closed_market_path_blocks_and_runner_never_submits(tmp_path: Path) -> None:
    app = app_settings(tmp_path)
    reader = FakeLiveReader()
    reader.get_clock = lambda: MarketClockSnapshot(
        timestamp=reader.decision_at,
        is_open=False,
        next_open=reader.decision_at + timedelta(days=1),
        next_close=reader.decision_at + timedelta(days=1, hours=7),
    )
    provider = CapturingProvider()
    outcome = LiveDecisionRunner(
        app,
        Journal(app.journal_path),
        reader,
        provider,
    ).run(
        dashboard_path=tmp_path / "dashboard-state.json",
        health_path=tmp_path / "health.json",
    )

    assert outcome.health.state is HealthState.PAUSED
    assert outcome.decision.referee.verdict is RefereeVerdict.BLOCK
    assert "MARKET_SESSION_CLOSED" in outcome.collection.snapshot.stale_sources
    assert outcome.dashboard["operational_state"] == "PAUSED"
    assert outcome.dashboard["market"]["session"] == "MARKET CLOSED"
    assert outcome.dashboard["controls"]["entry_enabled"] is False
    assert outcome.dashboard["controls"]["broker_submission_allowed"] is False
    assert outcome.dashboard["decision"]["state"] == "NOT_ELIGIBLE"
    assert reader.submit_calls == 0
    assert not hasattr(outcome, "submit")
    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["state"] == "PAUSED"
    assert health["entry_enabled"] is False
    assert health["broker_submission_allowed"] is False
    assert {item["component"] for item in health["components"]} >= {
        "alpaca",
        "broker_reconciliation",
        "spy_quote",
        "option_quote",
        "ai_provider",
        "evidence_store",
    }


def test_monitor_publication_refreshes_truth_without_invoking_terra(tmp_path: Path) -> None:
    app, reader, journal, collection = collect(tmp_path)
    provider = CapturingProvider()
    dashboard_path = tmp_path / "dashboard-state.json"
    health_path = tmp_path / "health.json"

    LiveDecisionRunner(app, journal, reader, provider).publish_monitor_state(
        collection,
        dashboard_path=dashboard_path,
        health_path=health_path,
    )

    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    health = json.loads(health_path.read_text(encoding="utf-8"))
    assert provider.payloads == []
    assert dashboard["proposal"]["direction"] == "NOT_EVALUATED"
    assert dashboard["decision"]["state"] == "MONITORING_PAUSED"
    assert dashboard["passport"]["sealed"] is False
    assert dashboard["controls"]["broker_submission_allowed"] is False
    assert health["state"] == "PAUSED"
    ai_health = next(
        item for item in health["components"] if item["component"] == "ai_provider"
    )
    assert ai_health["state"] == "HEALTHY"
    assert reader.submit_calls == 0


def test_missing_bars_and_missing_greeks_fail_safely(tmp_path: Path) -> None:
    missing_bars = FakeLiveReader()
    missing_bars.bars = missing_bars.bars[:5]
    _, _, _, incomplete = collect(tmp_path / "bars", missing_bars)
    assert "BARS_INSUFFICIENT" in incomplete.snapshot.hard_failures

    missing_greeks = FakeLiveReader()
    missing_greeks.chain = [replace(item, delta=None) for item in missing_greeks.chain]
    app, _, journal, collection = collect(tmp_path / "greeks", missing_greeks)
    decision = ReplayDecisionPipeline(
        app,
        journal,
        CapturingProvider(),
    ).run_snapshot(
        collection.snapshot,
        instructions=TERRA_LIVE_INSTRUCTIONS,
        prompt_version=TERRA_LIVE_PROMPT_VERSION,
    )
    assert decision.selection.candidate is None
    assert decision.selection.rejection_counts["MISSING_GREEKS"] == 1
    assert decision.operator_review.broker_submission_allowed is False


def test_broker_mismatch_blocks_live_entry(tmp_path: Path) -> None:
    reader = FakeLiveReader()
    reader.all_orders = [
        BrokerOrderSnapshot(
            broker_order_id="broker-order",
            client_order_id="cajnmnstr-unknown-client-order",
            symbol="SPY260911C00504000",
            status="filled",
            quantity=Decimal("1"),
            filled_quantity=Decimal("1"),
            filled_avg_price=Decimal("4.15"),
            limit_price=Decimal("4.20"),
            submitted_at=DECISION_AT - timedelta(minutes=5),
            updated_at=DECISION_AT - timedelta(minutes=4),
        )
    ]
    app, _, journal, collection = collect(tmp_path, reader)
    assert collection.reconciliation.matched is False
    assert "BROKER_RECONCILIATION_MISMATCH" in collection.snapshot.hard_failures

    decision = ReplayDecisionPipeline(
        app,
        journal,
        CapturingProvider(),
    ).run_snapshot(
        collection.snapshot,
        instructions=TERRA_LIVE_INSTRUCTIONS,
        prompt_version=TERRA_LIVE_PROMPT_VERSION,
    )
    assert decision.referee.verdict is RefereeVerdict.BLOCK
    assert decision.selection.candidate is None
    assert decision.operator_review.broker_submission_allowed is False
