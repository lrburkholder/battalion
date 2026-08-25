# BTN-86 persistence-layer control case

This directory is a disposable, provider-free comparison harness for the
Cartography persistence decision deferred by RFC-0008. It does not change
`battalion.state.persistence`, introduce production Cartography types, or make
a storage selection.

Every candidate consumes the same tiny socio-technical map and must prove:

- deterministic bounded retrieval and two-hop relationship traversal;
- a revision diff that leaves a governing, human-authored record distinct from
  a changed generated record;
- failed publication retains the last completed revision; and
- a candidate that emits a Markdown projection identifies it as generated.

The harness calls each adapter's own `retrieve`, `traverse`, and `diff`
operations. It must not validate a candidate merely by loading the entire map
into a generic in-memory projection and querying that projection instead.

The initial candidates are Markdown charters (control), repository-native
structured files, SQLite, a portable embedded graph adjacency representation,
SQLite-plus-Markdown and graph-JSON-plus-Markdown projection hybrids. They use
only the Python standard library; a hosted service, raw graph handle, or
provider credential is out of scope.

Run the common control case from the repository root:

```console
python -c "from pathlib import Path; from benchmarks.persistence.runner import run_all; print(run_all(Path('tmp/persistence-spike')))"
```

## Architectural integration simplicity

This spike weights integration evidence above throughput. For every candidate,
retain the exact command output and record: additional runtime dependencies,
new production interfaces, whether any raw-store access would leak past a
Battalion-owned repository/query contract, candidate-specific files and lines,
schema migration code, atomic-publication/recovery complexity, projection
authority, portability, inspectability, and test diagnostics. The common
control case only establishes that all candidates can be compared; it is not
evidence that any candidate is fit for production.

The final selection must be a separate ADR grounded in those measurements.

## Graph JSON plus Markdown projection spike

`graph-adjacency-markdown` uses graph-adjacency JSON as the only canonical
store and emits a readable, generated Markdown charter. Its focused integration
spike proves changed-path lookup after a symbol rename, explicit deletion and
addition reporting, preservation of a governing authored constraint, and a
visible `stale` projection state if canonical publication succeeds but
projection generation fails. The Markdown contains no hidden canonical record
envelope and cannot be treated as an editable input.
