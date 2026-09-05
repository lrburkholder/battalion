"""Desktop admission uses the application-owned human decision contract."""


from __future__ import annotations


import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


from battalion.desktop.app import BattalionWindow
from battalion.desktop.controller import DesktopController
from battalion.workflow_admission import (
    AdmissionEvidenceCondition,
    AdmissionEvidenceReference,
    AdmissionEvidenceSource,
    CompactAdmissionEvidence,
    HardRiskFlag,
    WorkflowAdmissionEvidence,
)
from battalion.workflow_admission_decisions import WorkflowAdmissionDisposition
from support.desktop import (
    StubController,
    qt_app,
)


def _workflow_admission_evidence(
    *,
    compact: bool = False,
    hard_risk: str | None = None,
) -> WorkflowAdmissionEvidence:
    references = [AdmissionEvidenceReference(
        evidence_id="work-item:BTN-144",
        source=AdmissionEvidenceSource.WORK_ITEM,
        source_revision="BTN-144@1",
        condition=AdmissionEvidenceCondition.PRESENT,
        authoritative=True,
        hard_risk_flags=frozenset((hard_risk,)) if hard_risk else frozenset(),
    )]
    if compact:
        references.extend(
            AdmissionEvidenceReference(
                evidence_id=f"evidence:{fact.value}",
                source=AdmissionEvidenceSource.REPOSITORY,
                source_revision="repository@1",
                condition=AdmissionEvidenceCondition.PRESENT,
                authoritative=True,
                establishes=frozenset((fact,)),
            )
            for fact in CompactAdmissionEvidence
        )
    return WorkflowAdmissionEvidence(
        work_item_revision="BTN-144@1",
        evidence_references=tuple(references),
    )


def test_admission_surface_uses_shared_three_outcome_contract_and_human_override(
    qt_app, tmp_path
):
    controller = StubController(tmp_path)
    window = BattalionWindow(tmp_path, controller=controller, autoload=False)
    scenarios = (
        (_workflow_admission_evidence(compact=True), "compact-admissible", True),
        (
            _workflow_admission_evidence(
                compact=True,
                hard_risk=HardRiskFlag.AUTHORIZATION_SECRETS_PRIVACY_SECURITY.value,
            ),
            "full-required",
            False,
        ),
        (_workflow_admission_evidence(), "uncertain", False),
    )

    for evidence, outcome, compact_enabled in scenarios:
        session = controller.prepare_admission(
            "BTN-144", "Present workflow admission.", evidence
        )
        window.render_admission(session)
        assert f"Outcome: {outcome}" in window.admission_inspector.toPlainText()
        assert window.admission_buttons[
            WorkflowAdmissionDisposition.COMPACT
        ].isEnabled() is compact_enabled
        assert window.admission_buttons[WorkflowAdmissionDisposition.FULL].isEnabled()
        assert window.admission_buttons[
            WorkflowAdmissionDisposition.CLARIFICATION
        ].isEnabled()
        assert window.admission_buttons[
            WorkflowAdmissionDisposition.CANCELLED
        ].isEnabled()

    compact_session = controller.prepare_admission(
        "BTN-144", "Present workflow admission.", _workflow_admission_evidence(compact=True)
    )
    window.render_admission(compact_session)
    window.admission_annotation.setText(
        "Use full despite compact eligibility because the operator prefers more assurance."
    )
    window.admission_buttons[WorkflowAdmissionDisposition.FULL].click()

    assert controller.admissions == [(
        compact_session,
        WorkflowAdmissionDisposition.FULL,
        "Use full despite compact eligibility because the operator prefers more assurance.",
    )]
    window.close()


def test_desktop_admission_decision_uses_application_owned_persistence(tmp_path) -> None:
    controller = DesktopController(tmp_path)
    session = controller.prepare_admission(
        "BTN-144",
        "Present workflow admission.",
        _workflow_admission_evidence(compact=True),
    )

    result = controller.decide_admission(
        session,
        WorkflowAdmissionDisposition.FULL,
        "The human selected the stronger workflow.",
    )

    assert result.state.workflow_admission is not None
    assert result.state.workflow_admission.decision.disposition is (
        WorkflowAdmissionDisposition.FULL
    )
    assert result.state.workflow_admission.decision.annotation == (
        "The human selected the stronger workflow."
    )
    assert result.state_path.exists()
