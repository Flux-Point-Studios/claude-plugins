---
description: Scan a file (or all staged deliverables) for AI-writing smell before it ships
---

Run the prose-smell gate on the file(s) the user names — or, with no
argument, on every prose file changed in the working tree:

```
node "${CLAUDE_PLUGIN_ROOT}/scripts/prose-smell.mjs" <files…>
```

Exit 2 means HIGH findings (contrastive-negation frames, whether-you're
triples, assistant artifacts, stock LLM vocabulary) — rewrite those
sentences as plain declarative statements and re-run until clean. Medium
findings (from-to coverage openers, em-dash density) are advice: apply
judgment, especially for external artifacts.

The gate deliberately does not flag metaphors, qualifiers, or structure —
normal writing is not a tell. What it cannot check, you must: every claim
defensible cold by the human sender, and human pacing on sends.
