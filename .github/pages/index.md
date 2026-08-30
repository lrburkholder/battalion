---
title: Human-directed software delivery
layout: default
---

<section class="hero">
  <div class="hero-copy">
    <p class="eyebrow">Human-directed SDLC orchestration</p>
    <h1>Keep the engineer in command.<br><span>Put the workflow to work.</span></h1>
    <p class="lede">Battalion coordinates specialized AI roles through a transparent delivery loop, with mechanical write scopes, explicit human interrupt points, and durable evidence for every attempt.</p>
    <div class="hero-actions">
      <a class="button primary" href="https://github.com/lrburkholder/battalion">Explore the repository</a>
      <a class="button" href="docs/operator/workflow.html">Read the operator guide</a>
    </div>
  </div>
  <div class="hero-mark" aria-hidden="true">
    <img src="{{ '/assets/mark-transparent.svg' | relative_url }}" alt="" width="280" height="280">
  </div>
</section>

<section class="pipeline" aria-labelledby="workflow-heading">
  <div class="section-heading">
    <p class="eyebrow">The shipped workflow</p>
    <h2 id="workflow-heading">One loop. Distinct roles. Visible decisions.</h2>
  </div>
  <ol class="role-grid">
    <li><span>01</span><strong>Architect</strong><p>Plans the implementation and records durable decisions.</p></li>
    <li><span>02</span><strong>Driver · RED/GREEN</strong><p>Writes tests first, then implements within approved paths.</p></li>
    <li><span>03</span><strong>Reviewer</strong><p>Checks each checkpoint against the specification and evidence.</p></li>
    <li><span>04</span><strong>Refactorer</strong><p>Improves clarity without changing accepted behavior.</p></li>
  </ol>
  <p class="loop-line"><code>Architect → Driver RED → Reviewer → Driver GREEN → Reviewer → Refactorer → Reviewer → done</code></p>
</section>

<section class="showcase" aria-labelledby="showcase-heading">
  <div class="section-heading">
    <p class="eyebrow">Production desktop</p>
    <h2 id="showcase-heading">See the work, the evidence, and the human checkpoints.</h2>
    <p>These are reviewed captures of the shipped PySide6 client using deterministic, credential-free demo data—not product mockups or live provider claims.</p>
  </div>

  <figure class="feature-shot">
    <a href="{{ '/assets/screenshots/battalion-work.png' | relative_url }}">
      <img src="{{ '/assets/screenshots/battalion-work.png' | relative_url }}" alt="Battalion Work view showing an awaiting-human BTN-57 run, execution map, evidence summary, interrupt resolution, and next-attempt controls." width="1380" height="860" loading="eager">
    </a>
    <figcaption><strong>Work stays actionable.</strong> Inspect a paused run, understand why it stopped, then resolve the interrupt or queue bounded context for the next approved attempt.</figcaption>
  </figure>

  <div class="shot-grid">
    <figure>
      <a href="{{ '/assets/screenshots/battalion-history.png' | relative_url }}">
        <img src="{{ '/assets/screenshots/battalion-history.png' | relative_url }}" alt="Battalion History view showing a completed run and its reviewer execution evidence, verification result, and operator summary." width="1380" height="860">
      </a>
      <figcaption><strong>History retains the proof.</strong> Every node attempt keeps its role, phase, verification, provenance, and operator handoff inspectable.</figcaption>
    </figure>
    <figure>
      <a href="{{ '/assets/screenshots/battalion-intel.png' | relative_url }}">
        <img src="{{ '/assets/screenshots/battalion-intel.png' | relative_url }}" alt="Battalion Intel view showing accepted knowledge and a pending Recon candidate with evidence and human review controls." width="1380" height="860">
      </a>
      <figcaption><strong>Learning remains human-reviewed.</strong> Recon proposes candidate knowledge; an operator decides whether it becomes accepted Intel.</figcaption>
    </figure>
  </div>
  <p class="text-path">Prefer text? The <a href="docs/operator/screens.html">screen contract</a> and <a href="docs/operator/workflow.html">operator workflows</a> describe the same shipped surfaces and authority boundaries.</p>
</section>

<section class="principles" aria-labelledby="principles-heading">
  <div class="section-heading">
    <p class="eyebrow">Designed for leverage</p>
    <h2 id="principles-heading">Automation without invisible authority.</h2>
  </div>
  <div class="principle-grid">
    <article><h3>Mechanical boundaries</h3><p>Writing roles receive only tools scoped to approved paths. Reviewer remains read-only.</p></article>
    <article><h3>Meaningful interrupts</h3><p>Repeated rejection, budget limits, role changes, infrastructure failure, and manual checkpoints stay explicit.</p></article>
    <article><h3>Durable evidence</h3><p>Run state, execution attempts, costs, artifacts, and human actions survive client restarts.</p></article>
  </div>
</section>

<section class="truth-panel">
  <div>
    <p class="eyebrow">What exists today</p>
    <h2>The v1 graph and production desktop operator workflow are implemented.</h2>
    <p>Start with the <a href="spec.html">shipped contract</a>, trace decisions in the <a href="docs/adrs/">ADR index</a>, inspect current delivery status in <a href="docs/status.html">project status</a>, read the <a href="docs/release.html">release guide</a>, or browse the <a href="https://github.com/lrburkholder/battalion/issues">canonical GitHub Issues backlog</a>.</p>
  </div>
  <aside>
    <strong>Roadmap ≠ shipped behavior</strong>
    <p>History search and analytics, plugin architecture, additional roles, and self-modification remain future work unless the specification and backlog say otherwise.</p>
  </aside>
</section>
