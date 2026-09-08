"""Shared, transport-neutral workflow-admission presentation projections.

CLI and desktop render the same application read models through this module so
neither client reinterprets deterministic policy, Tactician advice, recipe
semantics, or durable upgrade history.
"""

from __future__ import annotations

from typing import Any

from battalion.application import (
    WorkflowAdmissionInspection,
    WorkflowAdmissionRunInspection,
)
from battalion.tactician import TacticianAssessment
from battalion.workflow_admission import (
    AdmissionEvidenceReference,
    WorkflowAdmissionAssessment,
)
from battalion.workflow_recipes import WorkflowRecipe


def workflow_admission_payload(
    inspection: WorkflowAdmissionInspection,
) -> dict[str, Any]:
    """Return a JSON-safe decision projection without provider chain-of-thought."""

    assessment = inspection.assessment
    assessment_payload = _assessment_payload(assessment)
    full_stages = _full_recipe_stages(inspection.available_recipes)
    tactician = inspection.tactician_assessment
    return {
        "assessment": assessment_payload,
        "governing_evidence": [
            _reference_payload(reference)
            for reference in assessment.evidence_references
        ],
        "tactician_assessment": (
            _tactician_payload(tactician) if tactician is not None else None
        ),
        "available_actions": [
            disposition.value for disposition in inspection.available_dispositions
        ],
        "compact_unavailable_reason": inspection.compact_unavailable_reason,
        "recipes": [
            _recipe_payload(recipe, full_stages=full_stages)
            for recipe in inspection.available_recipes
        ],
    }


def workflow_admission_history_payload(
    inspection: WorkflowAdmissionRunInspection,
) -> dict[str, Any]:
    """Return JSON-safe original-admission and later-upgrade history."""

    payload: dict[str, Any] = {
        "run_id": inspection.run_id,
        "availability": inspection.availability,
        "limitation": inspection.limitation,
        "admission": None,
    }
    record = inspection.record
    if record is None:
        return payload
    payload["admission"] = {
        "assessment": _assessment_payload(record.assessment),
        "tactician_assessment": (
            _tactician_payload(record.tactician_assessment)
            if record.tactician_assessment is not None
            else None
        ),
        "human_decision": record.decision.model_dump(mode="json"),
        "execution": record.execution.model_dump(mode="json"),
    }
    return payload


def render_workflow_admission(inspection: WorkflowAdmissionInspection) -> str:
    """Render a keyboard/terminal-friendly admission decision surface."""

    payload = workflow_admission_payload(inspection)
    assessment = payload["assessment"]
    lines = [
        "WORKFLOW ADMISSION",
        f"Outcome: {assessment['outcome']}",
        f"Assessment: {assessment['assessment_id']}",
        f"Policy: {assessment['policy_id']} {assessment['policy_version']}",
        "",
        "GOVERNING EVIDENCE",
    ]
    for reference in payload["governing_evidence"]:
        established = ", ".join(reference["establishes"]) or "no compact fact"
        risks = ", ".join(reference["hard_risk_flags"]) or "none"
        lines.append(
            f"- {reference['evidence_id']} · {reference['source']} "
            f"@ {reference['source_revision']} · {reference['condition']} · "
            f"establishes: {established} · hard risks: {risks}"
        )
    lines.extend(("", "DETERMINISTIC REASONS"))
    lines.extend(f"- {reason}" for reason in assessment["reasons"])
    lines.append(
        "Missing compact evidence: "
        + (", ".join(assessment["missing_evidence"]) or "none")
    )
    lines.append(
        "Observed hard risks: "
        + (", ".join(assessment["hard_risk_flags"]) or "none")
    )

    tactician = payload["tactician_assessment"]
    lines.extend(("", "TACTICIAN ASSESSMENT (ADVISORY)"))
    if tactician is None:
        lines.append("Not available; deterministic evidence remains governing.")
    else:
        recommendation = tactician["recommendation_kind"]
        if tactician["recommended_recipe_id"] is not None:
            recommendation += (
                f" · {tactician['recommended_recipe_id']} "
                f"{tactician['recommended_recipe_version']}"
            )
        lines.append(f"Recommendation: {recommendation}")
        lines.extend(f"- {reason}" for reason in tactician["rationale"])
        lines.append(
            "Risk flags: " + (", ".join(tactician["risk_flags"]) or "none")
        )
        lines.append(
            "Missing evidence: "
            + (", ".join(tactician["missing_evidence"]) or "none")
        )

    if payload["compact_unavailable_reason"]:
        lines.extend(("", f"Compact unavailable: {payload['compact_unavailable_reason']}"))
    lines.extend(("", "AVAILABLE HUMAN ACTIONS"))
    lines.append("- " + ", ".join(payload["available_actions"]))
    lines.extend(("", "REGISTERED RECIPE OPTIONS"))
    for recipe in payload["recipes"]:
        lines.extend(
            (
                f"- {recipe['recipe_id']} {recipe['recipe_version']}",
                "  Runs: " + ", ".join(recipe["stages"]),
                "  Omits from full: "
                + (", ".join(recipe["omitted_full_stages"]) or "none"),
                "  Assurance gates: " + ", ".join(recipe["capabilities"]),
                "  Verification: " + ", ".join(recipe["mandatory_verification"]),
                "  Completion: "
                + (", ".join(recipe["completion_requirements"]) or "none"),
                "  Independent review: "
                + ("required" if recipe["independent_review_required"] else "not required"),
            )
        )
    return "\n".join(lines)


