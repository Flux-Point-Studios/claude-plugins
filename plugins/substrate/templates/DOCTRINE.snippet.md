# Compounding leverage: build things that multiply what we already have

Every new thing must make at least one existing thing more valuable.
Additive features are tax; multiplicative features are the work.

- **The substrate sweep comes first.** Before any new build, sweep the
  generated registry (`SUBSTRATE.md`) for the half-built thing that already
  covers part of this, and name the overlap in the plan — even when the
  answer is "nothing fits, here's why."
- **The sweep fires on repetition, not just on builds.** The second time the
  same manual step, workaround, redeploy, or "open problem" appears, treat
  it as a build proposal and sweep for the half-built thing that kills it.
  A cost paid twice without a named owner is the sweep's other entry point.
- **Shipping a primitive means updating that repo's `substrate.json` in the
  same commit.** A manifest refreshed later is drift, and the staleness
  alarm will say so at session start.
- **Orphans are dormant value.** A primitive with no consumers is a
  capability waiting to be activated; wiring one into a consumer is usually
  worth more than a greenfield build.
- **Hubs get hardened first.** A primitive with two or more consumers is
  load-bearing: its tests, docs, and interfaces deserve investment before
  anything that depends on it grows.
- **Demos and pitch artifacts stay out of the substrate** and are marked as
  such — they get no manifest, so the registry never mistakes a prop for a
  primitive.
