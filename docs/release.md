# Releases and distribution

For installation, artifact verification, and a first disposable project, use
[Getting Started](getting-started.md). It distinguishes a named UAT candidate
from an available published release; the workflow below does not imply that a
release already exists. Source contributors use [contributor setup](contributing.md).

## Release contract

`pyproject.toml` is the single source of truth for the Battalion application
and Python-package version. Its `[project].version` is currently `0.1.0`.
No other file, tag, artifact name, or GitHub Release defines a competing
version.

Battalion follows [Semantic Versioning 2.0.0](https://semver.org/). Before
`1.0.0`, incompatible contract changes advance the minor version and compatible
fixes advance the patch version. After `1.0.0`, incompatible changes advance
the major version, compatible features advance the minor version, and fixes
advance the patch version. Pre-release and build metadata may be used only when
they comply with SemVer and the tag remains exactly `v<version>`.

A release is a human decision, not a side effect of merging or pushing to
`main`. A maintainer starts one by creating and pushing an exact version tag on
a tested `main` commit, for example `v0.1.0`. Only `v*` tag pushes invoke the
release workflow. The workflow refuses to proceed unless the tag equals
`v` plus the declared package version.

## Maintainer flow

BTN-173 integrates the candidate into `main` without tagging or publishing a
release. Its handoff must identify the merged revision, version, applicable
artifact filenames/checksums, guide/UAT scripts, and known limitations. CLI
UAT (BTN-129), desktop UAT (BTN-132), and external-integration dogfooding
(BTN-80) remain separate gates; BTN-80 owns the final v1.0 readiness decision.
The empty-Architect response failure remains BTN-129 remediation, and the
frozen-worker pytest limitation remains BTN-132 remediation. Neither is
accepted merely by a successful integration build or prompt smoke check.

1. Merge the approved change set to `main` and verify its ordinary CI.
2. Confirm the intended SemVer version in `pyproject.toml`; commit that change
   before the release tag.
3. Create and push the matching maintainer-created tag, such as `v0.1.0`.
4. The tag workflow validates the tag/version pair, runs the credential-free
   deterministic test suite, then builds artifacts. It verifies the complete
   declared prompt inventory in the wheel and sdist, installs the wheel in a
   clean virtual environment outside the source checkout, exercises
   `battalion --help`, and loads every shipped prompt. Canonical Issue validation
   is performed separately by the status-governance workflow.
5. It uploads wheel, source distribution, Windows desktop ZIP, provenance
   metadata, and SHA-256 checksum files to a GitHub Release for that tag.

The provenance metadata identifies the tag, source revision, version, and the
version source. Each artifact set includes a checksum file; checksums are
integrity evidence, while the tag/revision metadata identifies the exact source
that produced it.

GitHub generates the release notes from the repository's merged PR history.
Maintainers review its linked canonical GitHub Issues and the applicable
milestone before tagging, and correct the PR/Issue links before release if
needed. GitHub Issues, milestones, and PR history are the release-notes source;
Battalion maintains no parallel local release ledger.

The first workflow intentionally **does not publish to PyPI or any other
package registry**. Publishing there is a separate maintainer decision and
must add an explicit trusted-publishing or credential design before it is
enabled.

## Initial supported desktop distribution

The initial end-user platform is Windows x64. The GitHub Release supplies a
self-contained ZIP of the existing PySide6/Nuitka desktop and worker sibling
distributions. Extract it to a user-writable directory and start the Battalion
desktop executable. This is a deliberate initial packaging mechanism, not a
claim that a raw Python wheel or a Nuitka directory is a sufficient onboarding
experience.

The frozen worker explicitly includes Battalion's package-owned role prompts.
Release validation starts the produced worker in its credential-free prompt
smoke mode and requires every declared prompt to load from the bundle before
the desktop ZIP is assembled.

A conventional native installer (for example MSIX or a signed installer) is
deferred. It needs a separate decision covering code signing, per-user versus
per-machine scope, upgrade/rollback behavior, uninstall, Windows SmartScreen
experience, and clean-machine validation. macOS and Linux artifacts are also
out of scope until their packaging and support evidence exists.

## First-run onboarding direction

The current artifact-based CLI/configuration and desktop inspection path is
documented in [Getting Started](getting-started.md), including the frozen
worker's pytest execution limitation. Prompt smoke validation alone is not
end-to-end desktop UAT acceptance.

Release packaging is mechanical; onboarding owns user-specific configuration.
The eventual first-run experience will:

1. offer local-runtime discovery or setup and remote-provider configuration;
2. collect API keys only through Battalion's credential boundary, never in
   artifacts or project configuration;
3. validate the selected configuration with a bounded sanity check;
4. present a basic guided introduction and first-project path; and
5. let the user explicitly choose models to pull/install and proposed role
   assignments before any change is made.

Existing `python -m battalion setup` remains the current guided provider/model
configuration path. It does not make the release installer a credential or
model-installation authority.

[`whichllm`](https://github.com/Andyyyy64/whichllm) is a promising advisory
input: it detects local hardware, estimates fit/speed, and emits JSON results.
Its data and ranking can refresh from external model and benchmark sources, and
its results answer *can run here*, not *works well for Battalion workloads*.
Therefore a future integration must capture source/version/time provenance and
combine compatible hardware-fit candidates with separately versioned
Battalion-specific evaluation or community evidence. It must visibly distinguish
the two kinds of evidence, surface uncertainty/staleness, and never silently
install models or assign a model to a role. Implementing that integration,
runtime-specific model pulls, credential UX, tutorial UI, and a native installer
are follow-up tickets, not hidden work in release CI.
