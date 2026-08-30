import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cajnmnstr.ai import (
    PROPOSAL_JSON_SCHEMA,
    OpenAIResponsesAdapter,
    ProposalDirection,
)
from cajnmnstr.config import PAPER_API_URL, TERRA_MODEL, Settings


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CAJNMNSTR_ENV": "paper",
            "CAJNMNSTR_DATA_ROOT": str(tmp_path),
            "ALPACA_API_BASE_URL": PAPER_API_URL,
            "CAJNMNSTR_EXECUTION_ENABLED": "false",
            "CAJNMNSTR_AI_PROVIDER": "openai",
            "OPENAI_API_KEY": "fixture-openai-key",
        },
        load_local_file=False,
    )


def valid_payload(direction: str = "LONG_CALL") -> dict[str, object]:
    return {
        "direction": direction,
        "time_horizon": "INTRADAY",
        "thesis": "Fixture evidence supports the stated direction.",
        "counterargument": "The fixture is stale and cannot support live authority.",
        "uncertainty": "HIGH",
        "evidence_ids": ["replay:price-001", "replay:risk-001"],
        "invalidation": {
            "condition": "Any live decision requires fresh authenticated evidence.",
            "evidence_ids": ["replay:risk-001"],
        },
    }


def response_for(payload: object, **overrides: object) -> SimpleNamespace:
    values = {
        "status": "completed",
        "model": TERRA_MODEL,
        "output_text": json.dumps(payload),
        "output": [],
        "usage": SimpleNamespace(input_tokens=100, output_tokens=50),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeResponses:
    def __init__(self, response: object = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


def adapter(tmp_path: Path, fake: FakeResponses) -> OpenAIResponsesAdapter:
    client = SimpleNamespace(responses=fake)
    return OpenAIResponsesAdapter(settings(tmp_path), client=client)


def test_valid_terra_schema_returns_proposal_only(tmp_path: Path) -> None:
    fake = FakeResponses(response_for(valid_payload()))
    result = adapter(tmp_path, fake).analyze(instructions="fixture", evidence_json="{}")
    assert result.failure_code is None
    assert result.proposal.direction is ProposalDirection.LONG_CALL
    assert result.authority_disposition == "PROPOSAL_ONLY"
    assert fake.kwargs is not None
    assert fake.kwargs["model"] == TERRA_MODEL
    assert fake.kwargs["tools"] == []
    assert fake.kwargs["store"] is False
    assert fake.kwargs["text"] == {
        "format": {
            "type": "json_schema",
            "name": "cajnmnstr_terra_proposal",
            "strict": True,
            "schema": PROPOSAL_JSON_SCHEMA,
        }
    }


def test_valid_no_trade_is_non_failure_abstain(tmp_path: Path) -> None:
    fake = FakeResponses(response_for(valid_payload("NO_TRADE")))
    result = adapter(tmp_path, fake).analyze(instructions="fixture", evidence_json="{}")
    assert result.failure_code is None
    assert result.proposal.direction is ProposalDirection.NO_TRADE
    assert result.authority_disposition == "ABSTAIN"


@pytest.mark.parametrize(
    ("response", "failure_code"),
    [
        (response_for("not-json"), "SCHEMA_INVALID"),
        (response_for({}, output_text="not-json"), "MALFORMED_OUTPUT"),
        (response_for({**valid_payload(), "unexpected": True}), "SCHEMA_INVALID"),
        (response_for(valid_payload(), status="incomplete"), "AI_INCOMPLETE"),
        (
            response_for(
                valid_payload(),
                output=[SimpleNamespace(type="function_call", content=[])],
            ),
            "UNEXPECTED_TOOL_CALL",
        ),
        (
            response_for(
                valid_payload(),
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="refusal")],
                    )
                ],
            ),
            "MODEL_REFUSAL",
        ),
    ],
)
def test_invalid_provider_results_fail_to_abstain(
    tmp_path: Path, response: SimpleNamespace, failure_code: str
) -> None:
    result = adapter(tmp_path, FakeResponses(response)).analyze(
        instructions="fixture", evidence_json="{}"
    )
    assert result.failure_code == failure_code
    assert result.proposal.direction is ProposalDirection.NO_TRADE
    assert result.authority_disposition == "ABSTAIN"


def test_timeout_fails_to_abstain(tmp_path: Path) -> None:
    result = adapter(tmp_path, FakeResponses(error=TimeoutError())).analyze(
        instructions="fixture", evidence_json="{}"
    )
    assert result.failure_code == "AI_TIMEOUT"
    assert result.proposal.direction is ProposalDirection.NO_TRADE
    assert result.authority_disposition == "ABSTAIN"


def test_adapter_exposes_no_broker_or_execution_interface(tmp_path: Path) -> None:
    terra = adapter(tmp_path, FakeResponses(response_for(valid_payload())))
    for method in (
        "submit_order",
        "submit_limit_order",
        "replace_order",
        "cancel_order",
        "close_position",
        "execute",
    ):
        assert not hasattr(terra, method)