def render_workflow_admission_history(
    inspection: WorkflowAdmissionRunInspection,
) -> str:
    """Render original admission distinctly from later upgrade evidence."""

    payload = workflow_admission_history_payload(inspection)
    lines = ["WORKFLOW ADMISSION HISTORY", f"Availability: {payload['availability']}"]
    if payload["limitation"]:
        lines.append(f"Limitation: {payload['limitation']}")
    admission = payload["admission"]
    if admission is None:
        return "\n".join(lines)

    assessment = admission["assessment"]
    decision = admission["human_decision"]
    execution = admission["execution"]
    lines.extend(
        (
            "",
            "ORIGINAL ADMISSION",
            f"Deterministic outcome: {assessment['outcome']}",
            "Deterministic reasons: " + "; ".join(assessment["reasons"]),
            f"Assessment: {assessment['assessment_id']}",
            f"Policy: {assessment['policy_id']} {assessment['policy_version']}",
        )
    )
    tactician = admission["tactician_assessment"]
    if tactician is None:
        lines.append("Tactician assessment: not used")
    else:
        lines.append(
            "Tactician assessment: advisory · "
            f"{tactician['recommendation_kind']} · {tactician['assessment_id']}"
        )
        lines.append("Tactician rationale: " + "; ".join(tactician["rationale"]))
    lines.extend(
        (
            f"Human decision: {decision['disposition']} by "
            f"{decision['approving_actor_display_name']} at {decision['occurred_at']}",
            f"Selected recipe: {decision['selected_recipe_id']} "
            f"{decision['selected_recipe_version']}",
            "Admitted risks: "
            + (", ".join(decision["admitted_risk_flags"]) or "none"),
            "Human annotation: " + (decision["annotation"] or "none"),
            "",
            "LATER UPGRADES",
        )
    )
    upgrades = execution["upgrade_history"]
    if not upgrades:
        lines.append("None")
    for upgrade in upgrades:
        lines.append(
            f"- {upgrade['trigger']} -> {upgrade['target']}: {upgrade['reason']} "
            f"(evidence: {', '.join(upgrade['evidence_ids'])})"
        )
    if execution["continuation_recipe_id"] is not None:
        lines.append(
            "Continuation recipe: "
            f"{execution['continuation_recipe_id']} "
            f"{execution['continuation_recipe_version']}"
        )
    return "\n".join(lines)


def _full_recipe_stages(recipes: tuple[WorkflowRecipe, ...]) -> tuple[str, ...]:
    full = tuple(
        recipe
        for recipe in recipes
        if recipe.eligibility_policy.policy_id == "full-workflow-default"
    )
    if len(full) != 1:
        return ()
    return tuple(stage.value for stage in full[0].stages)


def _recipe_payload(
    recipe: WorkflowRecipe,
    *,
    full_stages: tuple[str, ...],
) -> dict[str, Any]:
    stages = tuple(stage.value for stage in recipe.stages)
    return {
        "recipe_id": recipe.recipe_id,
        "recipe_version": recipe.recipe_version,
        "stages": list(stages),
        "omitted_full_stages": [stage for stage in full_stages if stage not in stages],
        "capabilities": sorted(item.value for item in recipe.capabilities),
        "mandatory_verification": sorted(
            item.value for item in recipe.mandatory_verification
        ),
        "independent_review_required": recipe.independent_review_required,
        "completion_requirements": [
            requirement.kind.value for requirement in recipe.completion_requirements
        ],
    }


def _assessment_payload(
    assessment: WorkflowAdmissionAssessment,
) -> dict[str, Any]:
    payload = assessment.model_dump(mode="json")
    payload["evidence_references"] = [
        _reference_payload(reference) for reference in assessment.evidence_references
    ]
    return payload


def _tactician_payload(assessment: TacticianAssessment) -> dict[str, Any]:
    payload = assessment.model_dump(mode="json")
    payload["input_evidence_references"] = [
        _reference_payload(reference)
        for reference in assessment.input_evidence_references
    ]
    return payload


def _reference_payload(reference: AdmissionEvidenceReference) -> dict[str, Any]:
    payload = reference.model_dump(mode="json")
    payload["establishes"] = sorted(payload["establishes"])
    payload["hard_risk_flags"] = sorted(payload["hard_risk_flags"])
    return payload
