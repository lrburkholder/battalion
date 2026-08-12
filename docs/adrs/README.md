# Architecture Decision Records

This directory is the canonical record of Battalion architecture decisions and
proposals. An accepted decision may describe future work; its implementation
status states whether it is part of the shipped system.

| ADR | Decision | Status | Implementation |
| --- | --- | --- | --- |
| [ADR-0001](adr0001.md) | Use Pydantic for state validation | Accepted | v1 |
| [ADR-0002](adr0002.md) | Enforce write scope through tool binding | Accepted | v1 |
| [ADR-0003](adr0003.md) | Keep Typer as a thin CLI | Accepted | v1 |
| [ADR-0004](adr0004.md) | Implement native Battalion roles | Accepted | v1 |
| [ADR-0005](adr0005.md) | Externalize role prompts | Accepted | v1 |
| [ADR-0006](adr0006.md) | Split Driver into RED and GREEN modes | Accepted | v1 |
| [ADR-0007](adr0007.md) | Review against an expected outcome | Accepted | v1 |
| [ADR-0008](adr0008.md) | Give Refactorer Driver's implementation scope | Accepted | v1 |
| [ADR-0009](adr0009.md) | Count rejection causes per checkpoint type | Accepted | v1 |
| [ADR-0010](adr0010.md) | Accepted instincts are immutable | Accepted | Future |
| [ADR-0011](adr0011.md) | Establish an Engineering Knowledge System | Proposed | Future |
| [ADR-0012](adr0012.md) | Confidence represents operational usefulness | Proposed | Future |

ADR-0010 through ADR-0012 were initially added with identifiers already used by
the v1 architecture plan. They were renumbered on BTN-18 to restore one unique
identifier per decision.
