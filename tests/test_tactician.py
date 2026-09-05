"""Credential-free contract tests for BTN-140 Tactician escalation."""

from __future__ import annotations

import json

import pytest
from support.responses import json_response

from battalion.application import AssessTactician, assess_tactician
from battalion.config import BattalionConfig
from battalion.llm.litellm_client import InfraFailure, NodeLLMConfig
from battalion.tactician import (
    InvalidTacticianRecommendation,
    MalformedTacticianOutput,
    TacticianAssessmentInput,
    TacticianEvidence,
    TacticianNotRequired,
    TacticianRecipeSummary,
    run_tactician,
)
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    CompactAdmissionEvidence,
    WorkflowAdmissionEvidence,
    assess_workflow_admission,
)
from battalion.workflow_recipes import (
    DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    FULL_IMPLEMENTATION_RECIPE,
    WorkflowRecipeRegistry,
)


def _reference(
    evidence_id: str,
    *,
    source: AdmissionEvidenceSource,
    establishes: frozenset[CompactAdmissionEvidence] = frozenset(),
    condition: AdmissionEvidenceCondition = AdmissionEvidenceCondition.PRESENT,
) -> AdmissionEvidenceReference:
    return AdmissionEvidenceReference(
        evidence_id=evidence_id,
        source=source,
        source_revision="revision-1",
        condition=condition,
        authoritative=True,
        establishes=establishes,
    )


def _uncertain_input(*, human_constraints: tuple[str, ...] = ()) -> TacticianAssessmentInput:
    admission = assess_workflow_admission(
        WorkflowAdmissionEvidence(
            work_item_revision="revision-1",
            evidence_references=(
                _reference("work-item", source=AdmissionEvidenceSource.WORK_ITEM),
            ),
        )
    )
    return TacticianAssessmentInput(
        admission_assessment=admission,
        evidence=(
            TacticianEvidence(
                evidence_id="work-item",
                source=AdmissionEvidenceSource.WORK_ITEM,
                source_revision="revision-1",
                content="Add an option, but the governing architecture is unclear.",
            ),
            TacticianEvidence(
                evidence_id="accepted-adr",
                source=AdmissionEvidenceSource.ADR,
                source_revision="adr-42",
                content="Existing services own their own configuration boundaries.",
            ),
        ),
        known_scope=("battalion/application.py",),
        registered_recipe_summaries=(
            TacticianRecipeSummary(
                recipe_id="full-implementation-run",
                recipe_version="1.0",
                summary="Architecture, RED/GREEN delivery, and independent review.",
            ),
        ),
        mandatory_policy_references=("workflow-admission/1.0",),
        human_constraints=human_constraints,
    )


def _response(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "recommendation_kind": "recipe",
        "recommended_recipe_id": "full-implementation-run",
        "recommended_recipe_version": "1.0",
        "rationale": ["The configuration boundary remains materially uncertain."],
        "risk_flags": ["material-architecture-boundary"],
        "missing_evidence": ["accepted architecture decision"],
    }
    payload.update(overrides)
    return json_response(payload)


def test_uncertain_work_receives_a_bounded_full_recipe_recommendation() -> None:
    captured: dict[str, object] = {}

    def fake_call(role, config, messages):
        captured.update(role=role, config=config, messages=messages)
        return _response()

    assessment = run_tactician(
        _uncertain_input(),
        NodeLLMConfig(model="tactician-model", temperature=0.2),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=fake_call,
    )

    assert captured["role"] == "tactician"
    assert assessment.assessment_version == "1.0"
    assert assessment.recommended_recipe_id == "full-implementation-run"
    assert assessment.recommended_recipe_version == "1.0"
    assert assessment.provenance.configured_model == "tactician-model"
    assert assessment.provenance.prompt_contract_version == "tactician/v1"
    assert [item.evidence_id for item in assessment.input_evidence_references] == [
        "work-item", "accepted-adr"
    ]


