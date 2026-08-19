#!/usr/bin/env python3
"""The embedder plug: quarantine properties, executed.

The provider is the one non-deterministic input the recall layer accepts,
so what these cases pin is the quarantine, not the vectors: resolution is
a closed registry where a misspelling is fatal rather than a silent
fallback, the keyless mode is a state and never an error, no code path
here touches the network when a test transport is injected, and every
vector that leaves is unit-normalized so dot product equals cosine
everywhere downstream.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import embedder  # noqa: E402

passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    ok = bool(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<56} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


# ==================== resolution matrix ====================================
spec = embedder.resolve(env={})
report("no keys resolves to none, not an error",
       spec == {"provider": "none", "model": "none", "dims": 0, "keyEnv": ""},
       str(spec))

spec = embedder.resolve(env={"VOYAGE_API_KEY": "k"})
report("voyage key alone serves voyage defaults",
       spec["provider"] == "voyage" and spec["model"] == "voyage-code-3"
       and spec["dims"] == 256, f"{spec['model']} @{spec['dims']}d")

spec = embedder.resolve(env={"OPENAI_API_KEY": "k"})
report("openai key alone serves openai defaults",
       spec["provider"] == "openai"
       and spec["model"] == "text-embedding-3-small" and spec["dims"] == 512,
       f"{spec['model']} @{spec['dims']}d")

spec = embedder.resolve(env={"VOYAGE_API_KEY": "k", "OPENAI_API_KEY": "k"})
report("voyage outranks openai when both keys exist",
       spec["provider"] == "voyage", spec["provider"])

spec = embedder.resolve(env={"FPL_EMBEDDER": "openai",
                             "VOYAGE_API_KEY": "k", "OPENAI_API_KEY": "k"})
report("FPL_EMBEDDER overrides key order",
       spec["provider"] == "openai", spec["provider"])

spec = embedder.resolve(env={"VOYAGE_API_KEY": "k",
                             "FPL_EMBED_MODEL": "voyage-code-4",
                             "FPL_EMBED_DIMS": "512"})
report("model and dims are env-tunable",
       spec["model"] == "voyage-code-4" and spec["dims"] == 512,
       f"{spec['model']} @{spec['dims']}d")

try:
    embedder.resolve(env={"FPL_EMBEDDER": "graphiti", "VOYAGE_API_KEY": "k"})
    report("an unknown FPL_EMBEDDER value is fatal", False, "resolved anyway")
except SystemExit as e:
    report("an unknown FPL_EMBEDDER value is fatal",
           "graphiti" in str(e) and "valid values" in str(e), str(e)[:70])

try:
    embedder.resolve(env={"FPL_EMBEDDER": "voyage"})
    report("a forced provider with no key is fatal", False, "resolved anyway")
except SystemExit as e:
    report("a forced provider with no key is fatal",
           "VOYAGE_API_KEY" in str(e), str(e)[:70])

# ==================== embed mechanics ======================================
ENV = {"VOYAGE_API_KEY": "k-test-not-real"}
SPEC = embedder.resolve(env=ENV)
calls = []


def transport(spec, texts, kind, env):
    calls.append({"n": len(texts), "kind": kind, "model": spec["model"]})
    return [[1.0 if i == j % spec["dims"] else 0.5
             for i in range(spec["dims"])] for j in range(len(texts))]


vecs = embedder.embed(["alpha", "beta"], "document", SPEC,
                      transport=transport, env=ENV)
norms = [math.sqrt(sum(x * x for x in v)) for v in vecs]
report("vectors leave unit-normalized",
       all(abs(n - 1.0) < 1e-9 for n in norms),
       f"norms {[round(n, 9) for n in norms]}")
report("the transport sees the query/document kind",
       calls[0]["kind"] == "document" and calls[0]["model"] == "voyage-code-3",
       str(calls[0]))

calls.clear()
embedder.embed([f"t{i}" for i in range(200)], "query", SPEC,
               transport=transport, env=ENV)
report("requests are batched at 128",
       [c["n"] for c in calls] == [128, 72], str([c["n"] for c in calls]))

try:
    embedder.embed(["x"], "query",
                   {"provider": "none", "model": "none", "dims": 0,
                    "keyEnv": ""}, env={})
    report("embedding with provider none raises, never guesses",
           False, "returned vectors")
except embedder.EmbedError as e:
    report("embedding with provider none raises, never guesses",
           "no embedder" in str(e), str(e))


def short_transport(spec, texts, kind, env):
    return [[1.0] * (spec["dims"] - 1) for _ in texts]


try:
    embedder.embed(["x"], "query", SPEC, transport=short_transport, env=ENV)
    report("a wrong-dimensional vector is refused", False, "accepted")
except embedder.EmbedError as e:
    report("a wrong-dimensional vector is refused",
           "dimension" in str(e), str(e)[:70])

# ==================== packing ==============================================
v = [0.25, -0.5, 0.125, 1.0]
report("pack/unpack round-trips float32 exactly",
       embedder.unpack(embedder.pack(v), 4) == v, "4d round trip")
try:
    embedder.unpack(embedder.pack(v), 8)
    report("a truncated packed vector is refused", False, "accepted")
except ValueError as e:
    report("a truncated packed vector is refused", "bytes" in str(e),
           str(e)[:60])

report("content hashing is stable",
       embedder.text_sha("claim") == embedder.text_sha("claim")
       and embedder.text_sha("claim") != embedder.text_sha("claim2"),
       embedder.text_sha("claim")[:16])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
