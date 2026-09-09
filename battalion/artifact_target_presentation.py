"""Shared CLI/desktop projections for persisted artifact-target handoffs."""

from __future__ import annotations

from typing import Any

from battalion.application import ArtifactTargetHandoffInspection


def artifact_target_handoff_payload(inspection: ArtifactTargetHandoffInspection) -> dict[str, Any]:
    """Return bounded JSON-safe handoff evidence without provider reasoning."""
    return {
        "run_id": inspection.run_id,
        "availability": inspection.availability,
        "limitation": inspection.limitation,
        "active_contract_id": inspection.active_contract_id,
        "superseded_contract_ids": list(inspection.superseded_contract_ids),
        "contracts": [_contract_payload(contract) for contract in inspection.contracts],
        "reconciliations": [item.model_dump(mode="json") for item in inspection.reconciliations],
        "corrections": [item.model_dump(mode="json") for item in inspection.corrections],
    }


def render_artifact_target_handoff(inspection: ArtifactTargetHandoffInspection) -> str:
    """Render the same operator-facing facts used by the desktop inspector."""
    payload = artifact_target_handoff_payload(inspection)
    lines = ["ARTIFACT-TARGET HANDOFF", f"Availability: {payload['availability']}"]
    if payload["limitation"]:
        lines.append(f"Limitation: {payload['limitation']}")
    lines.extend((
        f"Active contract: {payload['active_contract_id'] or 'none'}",
        "Superseded contracts: " + (", ".join(payload["superseded_contract_ids"]) or "none"),
        "",
        "CONTRACTS",
    ))
    if not payload["contracts"]:
        lines.append("No artifact-target contract evidence recorded.")
    for contract in payload["contracts"]:
        state = "active" if contract["contract_id"] == payload["active_contract_id"] else "superseded"
        lines.extend((
            f"- {contract['contract_id']} ({state})",
            f"  Replaces: {contract['supersedes_contract_id'] or 'none'}",
            f"  Work item: {contract['work_item_revision']}",
            f"  Specification: {contract['specification_revision']}",
            f"  Project source: {contract['project_source_revision']}",
        ))
        for target in contract["targets"]:
            assignments = ", ".join(
                f"{item['owner_role']}:{item['workflow_phase']}:{item['intended_operation']}"
                for item in target["assignments"]
            )
            lines.append(f"  - {target['target_id']}: {target['project_relative_path']} [{assignments}]")
    lines.extend(("", "RECONCILIATION"))
    if not payload["reconciliations"]:
        lines.append("No reconciliation evidence recorded.")
    for item in payload["reconciliations"]:
        reasons = ", ".join(item["reason_codes"]) or "none"
        lines.append(f"- {item['outcome']} · {item['reconciliation_id']} · reasons: {reasons}")
    lines.extend(("", "CORRECTIONS"))
    if not payload["corrections"]:
        lines.append("None")
    for item in payload["corrections"]:
        lines.append(f"- {item['action']} · {item['action_id']} · actor {item['actor_id']} · {item['reason']}")
    return "\n".join(lines)


def _contract_payload(contract: Any) -> dict[str, Any]:
    payload = contract.model_dump(mode="json")
    payload["targets"] = [
        {**target, "assignments": list(target["assignments"]), "evidence_references": list(target["evidence_references"])}
        for target in payload["targets"]
    ]
    payload["evidence_references"] = list(payload["evidence_references"])
    return payload
