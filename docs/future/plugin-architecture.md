# Plugin Architecture Planning Brief

**Status:** Draft
**Ticket:** BTN-46
**Target:** Post-v2 architecture
**Related:** `philosophy.md`, `spec.md`, ADR-0002, ADR-0003, RFC-0004,
RFC-0006, ADR-0025

## Question

What plugin model can extend Battalion with repositories, ticket systems,
model providers, tools, and presentation integrations without creating a
second policy authority or weakening scoped execution?

Plugins are not currently a shipped Battalion concept. A live JIRA or MCP
integration is therefore an example use case, not the architecture itself.

BTN-65 establishes six accepted Battalion-owned integration capability contracts in
RFC-0006: WorkSource, KnowledgeSource, RepositoryService, Notification,
OutboundEventSink, and HumanInteraction. Provider-adapter and transport
plugins implement those contracts; they do not create peer
capabilities or a privileged path around them. Built-in implementations remain
equally valid and do not require the plugin lifecycle.

## Candidate contract

A plugin architecture should make capabilities, permissions, compatibility,
configuration, provenance, and lifecycle state explicit. Battalion must remain
usable without optional plugins, and canonical tickets, run state, graph
control, human decisions, and accepted Intel must keep one declared owner.

## Required decisions

- Supported extension points and the boundaries that remain closed.
- Provider-adapter and transport implementations beneath the accepted
  Battalion capability contracts; plugin manifests cannot invent graph-facing
  generic tool authority.
- Manifest identity, versioning, compatibility, dependency, and provenance.
- Discovery, installation, enablement, configuration, update, disablement,
  removal, and data-retention behavior.
- Capability grants for filesystem, process, network, credentials, models,
  ticket mutation, and application commands.
- Secret ownership and redaction; secrets must not enter manifests, logs,
  fixtures, execution evidence, or model context by default.
- In-process versus isolated execution, timeouts, cancellation, crash
  containment, recovery, and offline degradation.
- Conflict resolution when a remote system disagrees with `backlog.json` or
  another repository source of truth.
- Audit evidence for plugin-originated reads, writes, commands, and failures.

## Threat and failure cases

The RFC must examine malicious or stale plugins, dependency compromise,
over-broad permission requests, confused-deputy writes, secret leakage,
unavailable remote systems, duplicate ticket mutation, incompatible upgrades,
and uninstall with retained data. Convenience cannot implicitly grant node
authority or bypass application operations.

## Deliverable

BTN-46 produces a decision-ready RFC with a minimal extension-point taxonomy,
manifest and permission sketches, lifecycle state machine, trust model, failure
semantics, and at least one disposable integration scenario. It must identify
which decisions require ADRs and split any accepted runtime, SDK, packaging,
and example integrations into separate tickets.

## Non-goals

- Implementing a plugin loader, marketplace, or live external integration.
- Moving canonical backlog ownership to a hosted service.
- Allowing plugins to invoke LangGraph, mutate `RunState`, or broaden a node's
  declared write scope without an explicit architectural decision.
