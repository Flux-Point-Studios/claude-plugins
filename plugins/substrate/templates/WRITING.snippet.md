# Outward prose: no AI writing smell

Every doc, message, reply, and email that leaves the building reads like a
person wrote it, because a person signs it. The prose-smell gate
(`prose-smell.mjs`, wired as a PostToolUse hook) catches the mechanical
frames; these rules cover what a regex cannot.

- **Banned frames** (the gate fails these): contrastive negation
  ("X isn't just about A — it's about B"), "whether you're A, B, or C"
  inclusive triples, sentence-initial "From X to Y," coverage sweeps,
  assistant artifacts ("Would you like me to…", "I hope this helps"), and
  stock LLM vocabulary (delve, tapestry, testament to, pivotal role,
  ever-evolving, fast-paced world, seamlessly integrate).
- **Normal writing is not a tell.** Metaphors, similes, qualifiers, clean
  structure, and an em dash in moderation are centuries-old tools — do not
  sand them off. The gate only notes em-dash DENSITY, and only as advice for
  external artifacts.
- **The deepest tell is undefendable content, not phrasing.** Every claim in
  an outward artifact must be something its human sender can defend cold in
  a follow-up conversation: numbers from measurement, sources named, caveats
  attached. If the sender could not survive a Q&A on it, it does not ship.
- **Human pacing.** Do not fire five polished artifacts at one recipient in
  an hour. Volume is the most reliable tell there is; batch, sequence, and
  let the sender set the cadence.
- **Say it once, plainly.** Declarative sentences, specific nouns, real
  numbers. When a sentence exists only to sound balanced or comprehensive,
  delete it.
