## Engineering Knowledge System

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

- metadata
- recommendation
- supporting evidence
- retrieval metadata
- feedback statistics

Accepted instincts are immutable.

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