def test_tactician_can_recommend_clarification_for_missing_architecture() -> None:
    assessment = run_tactician(
        _uncertain_input(),
        NodeLLMConfig(model="tactician-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=lambda *_: _response(
            recommendation_kind="clarification",
            recommended_recipe_id=None,
            recommended_recipe_version=None,
            missing_evidence=["architecture owner decision"],
        ),
    )

    assert assessment.recommendation_kind.value == "clarification"
    assert assessment.recommended_recipe_id is None


def test_tactician_can_recommend_a_supplied_registered_compact_recipe() -> None:
    compact_recipe = FULL_IMPLEMENTATION_RECIPE.model_copy(
        update={"recipe_id": "compact-implementation-run"}
    )
    registry = WorkflowRecipeRegistry((FULL_IMPLEMENTATION_RECIPE, compact_recipe))
    assessment_input = _uncertain_input().model_copy(
        update={
            "registered_recipe_summaries": (
                TacticianRecipeSummary(
                    recipe_id="compact-implementation-run",
                    recipe_version="1.0",
                    summary="Configured compact recipe with retained review.",
                ),
                TacticianRecipeSummary(
                    recipe_id="full-implementation-run",
                    recipe_version="1.0",
                    summary="Full default workflow.",
                ),
            )
        }
    )
    assessment = run_tactician(
        assessment_input,
        NodeLLMConfig(model="tactician-model"),
        registry=registry,
        call_llm_fn=lambda *_: _response(
            recommended_recipe_id="compact-implementation-run",
            recommended_recipe_version="1.0",
            risk_flags=[],
            missing_evidence=[],
        ),
    )

    assert assessment.recommended_recipe_id == "compact-implementation-run"


