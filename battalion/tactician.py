"""Bounded, advisory Tactician assessment for uncertain workflow admission.

Tactician is deliberately outside LangGraph and returns pre-admission evidence
only.  It cannot select a graph, mutate the recipe registry, or authorize a
workflow.  The application boundary decides when this module may be invoked.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.prompts.loader import load_system_prompt, prompt_contract_version
from battalion.state.models import CostSource
from battalion.workflow_admission import (
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    WorkflowAdmissionAssessment,
    WorkflowAdmissionOutcome,
)
from battalion.workflow_recipes import WorkflowRecipeRegistry


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


class TacticianError(ValueError):
    """Base class for expected Tactician assessment failures."""


class TacticianNotRequired(TacticianError):
    """A deterministic result already answers the admission question."""


class MalformedTacticianOutput(TacticianError):
    """The provider response cannot become bounded, inspectable evidence."""


class InvalidTacticianRecommendation(TacticianError):
    """The provider attempted to recommend something outside supplied policy."""


class TacticianRecommendationKind(str, Enum):
    RECIPE = "recipe"
    CLARIFICATION = "clarification"


class TacticianEvidence(BaseModel):
    """Revision-pinned, bounded material supplied to the advisory model."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=500)
    source: AdmissionEvidenceSource
    source_revision: str = Field(min_length=1, max_length=1_000)
    content: str = Field(min_length=1, max_length=20_000)
    authoritative: bool = True


