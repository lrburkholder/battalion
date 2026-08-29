"""Finite, versioned workflow policy artifacts.

Recipes describe Battalion-owned workflow semantics. They deliberately use a
closed vocabulary rather than accepting prompt-produced graph/node definitions:
admission selects one exact registered recipe and never constructs a graph.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class WorkflowRecipeError(ValueError):
    """Base class for recipe definition and resolution failures."""


class MalformedWorkflowRecipe(WorkflowRecipeError):
    """A registry received something other than a validated policy artifact."""


class DuplicateWorkflowRecipe(WorkflowRecipeError):
    """Two policy artifacts claim the same stable identity and version."""


class IncompatibleWorkflowRecipe(WorkflowRecipeError):
    """A policy artifact violates the assurance contract for its workflow kind."""


class UnknownWorkflowRecipe(WorkflowRecipeError):
    """No registered policy artifact has the requested identity/version."""


class AmbiguousWorkflowRecipe(WorkflowRecipeError):
    """A versionless lookup would silently choose between historical semantics."""


class WorkflowKind(str, Enum):
    """The finite kinds of workflow that may be admitted."""

    IMPLEMENTATION_RUN = "implementation-run"


class WorkflowStage(str, Enum):
    """Semantic execution stages, not public LangGraph node names."""

    ARCHITECTURE = "architecture"
    DRIVER_RED = "driver-red"
    REVIEW_RED = "review-red"
    DRIVER_GREEN = "driver-green"
    REVIEW_GREEN = "review-green"
    REFACTOR = "refactor"
    REVIEW_REFACTOR = "review-refactor"


class WorkflowCapability(str, Enum):
    """Execution assurances which remain under Battalion policy."""

    WRITE_SCOPE_ENFORCEMENT = "write-scope-enforcement"
    AUTHORIZATION = "authorization"
    SIDE_EFFECT_POLICY = "side-effect-policy"
    PROVENANCE = "provenance"
    COST_POLICY = "cost-policy"


class VerificationRequirement(str, Enum):
    """Verification evidence that every implementation recipe must retain."""

    BEHAVIORAL_EVIDENCE = "behavioral-evidence"
    DETERMINISTIC_GATES = "deterministic-gates"


class PolicyReference(BaseModel):
    """A stable reference to governing policy without embedding that policy."""

    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=100)


_REQUIRED_IMPLEMENTATION_CAPABILITIES = frozenset(WorkflowCapability)
_REQUIRED_IMPLEMENTATION_VERIFICATION = frozenset(VerificationRequirement)


class WorkflowRecipe(BaseModel):
    """A closed, versioned policy description of one admitted workflow.

    ``recipe_id`` and ``recipe_version`` are an immutable historical key. A
    changed definition must receive a new version; the registry will never
    substitute a later version during exact resolution.
    """

    model_config = ConfigDict(frozen=True)

    recipe_id: str = Field(min_length=1, max_length=200)
    recipe_version: str = Field(min_length=1, max_length=100)
    workflow_kind: WorkflowKind
    stages: tuple[WorkflowStage, ...] = Field(min_length=1)
    capabilities: frozenset[WorkflowCapability]
    mandatory_verification: frozenset[VerificationRequirement]
    independent_review_required: bool
    interrupt_policy: PolicyReference
    eligibility_policy: PolicyReference
    upgrade_triggers: tuple[PolicyReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_implementation_assurance(self) -> "WorkflowRecipe":
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("workflow recipe stages must be ordered without duplicates")
        if self.workflow_kind is WorkflowKind.IMPLEMENTATION_RUN:
            missing_capabilities = _REQUIRED_IMPLEMENTATION_CAPABILITIES - self.capabilities
            if missing_capabilities:
                raise ValueError(
                    "implementation recipes omit required capabilities: "
                    + ", ".join(sorted(item.value for item in missing_capabilities))
                )
            missing_verification = (
                _REQUIRED_IMPLEMENTATION_VERIFICATION - self.mandatory_verification
            )
            if missing_verification:
                raise ValueError(
                    "implementation recipes omit required verification: "
                    + ", ".join(sorted(item.value for item in missing_verification))
                )
            if not self.independent_review_required:
                raise ValueError("implementation recipes require independent review")
        return self


class WorkflowRecipeRegistry:
    """Read-only Battalion-owned lookup for validated recipe policy artifacts."""

    def __init__(self, recipes: Iterable[WorkflowRecipe]) -> None:
        indexed: dict[tuple[str, str], WorkflowRecipe] = {}
        by_id: dict[str, list[WorkflowRecipe]] = {}
        for recipe in recipes:
            if not isinstance(recipe, WorkflowRecipe):
                raise MalformedWorkflowRecipe(
                    "workflow registries accept validated WorkflowRecipe policy artifacts only"
                )
            try:
                WorkflowRecipe.model_validate(recipe.model_dump())
            except ValidationError as exc:
                raise IncompatibleWorkflowRecipe(
                    f"incompatible workflow recipe {recipe.recipe_id!r}: {exc}"
                ) from exc
            identity = (recipe.recipe_id, recipe.recipe_version)
            if identity in indexed:
                raise DuplicateWorkflowRecipe(
                    f"duplicate workflow recipe {recipe.recipe_id!r} version "
                    f"{recipe.recipe_version!r}"
                )
            indexed[identity] = recipe
            by_id.setdefault(recipe.recipe_id, []).append(recipe)
        self._by_identity = MappingProxyType(indexed)
        self._by_id = MappingProxyType(
            {recipe_id: tuple(versions) for recipe_id, versions in by_id.items()}
        )

    def list(self) -> tuple[WorkflowRecipe, ...]:
        """Return all registered artifacts in deterministic identity/version order."""
        return tuple(
            recipe
            for _, recipe in sorted(
                self._by_identity.items(), key=lambda item: item[0]
            )
        )

    def resolve(self, recipe_id: str, recipe_version: str) -> WorkflowRecipe:
        """Resolve an exact historical semantic key; never infer a version."""
        try:
            return self._by_identity[(recipe_id, recipe_version)]
        except KeyError as exc:
            raise UnknownWorkflowRecipe(
                f"unknown workflow recipe {recipe_id!r} version {recipe_version!r}"
            ) from exc

    def versions(self, recipe_id: str) -> tuple[WorkflowRecipe, ...]:
        """Inspect known versions without selecting one for execution."""
        try:
            return self._by_id[recipe_id]
        except KeyError as exc:
            raise UnknownWorkflowRecipe(f"unknown workflow recipe {recipe_id!r}") from exc

    def resolve_unversioned(self, recipe_id: str) -> WorkflowRecipe:
        """Reject ambiguity instead of silently changing historical semantics."""
        recipes = self.versions(recipe_id)
        if len(recipes) != 1:
            raise AmbiguousWorkflowRecipe(
                f"workflow recipe {recipe_id!r} has {len(recipes)} versions; "
                "an exact version is required"
            )
        return recipes[0]


FULL_IMPLEMENTATION_RECIPE = WorkflowRecipe(
    recipe_id="full-implementation-run",
    recipe_version="1.0",
    workflow_kind=WorkflowKind.IMPLEMENTATION_RUN,
    stages=(
        WorkflowStage.ARCHITECTURE,
        WorkflowStage.DRIVER_RED,
        WorkflowStage.REVIEW_RED,
        WorkflowStage.DRIVER_GREEN,
        WorkflowStage.REVIEW_GREEN,
        WorkflowStage.REFACTOR,
        WorkflowStage.REVIEW_REFACTOR,
    ),
    capabilities=frozenset(WorkflowCapability),
    mandatory_verification=frozenset(VerificationRequirement),
    independent_review_required=True,
    interrupt_policy=PolicyReference(policy_id="v1-interrupts", policy_version="1.0"),
    eligibility_policy=PolicyReference(
        policy_id="full-workflow-default", policy_version="1.0"
    ),
    upgrade_triggers=(
        PolicyReference(policy_id="upgrade-only-ratchet", policy_version="1.0"),
    ),
)


DEFAULT_WORKFLOW_RECIPE_REGISTRY = WorkflowRecipeRegistry((FULL_IMPLEMENTATION_RECIPE,))
