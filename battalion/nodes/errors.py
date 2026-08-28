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


class WriteScopeMisconfigured(Exception):
    """Raised when a node's declared write_scope doesn't grant it the
    write-tool entry it needs to do its job — a config error, not a
    runtime scope violation (see battalion.scope.tool_binding for that)."""
