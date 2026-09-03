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
| [ADR-0013](adr0013.md) | Bind write tools to project layout phases | Accepted | BTN-28 |
| [ADR-0014](adr0014.md) | Persist a bounded execution record in RunState | Accepted | BTN-19 |
| [ADR-0015](adr0015.md) | Keep Recon outside the completed execution graph | Accepted | BTN-22 |
| [ADR-0016](adr0016.md) | Make Instinct promotion an audited human boundary | Accepted | BTN-23 |
| [ADR-0017](adr0017.md) | Attribute LLM costs to durable node executions | Accepted | BTN-16 |
| [ADR-0018](adr0018.md) | Use literal, inspectable Instinct retrieval | Accepted | BTN-24 |
| [ADR-0019](adr0019.md) | Supervise active runs with detached per-run workers | Accepted | BTN-31 |
| [ADR-0020](adr0020.md) | Separate canonical run and project identity from display names | Accepted | BTN-32 |
| [ADR-0021](adr0021.md) | Recover live observation from durable state | Accepted | BTN-36 |
| [ADR-0022](adr0022.md) | Use PySide6 for desktop presentation | Accepted | BTN-41; production begins BTN-42 |
| [ADR-0023](adr0023.md) | Persist human actions with their existing authority | Accepted | BTN-43 |
| [ADR-0024](adr0024.md) | Keep inference identity and cost policy in Battalion | Accepted | BTN-51; implementation deferred to BTN-52–55 |
| [ADR-0025](adr0025.md) | Put provider adapters and transports beneath Battalion capabilities | Accepted | BTN-65; runtime deferred to BTN-66–80 |
| [ADR-0026](adr0026.md) | Separate Actor identity, authority, and responsibility | Accepted | BTN-58; implementation deferred to BTN-59–62 |
| [ADR-0027](adr0027.md) | Generate status documentation from canonical GitHub Issues and Milestones | Accepted | BTN-83; amended by BTN-102, BTN-104, and BTN-128 |
| [ADR-0028](adr0028.md) | Authorize Battalion operations, not identities or transports | Accepted | BTN-64 architecture; runtime consumed by BTN-60/61/68 |
| [ADR-0029](adr0029.md) | Persist side-effect evidence in RunState with replay-safe logical operation identity | Accepted | BTN-70 substrate; consumed by BTN-71–80 |
| [ADR-0030](adr0030.md) | Complete explicitly linked tickets after human PR merge | Accepted | BTN-126 |
| [ADR-0031](adr0031.md) | Separate canonical status validation from public status rendering | Accepted | BTN-128 |
| [ADR-0032](adr0032.md) | Register finite, versioned WorkflowRecipe policy artifacts | Accepted | BTN-138 |
| [ADR-0033](adr0033.md) | Classify workflow admission from bounded deterministic evidence | Accepted | BTN-139 |
| [ADR-0034](adr0034.md) | Keep Tactician advisory and outside Implementation Runs | Accepted | BTN-140 |
| [ADR-0035](adr0035.md) | Correct pre-write role-contract violations in-run | Accepted | BTN-154 |
| [ADR-0036](adr0036.md) | Keep human workflow admission separate from evidence and execution | Accepted | BTN-141 |
| [ADR-0037](adr0037.md) | Require semantic-review and human-acceptance evidence for compact completion | Accepted | BTN-142 |
| [ADR-0038](adr0038.md) | Gate Driver on a revision-pinned artifact-target contract | Accepted | BTN-193 architecture; runtime deferred |
| [ADR-0039](adr0039.md) | Persist exact workflow admission separately from execution history | Accepted | BTN-143 |

ADR-0010 through ADR-0012 were initially added with identifiers already used by
the v1 architecture plan. They were renumbered on BTN-18 to restore one unique
identifier per decision.
