"""Inference-cost policy admission and runtime evidence helpers (BTN-55)."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from battalion.llm.configuration import NodeLLMConfig


class CostPolicy(str, Enum):
    """The operator-selected monetary boundary for one Battalion run."""

    LOCAL_ONLY = "local-only"
    FREE_ONLY = "free-only"
    PAID_CAPABLE = "paid-capable"


class InferencePolicyError(ValueError):
    """Configured targets cannot satisfy the selected cost policy."""


_active_policy: ContextVar[CostPolicy] = ContextVar(
    "battalion_inference_cost_policy", default=CostPolicy.PAID_CAPABLE
)


@contextmanager
def cost_policy_context(policy: CostPolicy) -> Iterator[None]:
    """Bind the durable run policy to calls made during one graph execution."""
    token = _active_policy.set(policy)
    try:
        yield
    finally:
        _active_policy.reset(token)


def active_cost_policy() -> CostPolicy:
    return _active_policy.get()


def _effective_targets(models: dict[str, "NodeLLMConfig"]) -> dict[str, "NodeLLMConfig"]:
    default = models.get("default")
    targets: dict[str, NodeLLMConfig] = {}
    for role in ("architect", "driver", "reviewer", "refactorer"):
        target = models.get(role, default)
        if target is None:
            raise InferencePolicyError(
                f"{role} has no configured inference target; zero-cost policies require every role target."
            )
        targets[role] = target
    # Optional roles (for example Tactician) can perform inference outside the
    # graph. A zero-cost policy cannot leave a configured target unexamined.
    for role, target in models.items():
        if role != "default":
            targets.setdefault(role, target)
    return targets


def _has_current_evidence(target: "NodeLLMConfig", now: datetime) -> bool:
    if not target.classification_source or target.classification_observed_at is None:
        return False
    expiry = target.classification_expires_at
    return expiry is None or expiry > now


def validate_cost_policy(
    models: dict[str, "NodeLLMConfig"], policy: CostPolicy, *, now: datetime | None = None
) -> None:
    """Fail closed before graph execution for local-only and free-only runs."""
    if policy is CostPolicy.PAID_CAPABLE:
        return
    observed_at = now or datetime.now(timezone.utc)
    for role, target in _effective_targets(models).items():
        verified_local = (
            target.inference_location == "local"
            and target.cost_classification == "local"
            and _has_current_evidence(target, observed_at)
        )
        verified_free = (
            target.cost_classification == "verified-free"
            and _has_current_evidence(target, observed_at)
        )
        allowed = verified_local if policy is CostPolicy.LOCAL_ONLY else (verified_local or verified_free)
        if not allowed:
            requirement = "verified same-host local" if policy is CostPolicy.LOCAL_ONLY else "verified local or current verified-free"
            raise InferencePolicyError(
                f"{role} target {target.model!r} is {target.cost_classification!r} "
                f"with {target.inference_location!r} inference; {policy.value} requires {requirement} evidence."
            )


def nonzero_cost_violation(policy: CostPolicy, *, provider_reported: bool, cost_is_nonzero: bool) -> str | None:
    """Describe a reportable post-call violation without inventing missing cost."""
    if policy is not CostPolicy.PAID_CAPABLE and provider_reported and cost_is_nonzero:
        return f"Provider reported non-zero cost under {policy.value} policy"
    return None