def test_human_context_is_supplied_without_granting_tactician_authority() -> None:
    captured: dict[str, object] = {}

    def fake_call(_role, _config, messages):
        captured["payload"] = json.loads(messages[1]["content"])
        return _response()

    assessment = run_tactician(
        _uncertain_input(human_constraints=("The operator believes this is plumbing.",)),
        NodeLLMConfig(model="tactician-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=fake_call,
    )

    assert captured["payload"]["human_constraints"] == [
        "The operator believes this is plumbing."
    ]
    assert assessment.recommended_recipe_id == "full-implementation-run"


def test_full_required_and_compact_admissible_results_do_not_call_tactician() -> None:
    full_required = assess_workflow_admission(
        WorkflowAdmissionEvidence(
            work_item_revision="revision-1",
            evidence_references=(
                _reference("work-item", source=AdmissionEvidenceSource.WORK_ITEM),
                _reference(
                    "architecture-risk",
                    source=AdmissionEvidenceSource.ADR,
                    condition=AdmissionEvidenceCondition.PRESENT,
                ).model_copy(
                    update={"hard_risk_flags": frozenset(("material-architecture-boundary",))}
                ),
            ),
        )
    )
    input_value = _uncertain_input().model_copy(
        update={"admission_assessment": full_required}
    )
    called = False

    def fake_call(*_args):
        nonlocal called
        called = True
        return _response()

    with pytest.raises(TacticianNotRequired):
        run_tactician(
            input_value,
            NodeLLMConfig(model="tactician-model"),
            registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
            call_llm_fn=fake_call,
        )
    assert called is False

    compact_admissible = assess_workflow_admission(
        WorkflowAdmissionEvidence(
            work_item_revision="revision-1",
            evidence_references=(
                _reference("work-item", source=AdmissionEvidenceSource.WORK_ITEM),
                *(
                    _reference(
                        f"compact-{fact.value}",
                        source=AdmissionEvidenceSource.REPOSITORY,
                        establishes=frozenset((fact,)),
                    )
                    for fact in CompactAdmissionEvidence
                ),
            ),
        )
    )
    with pytest.raises(TacticianNotRequired):
        run_tactician(
            _uncertain_input().model_copy(
                update={"admission_assessment": compact_admissible}
            ),
            NodeLLMConfig(model="tactician-model"),
            registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
            call_llm_fn=fake_call,
        )
    assert called is False


def test_tactician_cannot_invent_or_mutate_a_recipe() -> None:
    with pytest.raises(InvalidTacticianRecommendation, match="only an exact registered"):
        run_tactician(
            _uncertain_input(),
            NodeLLMConfig(model="tactician-model"),
            registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
            call_llm_fn=lambda *_: _response(recommended_recipe_id="invented"),
        )


def test_malformed_output_and_provider_failure_produce_no_assessment() -> None:
    with pytest.raises(MalformedTacticianOutput):
        run_tactician(
            _uncertain_input(),
            NodeLLMConfig(model="tactician-model"),
            registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
            call_llm_fn=lambda *_: {"choices": [{"message": {"content": "not json"}}]},
        )
    failure = InfraFailure("tactician", "tactician-model", 1, RuntimeError("offline"))
    with pytest.raises(InfraFailure) as raised:
        run_tactician(
            _uncertain_input(),
            NodeLLMConfig(model="tactician-model"),
            registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
            call_llm_fn=lambda *_: (_ for _ in ()).throw(failure),
        )
    assert raised.value is failure


def test_application_resolves_configured_tactician_model_not_a_model_selected_by_role() -> None:
    captured: dict[str, object] = {}

    def fake_call(_role, config, _messages):
        captured["model"] = config.model
        return _response()

    assessment = assess_tactician(
        AssessTactician(
            assessment_input=_uncertain_input(),
            config=BattalionConfig(
                models={
                    "default": NodeLLMConfig(model="default-model"),
                    "tactician": NodeLLMConfig(model="configured-tactician-model"),
                }
            ),
        ),
        call_llm_fn=fake_call,
    )

    assert captured["model"] == "configured-tactician-model"
    assert assessment.provenance.configured_model == "configured-tactician-model"


def test_assessment_records_provider_model_and_sourced_token_cost_evidence() -> None:
    response = _response()
    response.update({
        "model": "provider-model",
        "usage": {
            "prompt_tokens": 17,
            "completion_tokens": 5,
            "cost": "0.012",
            "cost_currency": "USD",
        },
    })
    assessment = run_tactician(
        _uncertain_input(),
        NodeLLMConfig(model="configured-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=lambda *_: response,
    )

    assert assessment.provenance.provider_model == "provider-model"
    assert assessment.provenance.usage.input_tokens == 17
    assert assessment.provenance.usage.output_tokens == 5
    assert str(assessment.provenance.usage.cost) == "0.012"
    assert assessment.provenance.usage.cost_source.value == "provider-reported"


def test_assessment_identity_is_stable_for_identical_input_output_and_provenance() -> None:
    def fake_call(*_args):
        return _response()

    first = run_tactician(
        _uncertain_input(),
        NodeLLMConfig(model="tactician-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=fake_call,
    )
    second = run_tactician(
        _uncertain_input(),
        NodeLLMConfig(model="tactician-model"),
        registry=DEFAULT_WORKFLOW_RECIPE_REGISTRY,
        call_llm_fn=fake_call,
    )

    assert first.assessment_id == second.assessment_id


def test_input_rejects_missing_assessed_work_item_and_unregistered_context_recipe() -> None:
    with pytest.raises(ValueError, match="work-item revision"):
        TacticianAssessmentInput.model_validate(
            _uncertain_input().model_dump() | {
                "evidence": (
                    TacticianEvidence(
                        evidence_id="adr-only",
                        source=AdmissionEvidenceSource.ADR,
                        source_revision="adr-1",
                        content="No work item.",
                    ).model_dump(),
                )
            }
        )
    unregistered = _uncertain_input().model_copy(
        update={
            "registered_recipe_summaries": (
                TacticianRecipeSummary(
                    recipe_id="invented",
                    recipe_version="1.0",
                    summary="Not registry policy.",
                ),
            )
        }
    )
    with pytest.raises(InvalidTacticianRecommendation, match="unregistered recipe"):
        run_tactician(
            unregistered,
            NodeLLMConfig(model="tactician-model"),
            registry=WorkflowRecipeRegistry((FULL_IMPLEMENTATION_RECIPE,)),
            call_llm_fn=lambda *_: _response(),
        )
