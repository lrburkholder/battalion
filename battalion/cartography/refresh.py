"""Authority-safe assembly of a generated Cartography refresh candidate."""

from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel, ConfigDict

from battalion.cartography.models import (
    Annotation,
    AuthorityClass,
    InstitutionalConstraint,
    KnowledgeClaim,
    MapEntityId,
    MapRevision,
)


class GeneratedAuthorityViolation(ValueError):
    """A Cartographer refresh attempted to manufacture human/governing authority."""


class RefreshProjectMismatch(ValueError):
    """Generated structure and prior institutional context are from different projects."""


class RefreshAssembly(BaseModel):
    """Validated publication candidate plus explicit protected-record evidence."""

    model_config = ConfigDict(frozen=True)

    revision: MapRevision
    preserved_record_ids: tuple[MapEntityId, ...]


_InstitutionalRecord = TypeVar(
    "_InstitutionalRecord", Annotation, KnowledgeClaim, InstitutionalConstraint
)


def assemble_generated_refresh(
    prior: MapRevision, generated: MapRevision
) -> RefreshAssembly:
    """Carry protected institutional context into a newly generated revision.

    The Cartographer can contribute only derived institutional records.  Existing
    attributed and governing records are carried byte-for-byte from the previous
    completed revision, including their Actor/governing provenance, even when a
    generated record repeats their ID or structural evidence has become stale.
    """

    if prior.project_id != generated.project_id:
        raise RefreshProjectMismatch("Cartography refresh revisions must belong to one project")
    for record in (
        *generated.annotations,
        *generated.knowledge_claims,
        *generated.constraints,
    ):
        if record.authority_class is not AuthorityClass.DERIVED:
            raise GeneratedAuthorityViolation(
                "Generated Cartography records must remain derived; human and governing "
                "authority is carried only from a prior completed revision."
            )

    annotations, annotation_ids = _preserve_records(
        prior.annotations, generated.annotations, lambda item: item.annotation_id
    )
    claims, claim_ids = _preserve_records(
        prior.knowledge_claims, generated.knowledge_claims, lambda item: item.claim_id
    )
    constraints, constraint_ids = _preserve_records(
        prior.constraints, generated.constraints, lambda item: item.constraint_id
    )
    revision = generated.model_copy(
        update={
            "annotations": annotations,
            "knowledge_claims": claims,
            "constraints": constraints,
        }
    )
    return RefreshAssembly(
        revision=revision,
        preserved_record_ids=tuple(sorted((*annotation_ids, *claim_ids, *constraint_ids))),
    )


def _preserve_records(
    prior: tuple[_InstitutionalRecord, ...],
    generated: tuple[_InstitutionalRecord, ...],
    identifier: Callable[[_InstitutionalRecord], MapEntityId],
) -> tuple[tuple[_InstitutionalRecord, ...], tuple[MapEntityId, ...]]:
    """Preserve prior non-derived records and accept only fresh derived records."""

    protected = {
        identifier(record): record
        for record in prior
        if record.authority_class is not AuthorityClass.DERIVED
    }
    assembled = [record for record in generated if identifier(record) not in protected]
    assembled.extend(protected.values())
    return tuple(sorted(assembled, key=identifier)), tuple(sorted(protected))
