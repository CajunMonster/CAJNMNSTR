from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .config import TERRA_MODEL, Settings
from .errors import ConfigurationError


class ProposalDirection(StrEnum):
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    NO_TRADE = "NO_TRADE"


class ProposalUncertainty(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class StructuredProposal:
    direction: ProposalDirection
    thesis: str
    counterargument: str
    uncertainty: ProposalUncertainty
    evidence_ids: tuple[str, ...]
    invalidation: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    provider: str
    requested_model: str
    resolved_model: str
    proposal: StructuredProposal
    authority_disposition: str
    failure_code: str | None
    failure_detail: str | None
    input_tokens: int | None
    output_tokens: int | None


PROPOSAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "direction",
        "thesis",
        "counterargument",
        "uncertainty",
        "evidence_ids",
        "invalidation",
    ],
    "properties": {
        "direction": {
            "type": "string",
            "enum": [direction.value for direction in ProposalDirection],
        },
        "thesis": {"type": "string"},
        "counterargument": {"type": "string"},
        "uncertainty": {
            "type": "string",
            "enum": [level.value for level in ProposalUncertainty],
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "invalidation": {"type": "string"},
    },
}


TERRA_FIXTURE_INSTRUCTIONS = """You are the CAJNMNSTR Terra analyst.
Analyze only the supplied fixture or replay evidence. Never treat it as live or actionable.
Choose exactly LONG_CALL, LONG_PUT, or NO_TRADE. Cite only supplied evidence IDs.
Give the strongest counterargument, an uncertainty level, and a falsifiable invalidation.
You have no tools, broker access, execution authority, position sizing authority,
or order authority.
When evidence is stale, incomplete, conflicting, or insufficient, choose NO_TRADE.
"""


def validate_proposal(payload: Any) -> StructuredProposal:
    if not isinstance(payload, dict):
        raise ValueError("Proposal must be a JSON object")
    expected = set(PROPOSAL_JSON_SCHEMA["required"])
    if set(payload) != expected:
        raise ValueError("Proposal fields do not exactly match the required schema")

    try:
        direction = ProposalDirection(payload["direction"])
        uncertainty = ProposalUncertainty(payload["uncertainty"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Proposal enum value is invalid") from exc

    text_fields: dict[str, str] = {}
    for name in ("thesis", "counterargument", "invalidation"):
        value = payload[name]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(f"Proposal field {name} must be non-empty text")
        text_fields[name] = value.strip()

    raw_evidence_ids = payload["evidence_ids"]
    if not isinstance(raw_evidence_ids, list) or not raw_evidence_ids:
        raise ValueError("Proposal evidence_ids must be a non-empty list")
    evidence_ids: list[str] = []
    for evidence_id in raw_evidence_ids:
        if not isinstance(evidence_id, str) or not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}", evidence_id
        ):
            raise ValueError("Proposal evidence ID is invalid")
        evidence_ids.append(evidence_id)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("Proposal evidence IDs must be unique")

    return StructuredProposal(
        direction=direction,
        thesis=text_fields["thesis"],
        counterargument=text_fields["counterargument"],
        uncertainty=uncertainty,
        evidence_ids=tuple(evidence_ids),
        invalidation=text_fields["invalidation"],
    )


def _abstain(
    *,
    model: str,
    failure_code: str,
    failure_detail: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> AnalysisResult:
    return AnalysisResult(
        provider="openai",
        requested_model=model,
        resolved_model=model,
        proposal=StructuredProposal(
            direction=ProposalDirection.NO_TRADE,
            thesis="No actionable proposal was accepted.",
            counterargument="The AI result failed a required provider or schema check.",
            uncertainty=ProposalUncertainty.HIGH,
            evidence_ids=("system:ai_failure",),
            invalidation="Retry only with validated fixture evidence and a healthy Terra response.",
        ),
        authority_disposition="ABSTAIN",
        failure_code=failure_code,
        failure_detail=failure_detail,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class OpenAIResponsesAdapter:
    """Structured proposal adapter with no broker, MCP, or execution interface."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        if settings.ai_provider != "openai":
            raise ConfigurationError("OpenAI adapter selected for a different AI provider")
        if not settings.openai_api_key:
            raise ConfigurationError("OPENAI_API_KEY is required")
        if settings.openai_model != TERRA_MODEL:
            raise ConfigurationError(f"Terra baseline requires {TERRA_MODEL}")
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.ai_timeout_seconds,
                max_retries=0,
            )
        self._client = client
        self._model = settings.openai_model
        self._timeout = settings.ai_timeout_seconds

    def probe(self) -> None:
        self._client.models.retrieve(self._model)

    def analyze(self, *, instructions: str, evidence_json: str) -> AnalysisResult:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=evidence_json,
                reasoning={"effort": "medium"},
                max_output_tokens=1200,
                store=False,
                tools=[],
                parallel_tool_calls=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "cajnmnstr_terra_proposal",
                        "strict": True,
                        "schema": PROPOSAL_JSON_SCHEMA,
                    }
                },
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            return _abstain(
                model=self._model,
                failure_code="AI_TIMEOUT",
                failure_detail=type(exc).__name__,
            )
        except Exception as exc:
            return _abstain(
                model=self._model,
                failure_code="AI_PROVIDER_ERROR",
                failure_detail=type(exc).__name__,
            )

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None
        resolved_model = str(getattr(response, "model", self._model))
        raw_status = getattr(response, "status", "")
        status = str(getattr(raw_status, "value", raw_status))
        if status != "completed":
            return _abstain(
                model=self._model,
                failure_code="AI_INCOMPLETE",
                failure_detail=status or "missing_status",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        for item in getattr(response, "output", ()) or ():
            raw_item_type = getattr(item, "type", "")
            item_type = str(getattr(raw_item_type, "value", raw_item_type))
            if "tool" in item_type or item_type.endswith("_call"):
                return _abstain(
                    model=self._model,
                    failure_code="UNEXPECTED_TOOL_CALL",
                    failure_detail=item_type,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            for content in getattr(item, "content", ()) or ():
                if getattr(content, "type", None) == "refusal":
                    return _abstain(
                        model=self._model,
                        failure_code="MODEL_REFUSAL",
                        failure_detail="refusal",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

        try:
            payload = json.loads(response.output_text)
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            return _abstain(
                model=self._model,
                failure_code="MALFORMED_OUTPUT",
                failure_detail=type(exc).__name__,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        try:
            proposal = validate_proposal(payload)
        except ValueError as exc:
            return _abstain(
                model=self._model,
                failure_code="SCHEMA_INVALID",
                failure_detail=str(exc),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        return AnalysisResult(
            provider="openai",
            requested_model=self._model,
            resolved_model=resolved_model,
            proposal=proposal,
            authority_disposition=(
                "ABSTAIN"
                if proposal.direction is ProposalDirection.NO_TRADE
                else "PROPOSAL_ONLY"
            ),
            failure_code=None,
            failure_detail=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
