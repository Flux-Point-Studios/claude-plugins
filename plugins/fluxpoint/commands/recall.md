---
description: Search the memory graph — lessons, kills, counterexamples — by meaning, identifiers, and graph proximity; or build/inspect the index.
argument-hint: [query text, or "build" / "stats"]
---

Search what earlier campaigns established, or maintain the index that
makes it searchable. Read-only against the stores; the index under
`.claude/fluxpoint/index/` is a rebuildable projection, never a second
source of truth.

1. If the argument is `build`, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" recall.py --build --embed`
   and report what it printed: docs indexed, dense provider, pending
   embeddings, dropped path candidates, stale kills. `--embed` backfills
   vectors only when an embedder key is present (`VOYAGE_API_KEY`,
   `OPENAI_API_KEY`, or `GEMINI_API_KEY`, in that order; `FPL_EMBEDDER`
   overrides) — keyless is a supported mode, not a failure, and the
   output says which mode ran.

2. If the argument is `stats`, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" recall.py --stats` and
   `... embedder.py --status`, and report both lines plus whether
   `--verify` says the index lags its sources.

3. Otherwise treat the argument as the query:
   ```
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" recall.py --query "<argument>" --k 12
   ```
   Add `--files <comma-separated paths>` when the question is about
   specific files (the identifier leg is exact-match strong there), and
   `--tag <t>` to anchor a tag. For history questions, `--as-of <ISO
   timestamp>` serves the version that held then, and
   `--include-superseded` lifts the default current-state filter.

4. Present the results as they came: status labels (`surviving` /
   `killed`), the killing objection, run provenance, and any
   `stale: <file> changed since` marks are the payload, not noise. A
   killed lesson is a prior, not a verdict — if one looks re-live because
   its file changed, say so; that is exactly what the mark is for.

5. If the output names a degradation — dense leg absent, index stale,
   nothing indexed yet — repeat it to the user verbatim rather than
   smoothing it over, and name the fix it suggests.
