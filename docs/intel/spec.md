## Engineering Knowledge System

**Status:** Draft, except for the BTN-20 Instinct data contract described below.

### Purpose

Battalion SHALL accumulate engineering knowledge across executions while preserving
human oversight.

The Engineering Knowledge System (EKS) consists of four components:

- Recon
- Intel Repository
- Intel
- Operator Feedback

The EKS exists to improve future executions without modifying the current one.

---

## Recon

Recon executes after a completed Battalion mission.

Recon SHALL:

- inspect the completed execution
- identify reusable engineering observations
- generate zero or more candidate Instincts

Recon SHALL NOT:

- modify repository documentation
- modify standards
- modify architecture
- affect the completed execution

Candidate instincts require human review before acceptance.

---

## Intel Repository

The Intel Repository stores accepted instincts.

Each instinct SHALL contain:

- a stable identifier and schema version
- an explicit candidate or accepted lifecycle
- recommendation
- bounded supporting evidence references and descriptions
- role audience, applicability, and tags
- creation provenance
- optional supersession metadata

Accepted instincts additionally require human-acceptance provenance. A
candidate cannot be represented as accepted without that provenance.

Confidence is not part of the creation contract. Feedback statistics and
operational confidence remain deferred until retrieval usage exists.

Accepted instincts are immutable once repository persistence is implemented.

New knowledge supersedes previous knowledge by creating a new instinct.

---

## Intel

Intel supplies relevant engineering knowledge to execution nodes.

Intel SHALL:

- retrieve eligible instincts
- rank eligible instincts
- assemble node-specific context

Different nodes MAY receive different instincts.

Intel SHALL NOT modify instincts.

---

## Operator Feedback

When a node completes, Battalion SHALL record which instincts were supplied.

The operator MAY indicate whether each instinct was useful.

Feedback SHALL influence future retrieval ranking.

Feedback SHALL NOT automatically:

- rewrite instincts
- delete instincts
- promote instincts
- modify standards

---

## Knowledge Lifecycle

Observation

↓

Candidate Instinct

↓

Human Review

↓

Accepted Instinct

↓

Intel Retrieval

↓

Node Execution

↓

Operator Feedback

↓

Updated Retrieval Confidence

The lifecycle after Instinct creation remains future-facing; BTN-20 defines the
validated records but does not ship Recon, promotion, repository persistence,
retrieval, or feedback behavior.
