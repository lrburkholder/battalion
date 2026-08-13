Recon Node
Purpose

The Recon node observes a completed execution and proposes reusable engineering knowledge for future executions.

Recon does not modify execution behavior for the current run.

Instead, it produces candidate Instincts that may be reviewed, accepted, edited, or discarded by the human operator.

Responsibilities

Recon SHALL:

analyze the completed execution graph
identify recurring or noteworthy engineering lessons
produce zero or more Instinct documents
attach evidence supporting each Instinct
identify the execution context in which the Instinct applies

Recon SHALL NOT assign confidence at creation. Operational confidence is
derived only from later retrieval and operator-feedback evidence, as proposed
by ADR-0012.

Recon SHALL NOT:

modify repository standards
modify architecture documents
rewrite specifications
automatically promote Instincts to standards
affect the current execution
Inputs
Completed Battalion state containing the durable execution record
Accepted Instincts explicitly supplied for duplicate comparison
Outputs

Zero or more CandidateInstinct records returned separately from RunState.

Each Instinct SHALL be independently reviewable.

Human Interaction

After generation, the operator SHALL choose:

Accept
Edit then accept
Reject

Rejected instincts SHALL NOT become part of Battalion knowledge.

Accepted instincts become available to future executions.

Success Criteria

A successful Recon execution:

captures useful engineering knowledge
avoids duplicating existing instincts
provides sufficient evidence
scopes the instinct narrowly
remains understandable without conversational context
