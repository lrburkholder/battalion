"""Error types shared across node implementations (Architect, Driver,
Reviewer) — kept in one place so a scope-misconfiguration check in one
node is the same exception type as in another, not three lookalikes."""


class WriteScopeMisconfigured(Exception):
    """Raised when a node's declared write_scope doesn't grant it the
    write-tool entry it needs to do its job — a config error, not a
    runtime scope violation (see battalion.scope.tool_binding for that)."""
