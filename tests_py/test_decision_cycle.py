import json
from decimal import Decimal
from pathlib import Path

from cajnmnstr.ai import ProposalDirection
from cajnmnstr.config import PAPER_API_URL, Settings
from cajnmnstr.decision_cycle import (
    EvidenceCalculator,
    FixtureAnalysisProvider,
    ReplayDecisionPipeline,
    replay_distribution,
)
from cajnmnstr.journal import Journal
from cajnmnstr.models import AuthorityGrant, EventType, RefereeVerdict

FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "replay" / "spy-decision-cycle.json"
)


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path / "data"),
            "CAJNMNSTR_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "CAJNMNSTR_EXECUTION_ENABLED": "false",
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-only-key",
        },
        load_local_file=False,
    )


def replay_document() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def run_replay(tmp_path: Path):
    document = replay_document()
    proposals = {
        str(item["scenario_id"]): item["fixture_proposal"]
        for item in document["scenarios"]
    }
    app_settings = settings(tmp_path)
    journal = Journal(app_settings.journal_path)
    pipeline = ReplayDecisionPipeline(
        app_settings,
        journal,
        FixtureAnalysisProvider(proposals),
    )
    results = pipeline.run_document(document)
    return {item.scenario_id: item for item in results}, journal, pipeline


def test_deterministic_feature_calculation_uses_replay_numbers() -> None:
    document = replay_document()
    scenarios = {str(item["scenario_id"]): item for item in document["scenarios"]}
    calculator = EvidenceCalculator()
    bullish = calculator.build(document, scenarios["bullish-approve"])
    bearish = calculator.build(document, scenarios["bearish-approve"])
    reduce = calculator.build(document, scenarios["bullish-reduce"])
    weak = calculator.build(document, scenarios["weak-abstain"])

    assert bullish.features["return_5m"] > Decimal("0")
    assert bullish.features["return_15m"] > Decimal("0")
    assert bullish.features["return_60m"] > Decimal("0")
    assert bullish.features["previous_close_gap"] > Decimal("0")
    assert bullish.features["vwap_relationship"] == "ABOVE"
    assert bullish.features["opening_range_state"] == "ABOVE"
    assert bullish.features["realized_volatility"] > Decimal("0")
    assert bullish.features["preferred_expiry_contract_count"] == 4
    assert bullish.features["atm_iv"] == Decimal("0.2085")
    assert "simple_skew" not in bullish.features
    assert weak.features["simple_skew"] == Decimal("0.010")

    assert bearish.features["return_60m"] < Decimal("0")
    assert bearish.features["vwap_relationship"] == "BELOW"
    assert bearish.features["opening_range_state"] == "BELOW"

    assert reduce.features["return_5m"] < Decimal("0")
    assert reduce.features["return_15m"] < Decimal("0")
    assert reduce.features["return_60m"] > Decimal("0")
    assert reduce.features["vwap_relationship"] == "ABOVE"


def test_bullish_and_bearish_replays_produce_directional_candidates(tmp_path: Path) -> None:
    results, _, _ = run_replay(tmp_path)
    bullish = results["bullish-approve"]
    bearish = results["bearish-approve"]

    assert bullish.proposal.direction is ProposalDirection.LONG_CALL
    assert bullish.referee.verdict is RefereeVerdict.APPROVE
    assert bullish.selection.candidate is not None
    assert "C" in bullish.selection.candidate.symbol
    assert bullish.operator_review.state == "READY_FOR_OPERATOR_REVIEW"

    assert bearish.proposal.direction is ProposalDirection.LONG_PUT
    assert bearish.referee.verdict is RefereeVerdict.APPROVE
    assert bearish.selection.candidate is not None
    assert "P" in bearish.selection.candidate.symbol
    assert bearish.operator_review.state == "READY_FOR_OPERATOR_REVIEW"


def test_weak_invalid_and_stale_replays_fail_with_distinct_verdicts(tmp_path: Path) -> None:
    results, _, _ = run_replay(tmp_path)

    weak = results["weak-abstain"]
    assert weak.proposal.direction is ProposalDirection.NO_TRADE
    assert weak.referee.verdict is RefereeVerdict.ABSTAIN
    assert weak.selection.candidate is None

    invalid = results["hard-invalid-block"]
    assert invalid.referee.verdict is RefereeVerdict.BLOCK
    assert invalid.referee.reason_code == "HARD_DATA_INVALID"
    assert invalid.selection.candidate is None

    stale = results["stale-data-block"]
    assert stale.referee.verdict is RefereeVerdict.BLOCK
    assert stale.referee.reason_code == "STALE_REPLAY_EVIDENCE"
    assert stale.selection.candidate is None