class TacticianRecipeSummary(BaseModel):
    """A concise, exact semantic key for a registered workflow recipe."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    recipe_id: str = Field(min_length=1, max_length=200)
    recipe_version: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=2_000)


class TacticianAssessmentInput(BaseModel):
    """Everything Tactician may inspect for one uncertainty escalation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    admission_assessment: WorkflowAdmissionAssessment
    evidence: tuple[TacticianEvidence, ...] = Field(min_length=1, max_length=50)
    known_scope: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    registered_recipe_summaries: tuple[TacticianRecipeSummary, ...] = Field(
        min_length=1, max_length=20
    )
    mandatory_policy_references: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    human_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=30)

    @model_validator(mode="after")
    def validate_evidence_and_recipes(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Tactician evidence requires unique evidence IDs")
        recipe_keys = [
            (item.recipe_id, item.recipe_version)
            for item in self.registered_recipe_summaries
        ]
        if len(recipe_keys) != len(set(recipe_keys)):
            raise ValueError("Tactician recipe summaries require unique exact keys")
        work_item_present = any(
            item.source is AdmissionEvidenceSource.WORK_ITEM
            and item.source_revision == self.admission_assessment.work_item_revision
            for item in self.evidence
        )
        if not work_item_present:
            raise ValueError("Tactician input requires the assessed work-item revision")
        if self.admission_assessment.specification_revision is not None and not any(
            item.source is AdmissionEvidenceSource.SPECIFICATION
            and item.source_revision == self.admission_assessment.specification_revision
            for item in self.evidence
        ):
            raise ValueError("Tactician input requires the assessed specification revision")
        return self


class TacticianProvenance(BaseModel):
    """Inspectable model, prompt, and non-secret configuration provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    configured_model: str = Field(min_length=1, max_length=500)
    provider_model: str = Field(min_length=1, max_length=500)
    prompt_contract_version: str = Field(min_length=1, max_length=100)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    temperature: float
    max_retries: int = Field(ge=0)
    extra_parameter_names: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    usage: "TacticianUsageEvidence"


class TacticianUsageEvidence(BaseModel):
    """Sourced token and cost evidence for one pre-admission model call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost_source: CostSource = CostSource.UNKNOWN

    @model_validator(mode="after")
    def validate_cost_evidence(self) -> Self:
        if self.cost is None:
            if self.cost_currency is not None or self.cost_source is not CostSource.UNKNOWN:
                raise ValueError("unknown cost requires null currency and unknown source")
        elif self.cost_currency is None or self.cost_source is CostSource.UNKNOWN:
            raise ValueError("known cost requires currency and a known source")
        return self


class TacticianAssessment(BaseModel):
    """Concise, advisory evidence returned after an uncertain admission result."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    assessment_version: str = Field(default="1.0", pattern=r"^1\.0$")
    assessment_id: str = Field(min_length=1, max_length=200)
    recommendation_kind: TacticianRecommendationKind
    recommended_recipe_id: str | None = Field(default=None, min_length=1, max_length=200)
    recommended_recipe_version: str | None = Field(default=None, min_length=1, max_length=100)
    rationale: tuple[str, ...] = Field(min_length=1, max_length=8)
    risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    missing_evidence: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    input_evidence_references: tuple[AdmissionEvidenceReference, ...] = Field(
        min_length=1, max_length=50
    )
    provenance: TacticianProvenance

    @field_validator("rationale", "risk_flags", "missing_evidence")
    @classmethod
    def validate_bounded_text(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 2_000 for value in values):
            raise ValueError("Tactician assessment text must be non-empty and bounded")
        return values

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        has_recipe = self.recommended_recipe_id is not None
        has_version = self.recommended_recipe_version is not None
        if has_recipe != has_version:
            raise ValueError("Tactician recipe recommendations require an exact version")
        if self.recommendation_kind is TacticianRecommendationKind.RECIPE:
            if not has_recipe:
                raise ValueError("recipe recommendations require a registered recipe key")
        elif has_recipe:
            raise ValueError("clarification recommendations cannot select a recipe")
        return self


class _TacticianModelOutput(BaseModel):
    """Strict provider output before Battalion attaches evidence/provenance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    recommendation_kind: TacticianRecommendationKind
    recommended_recipe_id: str | None = Field(default=None, min_length=1, max_length=200)
    recommended_recipe_version: str | None = Field(default=None, min_length=1, max_length=100)
    rationale: tuple[str, ...] = Field(min_length=1, max_length=8)
    risk_flags: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    missing_evidence: tuple[str, ...] = Field(default_factory=tuple, max_length=30)

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        TacticianAssessment.model_validate({
            "assessment_id": "validation-only",
            "recommendation_kind": self.recommendation_kind,
            "recommended_recipe_id": self.recommended_recipe_id,
            "recommended_recipe_version": self.recommended_recipe_version,
            "rationale": self.rationale,
            "risk_flags": self.risk_flags,
            "missing_evidence": self.missing_evidence,
            "input_evidence_references": ({
                "evidence_id": "validation-only",
                "source": "context",
                "source_revision": "validation-only",
                "condition": "present",
                "authoritative": True,
            },),
            "provenance": {
                "configured_model": "validation-only",
                "provider_model": "validation-only",
                "prompt_contract_version": "validation-only",
                "prompt_sha256": "0" * 64,
                "temperature": 0,
                "max_retries": 0,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": None,
                    "cost_currency": None,
                    "cost_source": "unknown",
                },
            },
        })
        return self


