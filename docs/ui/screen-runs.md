# Runs Hub Screen
Version: 0.1

---

# Purpose

The Runs Hub is Battalion's primary interface.

It replaces the traditional "New Chat" landing page found in AI assistants.

Rather than centering the conversation, the Runs Hub centers engineering
execution.

The screen answers four questions immediately:

1. What is Battalion doing?
2. What has Battalion done recently?
3. How much did it cost?
4. Why did it make those decisions?

Every other detail is progressively discoverable.

---

# Layout

The screen is composed of four persistent regions.

+---------------------------------------------------------------+
| Top Navigation                                                 |
+-------------+----------------------------+--------------------+
|             |                            |                    |
|             |                            |                    |
| Run List    |      Execution Graph       |     Inspector      |
|             |                            |                    |
|             |                            |                    |
+-------------+----------------------------+--------------------+
| Bottom Status Bar                                             |
+---------------------------------------------------------------+

The layout remains stable.

Changing the selected run updates content inside the regions rather than
changing pages whenever possible.

---

# Region Responsibilities

## Top Navigation

Purpose

Global navigation and high-level project status.

Contains

Battalion logo

Current project

(Currently always Battalion)

Current branch

Current session

Search button (disabled until implemented)

Settings

Future

Project switcher

Notifications

Multi-project support

---

## Run List

Purpose

Display every execution in chronological order.

Default Sort

Newest first.

Each Run Card displays

Role

Status

Model

Start time

Duration

Token count

Estimated cost

Associated ticket (if any)

Branch

Hover State

Highlights row

Shows quick actions

Right Click

Future context menu

Selection

Selecting a run updates

Execution Graph

Inspector

Status Bar

Without changing screens.

---

## Run Card

Example

----------------------------------------------------

Driver

Running

Claude Sonnet 5

2m 14s

18,441 tokens

$0.21

REG-184

feature/reg-184

----------------------------------------------------

Status Color

Queued

Gray

Running

Blue

Waiting

Amber

Succeeded

Green

Failed

Red

Cancelled

Neutral

---

## Execution Graph

Purpose

Visualize relationships between runs.

Nodes

Agent executions

Edges

Manual launch

Automatic handoff

Dependency

Future orchestration

Default Layout

Top-to-bottom

Example

Researcher

↓

Specifier

↓

Architect

↓

Driver

↓

Reviewer

↓

Deployer

Current Run

Animated border

Completed Runs

Solid

Failed Runs

Red outline

Hover

Highlights incoming and outgoing edges.

Click

Updates Inspector.

Future

Zoom

Pan

Filtering

Replay animation

---

## Inspector

Purpose

Display detailed information for the selected run.

The Inspector uses tabs.

Overview

Timeline

Messages

Tool Calls

Artifacts

Observability

Provenance

Raw Trace

Default Tab

Overview

---

### Overview

Shows

Role

Status

Objective

Summary

Duration

Provider

Model

Token totals

Estimated cost

Inputs

Outputs

---

### Timeline

Chronological execution.

Example

14:32:18

Started

14:32:22

Loaded spec.md

14:32:26

Read ADR-12

14:32:41

Generated plan.md

14:33:03

Completed

---

### Messages

Conversation exchanged during execution.

Initially collapsed.

Supports

Markdown

Syntax highlighting

Future

Semantic search

---

### Tool Calls

Each invocation shows

Tool

Arguments

Duration

Result

Expandable

---

### Artifacts

Lists generated outputs.

Examples

plan.md

spec.md

ADR-18

commit

review.md

Clicking an artifact opens it.

---

### Observability

Displays

Provider

Model

Latency

Input Tokens

Output Tokens

Cached Tokens

Cost

Retries

Tool Count

LangFuse Trace ID

Future

Historical comparison

Model recommendations

---

### Provenance

Shows

Inputs consumed

Referenced artifacts

Referenced runs

Referenced specifications

Referenced ADRs

Every item is clickable.

---

### Raw Trace

Developer mode.

Contains

Prompt

Context

Streaming events

Tool payloads

LLM responses

Only loaded when requested.

---

# Component Boundaries

RunsHub
├── TopNavigation
├── RunList
│   ├── RunCard
│   └── RunFilters (future)
├── ExecutionGraph
│   ├── GraphNode
│   ├── GraphEdge
│   └── GraphLegend
├── Inspector
│   ├── OverviewTab
│   ├── TimelineTab
│   ├── MessagesTab
│   ├── ToolCallsTab
│   ├── ArtifactsTab
│   ├── ObservabilityTab
│   ├── ProvenanceTab
│   └── RawTraceTab
└── StatusBar

---

# Bottom Status Bar

Always visible.

Displays

Current session duration

Total session tokens

Total session cost

Current model

Connection status

Background activity

Future

Rate limits

Queued work

Memory usage

---

# Empty State

When no runs exist.

Display

Welcome to Battalion

Create your first run

Explain what a run is

Provide one primary action

Start Session

Avoid presenting an empty chat window.

---

# Loading State

Execution Graph

Skeleton nodes

Inspector

Skeleton content

Run List

Placeholder cards

Never leave regions blank.

---

# Error State

Errors remain localized.

Example

Execution Graph unavailable.

Retry

rather than

Entire screen unavailable.

---

# Keyboard Navigation

Up / Down

Select previous or next run

Enter

Open selected run

Tab

Move between regions

1-7

Switch Inspector tabs

Ctrl+F

Future global search

---

# Design Rules

The Runs Hub must remain calm.

Avoid dashboards filled with numbers.

Surface detail only when requested.

Every number shown should answer an engineering question.

Every artifact should expose provenance.

Every interaction should preserve user context.

No navigation should unexpectedly destroy the engineer's place in the workflow.

---

# Success Criteria

A first-time user can determine within ten seconds:

- What Battalion is currently doing.
- Which agent is active.
- What has happened recently.
- Where generated artifacts originated.
- Approximately how much the current work has cost.

An experienced user can inspect any execution without consulting log files or terminal output.