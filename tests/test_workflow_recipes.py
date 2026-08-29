"""Tests for finite, versioned WorkflowRecipe policy artifacts (BTN-138)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from battalion.application import (
    InspectWorkflowRecipe,
    ListWorkflowRecipes,
    inspect_workflow_recipe,
    list_workflow_recipes,
)
from battalion.workflow_recipes import (
    AmbiguousWorkflowRecipe,
    DEFAULT_WORKFLOW_RECIPE_REGISTRY,
    DuplicateWorkflowRecipe,
    FULL_IMPLEMENTATION_RECIPE,
    MalformedWorkflowRecipe,
    UnknownWorkflowRecipe,
    WorkflowRecipe,
    WorkflowRecipeRegistry,
)


def test_full_recipe_is_default_fallback_with_existing_assurance() -> None:
    recipe = FULL_IMPLEMENTATION_RECIPE

    assert recipe.recipe_id == "full-implementation-run"
    assert recipe.recipe_version == "1.0"
    assert recipe.independent_review_required is True
    assert [stage.value for stage in recipe.stages] == [
        "architecture",
        "driver-red",
        "review-red",
        "driver-green",
        "review-green",
        "refactor",
        "review-refactor",
    ]


def test_registry_resolves_exact_identity_and_preserves_historical_versions() -> None:
    previous = FULL_IMPLEMENTATION_RECIPE.model_copy(update={"recipe_version": "0.9"})
    registry = WorkflowRecipeRegistry((previous, FULL_IMPLEMENTATION_RECIPE))

    assert registry.resolve("full-implementation-run", "0.9") is previous
    assert registry.resolve("full-implementation-run", "1.0") is FULL_IMPLEMENTATION_RECIPE
    with pytest.raises(AmbiguousWorkflowRecipe):
        registry.resolve_unversioned("full-implementation-run")


def test_registry_rejects_unknown_duplicate_and_model_supplied_recipes() -> None:
    with pytest.raises(UnknownWorkflowRecipe):
        DEFAULT_WORKFLOW_RECIPE_REGISTRY.resolve("invented", "1.0")
    with pytest.raises(DuplicateWorkflowRecipe):
        WorkflowRecipeRegistry((FULL_IMPLEMENTATION_RECIPE, FULL_IMPLEMENTATION_RECIPE))
    with pytest.raises(MalformedWorkflowRecipe):
        WorkflowRecipeRegistry(({"recipe_id": "model-invented"},))  # type: ignore[arg-type]


def test_recipe_validation_cannot_omit_required_assurance() -> None:
    missing_review = FULL_IMPLEMENTATION_RECIPE.model_dump()
    missing_review["independent_review_required"] = False
    with pytest.raises(ValidationError, match="independent review"):
        WorkflowRecipe.model_validate(missing_review)

    missing_verification = FULL_IMPLEMENTATION_RECIPE.model_dump()
    missing_verification["mandatory_verification"] = []
    with pytest.raises(ValidationError, match="required verification"):
        WorkflowRecipe.model_validate(missing_verification)


def test_application_exposes_read_only_recipe_enumeration_and_inspection() -> None:
    recipes = list_workflow_recipes(ListWorkflowRecipes())

    assert recipes == (FULL_IMPLEMENTATION_RECIPE,)
    assert inspect_workflow_recipe(
        InspectWorkflowRecipe(recipe_id="full-implementation-run", recipe_version="1.0")
    ) is FULL_IMPLEMENTATION_RECIPE
