## Engineering Knowledge System

**Status:** Draft, except for the implemented BTN-20 through BTN-24 behavior
described below.

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

BTN-21 persists accepted instincts as one local JSON record per stable
identifier. Candidate instincts are rejected, and create-only writes prevent an
accepted identifier from being edited or overwritten in place.

New knowledge supersedes previous knowledge by creating a new instinct whose
`supersedes_id` references a record already in the repository. Superseded
records remain directly retrievable for provenance and are omitted from active
listing. Semantic indexes, remote stores, and cross-project sharing are not
part of this repository slice.

BTN-23 is the explicit candidate-to-repository authority boundary. An operator
accepts, edits then accepts, or rejects each candidate independently. Accepted
content is validated and written through BTN-21; rejected candidates are never
written. Every action has an immutable decision record identifying the
operator, timestamp, candidate, and accepted result when one exists.

---

## Intel

Intel supplies relevant engineering knowledge to execution nodes.

Intel SHALL:

- retrieve eligible instincts
- rank eligible instincts
- assemble node-specific context

Different nodes MAY receive different instincts.

Intel SHALL NOT modify instincts.

BTN-24 retrieves only accepted, active, non-superseded records. Audience must
match the execution role. Normalized literal applicability exclusions take
precedence over inclusions; a non-empty inclusion list requires a task match.
Eligible records are ordered by descending applicability matches, descending
tag matches, then ascending stable identifier. Each role queries independently,
and whole identified records are injected through the bounded BTN-26 context
path. Semantic retrieval, feedback, confidence, and cross-project sharing are
not part of this slice.

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

Retrieval integration and feedback remain future-facing. BTN-20 defines the
validated records, BTN-21 provides immutable local persistence, BTN-22
generates candidates, and BTN-23 supplies audited human promotion without
adding any of these features to the execution graph.