def run_tactician(
    assessment_input: TacticianAssessmentInput,
    llm_config: NodeLLMConfig,
    *,
    registry: WorkflowRecipeRegistry,
    call_llm_fn: Callable = call_llm,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
) -> TacticianAssessment:
    """Obtain one advisory assessment for an ``UNCERTAIN`` result only.

    Provider failures deliberately propagate as the normal ``InfraFailure``;
    without a valid assessment this function cannot authorize compact work.
    """
    if assessment_input.admission_assessment.outcome is not WorkflowAdmissionOutcome.UNCERTAIN:
        raise TacticianNotRequired(
            "Tactician may run only when deterministic admission is uncertain"
        )

    summaries = {
        (summary.recipe_id, summary.recipe_version)
        for summary in assessment_input.registered_recipe_summaries
    }
    for recipe_id, recipe_version in summaries:
        try:
            registry.resolve(recipe_id, recipe_version)
        except ValueError as exc:
            raise InvalidTacticianRecommendation(
                f"Tactician input includes an unregistered recipe {recipe_id!r} "
                f"version {recipe_version!r}"
            ) from exc

    prompt = system_prompt or load_system_prompt("tactician", prompts_dir=prompts_dir)
    response = call_llm_fn(
        "tactician",
        llm_config,
        [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    assessment_input.model_dump(mode="json"), sort_keys=True
                ),
            },
        ],
    )
    model_output = _parse_model_output(response)
    if model_output.recommendation_kind is TacticianRecommendationKind.RECIPE:
        recipe_key = (
            model_output.recommended_recipe_id,
            model_output.recommended_recipe_version,
        )
        if recipe_key not in summaries:
            raise InvalidTacticianRecommendation(
                "Tactician may recommend only an exact registered recipe supplied in context"
            )

    input_references = tuple(
        AdmissionEvidenceReference(
            evidence_id=item.evidence_id,
            source=item.source,
            source_revision=item.source_revision,
            condition="present",
            authoritative=item.authoritative,
        )
        for item in assessment_input.evidence
    )
    provenance = TacticianProvenance(
        configured_model=llm_config.model,
        provider_model=(
            _response_value(response, "model", llm_config.model) or llm_config.model
        ),
        prompt_contract_version=prompt_contract_version("tactician"),
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        temperature=llm_config.temperature,
        max_retries=llm_config.max_retries,
        extra_parameter_names=tuple(sorted(llm_config.extra_params)),
        usage=_usage_evidence(response),
    )
    payload = model_output.model_dump(mode="json")
    assessment_id = _assessment_identity(
        assessment_input, payload, provenance
    )
    return TacticianAssessment(
        assessment_id=assessment_id,
        **payload,
        input_evidence_references=input_references,
        provenance=provenance,
    )


def _parse_model_output(response: Any) -> _TacticianModelOutput:
    content = extract_content(response)
    if not isinstance(content, str):
        raise MalformedTacticianOutput("Tactician output content must be text")
    match = _FENCE_RE.match(content.strip())
    try:
        value = json.loads(match.group(1) if match else content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MalformedTacticianOutput(f"Tactician output was not valid JSON: {exc}") from exc
    try:
        return _TacticianModelOutput.model_validate(value)
    except ValidationError as exc:
        raise MalformedTacticianOutput(
            f"Tactician output violated the assessment contract: {exc}"
        ) from exc


def _assessment_identity(
    assessment_input: TacticianAssessmentInput,
    model_output: dict[str, Any],
    provenance: TacticianProvenance,
) -> str:
    payload = {
        "input": assessment_input.model_dump(mode="json"),
        "output": model_output,
        "provenance": provenance.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "tactician:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _response_value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)


def _usage_evidence(response: Any) -> TacticianUsageEvidence:
    usage = _response_value(response, "usage") or {}
    hidden = _response_value(response, "_hidden_params") or {}
    input_tokens = _response_value(
        usage, "prompt_tokens", _response_value(usage, "input_tokens", 0)
    )
    output_tokens = _response_value(
        usage, "completion_tokens", _response_value(usage, "output_tokens", 0)
    )
    provider_cost = _response_value(usage, "cost")
    estimated_cost = _response_value(
        hidden, "response_cost", _response_value(response, "response_cost")
    )
    cost = provider_cost if provider_cost is not None else estimated_cost
    currency = _response_value(
        usage,
        "cost_currency",
        _response_value(
            hidden, "response_cost_currency", _response_value(response, "cost_currency")
        ),
    )
    source = (
        CostSource.PROVIDER_REPORTED
        if provider_cost is not None
        else CostSource.ESTIMATED
        if estimated_cost is not None
        else CostSource.UNKNOWN
    )
    return TacticianUsageEvidence(
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cost=str(cost) if cost is not None else None,
        cost_currency=(currency or "USD") if cost is not None else None,
        cost_source=source,
    )
