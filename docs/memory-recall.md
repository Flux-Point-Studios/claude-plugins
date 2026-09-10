# Hybrid memory recall

Design notes for the recall layer in the fluxpoint plugin: what it indexes, how it ranks, and what it refuses to do. The user-facing summary is in the repository README.


`memory.jsonl` remembers what campaigns established; until v1.26 the only
way back in was an exact `tag|dedupeKey` match, so a lesson about
beacon-prefix derivation was invisible to a session working on two-way
asset beacons. `scripts/recall.py` closes that gap with the architecture
the research points at — Graphiti's read path without its write path —
while adding **no store, no writer, no dependency, and no daemon**:

- **The graph is a projection, not a store.** `recall.py --build` compiles
  a typed knowledge graph deterministically from the stores that already
  exist — lessons, run artifacts, decisions (keyed by the import id the
  compiler already resolves), counterexample ledger entries, and the repo's own
  `substrate.json` primitives (sanitized as hostile input; consumes-edges
  kept walkable across the repo boundary) — into gitignored
  `.claude/fluxpoint/index/`. Every relation is schema-native (provenance,
  supersession, kill events, path components inside dedupe keys, campaign
  membership), so there is no LLM extraction step to pay for or to
  hallucinate: the evidence (LazyGraphRAG, HippoRAG 2's ablations,
  verbatim-beats-extracted) says extraction subtracts value when the data
  is already typed. Identical sources build byte-identical indexes, and a
  malformed line in a *source* store is still a hard error while a damaged
  *index* file is deleted and rebuilt out loud.
- **Bi-temporal by derivation.** Each lesson identity carries its full
  append chain, so recall serves the current version by default,
  `--as-of <ts>` serves the version that held then, and a kill stays a
  permanently valid `KILLED_BY` edge even though the claim it killed is
  not — kills are priors, never suppressors. A killed lesson whose touched
  file changed after the kill is marked `stale: <file> changed since`
  at build time — annotated, never dropped, because a finding that comes
  back after the code moved is exactly the regression a sweep exists to
  catch.
- **Hybrid retrieval, evidence-shaped.** Up to four legs — BM25 over an
  identifier-aware tokenization of the query, BM25 over path tokens from
  files touched, cosine against API embeddings, and personalized PageRank
  from tag/file/campaign seeds with hub-resistant specificity weights —
  fused with weighted reciprocal-rank fusion (k=60), then boosted by
  re-establishment count (a lesson filed by many runs outranks a one-off)
  and gentle recency decay on lessons only.
- **The embedder is quarantined.** `scripts/embedder.py` is a closed
  provider registry — `voyage` (default `voyage-code-3` at 256 dims;
  Anthropic's documented embeddings partner, and its code-tuned models
  lead code retrieval), `openai` (`text-embedding-3-small` at 512),
  `gemini` (`gemini-embedding-001` at 768), or `none` — resolved by key
  presence (`VOYAGE_API_KEY`, then `OPENAI_API_KEY`, then
  `GEMINI_API_KEY`) or forced with `FPL_EMBEDDER`; an unknown value is a
  hard error, never a silent fallback, and
  `FPL_EMBED_MODEL`/`FPL_EMBED_DIMS` tune the model.
  Vectors are cached by content hash (rebuilds re-embed only what
  changed), stored unit-normalized one file per model, and compared by
  brute-force dot product — at this corpus size a vector database would
  be a dependency, not a speedup. The API is the **single sanctioned
  non-deterministic input** in the memory layer, and it can only ever
  reorder advisory output: nothing embedded is stored as truth, gates
  anything, or suppresses anything. Keyless is a fully supported mode —
  BM25 + graph serve, and every result names the absent leg.
- **Injection is budgeted per site.** SessionStart injects the top 5
  lessons ranked against the work file and the session's touched paths —
  offline, never rebuilding, capped at 1200 bytes, elisions named,
  `FPL_RECALL_INJECT=0` to demote. `/fluxpoint:graph-run` seeds sweeps
  through `recall.py --format seedmap`, which emits exactly the shape
  `memory.py --load` prints with keys relevance-ordered — the compiled
  graph's advisory-seed contract is untouched. A node declaring
  `memory: {priors: true}` additionally hands its refuters the seed tag's
  killed claims with the objections that killed them (5 items, 400 chars
  each), framed as priors the panel may overturn — the panel stops paying
  to rediscover arguments the store already holds, and the finder's
  prompt stays clean. `/fluxpoint:recall` serves humans. A per-prompt
  UserPromptSubmit hook exists but ships **dark** behind
  `FPL_MEM_PROMPT=1`: offline, 3 items, 1200 bytes, and a precision floor
  — two independent retrieval legs, or a lexical match on at least two
  informative query tokens; the graph leg never corroborates, because its
  seeds come from the lexical top ranks — off by default because the
  strongest external result says wrongly retrieved memories cost more
  than none.
- **Curation is a campaign, not a daemon.** `templates/WORK.consolidate.md`
  reads the store, proposes merges and restatements as findings, has a
  skeptic attack each one with the killed priors in hand, and lets
  `record-run.py` file the survivors through the same single-writer path —
  supersession by append, never deletion. Run it by hand or from a
  scheduled Routine.

What it refuses, on purpose: LLM extraction or reranking anywhere in the
write or rank path; any new dependency (no numpy, faiss, sqlite-vec, or
local models); graph databases, bundled MCP servers, daemons; new writers
to `memory.jsonl`; and retrieval-driven suppression of any kind — ranking
decides what reaches a prompt first, never what gets judged.