def test_reduce_preserves_one_contract_authority(tmp_path: Path) -> None:
    results, _, _ = run_replay(tmp_path)
    reduced = results["bullish-reduce"]

    assert reduced.referee.verdict is RefereeVerdict.REDUCE
    assert reduced.referee.max_quantity == 1
    assert reduced.selection.candidate is not None
    assert reduced.selection.candidate.quantity == 1
    assert reduced.selection.eligible_quantity == 1
    assert reduced.operator_review.authority is AuthorityGrant.ENTRY_REDUCED


def test_contract_failures_never_create_actionable_selection(tmp_path: Path) -> None:
    results, _, _ = run_replay(tmp_path)

    missing = results["missing-greeks"]
    assert missing.referee.verdict is RefereeVerdict.APPROVE
    assert missing.selection.candidate is None
    assert missing.selection.rejection_counts["MISSING_GREEKS"] == 1

    spread = results["wide-spread"]
    assert spread.referee.verdict is RefereeVerdict.APPROVE
    assert spread.selection.candidate is None
    assert spread.selection.rejection_counts["SPREAD_TOO_WIDE"] == 1

    unsuitable = results["no-suitable-contract"]
    assert unsuitable.referee.verdict is RefereeVerdict.APPROVE
    assert unsuitable.selection.candidate is None
    assert unsuitable.selection.reason_code == "NO_SUITABLE_CONTRACT"
    assert unsuitable.selection.rejection_counts == {
        "DELTA_OUT_OF_RANGE": 1,
        "DTE_OUT_OF_RANGE": 1,
    }


def test_distribution_and_sealed_passports_are_durable(tmp_path: Path) -> None:
    results, journal, _ = run_replay(tmp_path)

    assert replay_distribution(list(results.values())) == {
        "APPROVE": 5,
        "REDUCE": 1,
        "ABSTAIN": 1,
        "BLOCK": 2,
    }
    for result in results.values():
        passport = journal.get_passport(result.passport_id)
        assert passport is not None
        assert passport["state"] == "SEALED"
        payload = passport["payload"]
        assert payload["broker_submission_allowed"] is False
        assert payload["evidence_snapshot"]["passport_mode"] == "REPLAY_ONLY"
        assert payload["terra"]["proposal"]["time_horizon"] == "INTRADAY"
        assert isinstance(payload["terra"]["proposal"]["invalidation"], dict)
        assert journal.get_referee_result(result.passport_id) is not None


def test_replay_has_no_broker_path_and_journals_stop_boundary(tmp_path: Path) -> None:
    results, journal, pipeline = run_replay(tmp_path)

    for method in (
        "submit",
        "submit_order",
        "replace_order",
        "cancel_order",
        "close_position",
        "execute",
    ):
        assert not hasattr(pipeline, method)
        assert not hasattr(pipeline.analysis_provider, method)

    assert journal.list_events(EventType.ORDER_ATTEMPT) == []
    assert journal.list_events(EventType.BROKER_LIFECYCLE) == []
    data_failures = journal.list_events(EventType.DATA_HEALTH_FAILURE)
    assert len(data_failures) == 2
    assert all(event["protective_action"] for event in data_failures)
    transitions = journal.list_events(EventType.AUTHORITY_TRANSITION)
    assert len(transitions) == len(results)
    assert all(event["payload"]["execution_allowed"] is False for event in transitions)
    assert all(event["payload"]["mock_broker_result"] is None for event in transitions)
    assert all(
        result.operator_review.broker_submission_allowed is False
        for result in results.values()
    )


def test_unknown_terra_citation_fails_to_abstain(tmp_path: Path) -> None:
    document = replay_document()
    scenario = document["scenarios"][0]
    scenario["fixture_proposal"]["evidence_ids"] = ["invented:evidence"]
    document["scenarios"] = [scenario]
    provider = FixtureAnalysisProvider(
        {str(scenario["scenario_id"]): scenario["fixture_proposal"]}
    )
    app_settings = settings(tmp_path)
    result = ReplayDecisionPipeline(
        app_settings,
        Journal(app_settings.journal_path),
        provider,
    ).run_document(document)[0]

    assert result.ai_failure_code == "CITATION_INVALID"
    assert result.proposal.direction is ProposalDirection.NO_TRADE
    assert result.referee.verdict is RefereeVerdict.ABSTAIN
    assert result.selection.candidate is None
