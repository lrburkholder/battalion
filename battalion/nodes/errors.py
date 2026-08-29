"""Error types shared across node implementations.

Keeping graph-routable role-output contract errors here means malformed
provider responses pause through the documented failure interrupt rather than
being indistinguishable from an application defect.
"""


class RoleOutputError(Exception):
    """A role returned unusable content for its declared response contract.

    This is a recoverable provider/protocol failure: the operator can inspect
    the retained context, adjust the model or configuration, and resume. It
    remains distinct from an unexpected programmer error, which propagates.
    """


class RoleContractViolation(RoleOutputError):
    """A mechanically detected, pre-write violation of a role's contract.

    The candidate has not crossed a write boundary, so the execution scaffold
    may supply deterministic correction context and retry the same role within
    its bounded automatic-correction policy.  This is deliberately narrower
    than a :class:`ScopeViolationError`, which remains an authority failure.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        offending_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.offending_paths = offending_paths

    def correction_context(self) -> str:
        """Return bounded, deterministic context for the next same-role call."""
        paths = "\n".join(f"- {path}" for path in self.offending_paths)
        path_detail = f"\nOffending artifact paths:\n{paths}" if paths else ""
        return (
            "Battalion caught your previous candidate; prohibited output was not written.\n"
            f"Rule violated ({self.reason_code}): {self}\n"
            f"Correct the same role output and return only output that satisfies its role contract."
            f"{path_detail}"
        )


class WriteScopeMisconfigured(Exception):
    """Raised when a node's declared write_scope doesn't grant it the
    write-tool entry it needs to do its job — a config error, not a
    runtime scope violation (see battalion.scope.tool_binding for that)."""
