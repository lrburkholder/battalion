# Engineering Knowledge System Backlog

**Status:** Canonicalized in `backlog.json` on 2026-08-11

The previous ticket list in this document reused BTN-15 through BTN-22 after
those identifiers had already been assigned in the canonical backlog. It is
superseded by BTN-19 through BTN-24 below. `backlog.json` remains the source of
truth for scope, dependencies, priority, and status.

| Ticket | Capability | Depends on |
| --- | --- | --- |
| BTN-19 | Durable execution record and artifact provenance | BTN-1, BTN-7, BTN-10 |
| BTN-20 | Instinct data contract | BTN-1; ADR-0010 through ADR-0012 |
| BTN-21 | Immutable Intel repository | BTN-20 |
| BTN-22 | Recon candidate generation | BTN-19, BTN-20 |
| BTN-23 | Operator review and promotion workflow | BTN-21, BTN-22 |
| BTN-24 | Deterministic retrieval and node-specific injection | BTN-21, BTN-23, BTN-26 |

The first vertical slice is deliberately deterministic and human-controlled:

```text
Completed execution record
  -> Recon candidate
  -> operator accept / edit / reject
  -> immutable Intel repository
  -> bounded role-specific retrieval
```

Confidence does not represent a model's initial opinion. Per proposed ADR-0012,
it will represent observed operational usefulness once Battalion has enough
real retrieval and operator-feedback evidence. Feedback, confidence scoring,
semantic retrieval, and cross-project sharing therefore remain future tickets.
