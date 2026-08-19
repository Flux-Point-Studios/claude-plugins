#!/usr/bin/env python3
"""The dense leg's provider plug: API embeddings, quarantined.

The recall layer ranks memory with three kinds of evidence — lexical
(BM25), structural (a walk over the derived memory graph), and, when a key
is present, dense vectors from an embedding API. The API is the one
non-deterministic input in the whole memory layer, so it is quarantined
here behind two rules the rest of the code can rely on:

  * An embedding can only ever change the ORDER of advisory recall output.
    Nothing embedded is stored as truth, gates nothing, and suppresses
    nothing — a missing or failing provider costs ranking quality, never
    correctness.
  * Every degradation names itself. No key resolves to the `none` provider
    and the dense leg is reported absent; an unknown FPL_EMBEDDER value is
    a hard error, because a misspelled provider that silently fell back
    would be the inert-field bug class all over again.

Providers form a closed registry. `voyage` is first in auto-resolution
because Voyage is the embeddings partner Anthropic's own docs point at and
its code-tuned models lead code-retrieval benchmarks; `openai` is the
common fallback; `none` is a fully supported mode, not an error state.
Vectors come back unit-normalized so dot product equals cosine, and every
text is cached by content hash so a rebuild only pays for what changed.

  embedder.py --status     say which provider would serve, without a call

Used as a module by recall.py; keys are read from the environment at call
time and never logged.
"""
import argparse
import base64
import hashlib
import json
import math
import os
import struct
import sys
import urllib.error
import urllib.request

# Closed registry. Adding a provider means adding it here, in resolve(),
# in _request(), and in embedder-test.py's resolution matrix.
PROVIDERS = ("voyage", "openai", "none")
DEFAULTS = {
    "voyage": {"model": "voyage-code-3", "dims": 256},
    "openai": {"model": "text-embedding-3-small", "dims": 512},
}
BATCH = 128
TIMEOUT = 20


class EmbedError(Exception):
    """A provider call failed. Callers degrade loudly; nothing retries here."""


def resolve(env=None):
    """{provider, model, dims, keyEnv} for the provider that would serve.

    FPL_EMBEDDER overrides; unset means first-key-wins in registry order.
    An unknown override is fatal rather than a fallback: the caller asked
    for something by name and silence would grant them something else.
    """
    env = os.environ if env is None else env
    forced = env.get("FPL_EMBEDDER", "").strip()
    if forced and forced not in PROVIDERS:
        raise SystemExit(
            f"embedder: unknown FPL_EMBEDDER {forced!r} — valid values: "
            f"{', '.join(PROVIDERS)}")
    if forced:
        provider = forced
    elif env.get("VOYAGE_API_KEY"):
        provider = "voyage"
    elif env.get("OPENAI_API_KEY"):
        provider = "openai"
    else:
        provider = "none"
    if provider == "none":
        return {"provider": "none", "model": "none", "dims": 0, "keyEnv": ""}
    d = DEFAULTS[provider]
    model = env.get("FPL_EMBED_MODEL", "").strip() or d["model"]
    dims = int(env.get("FPL_EMBED_DIMS", "").strip() or d["dims"])
    key_env = "VOYAGE_API_KEY" if provider == "voyage" else "OPENAI_API_KEY"
    if not env.get(key_env):
        # A forced provider with no key present is the same named failure.
        raise SystemExit(
            f"embedder: FPL_EMBEDDER={provider} but {key_env} is not set")
    return {"provider": provider, "model": model, "dims": dims,
            "keyEnv": key_env}


def _request(spec, texts, kind, env):
    """One provider call. Returns the raw vector list, order-preserving."""
    if spec["provider"] == "voyage":
        url = "https://api.voyageai.com/v1/embeddings"
        payload = {"input": texts, "model": spec["model"],
                   "input_type": kind, "output_dimension": spec["dims"]}
    else:
        url = "https://api.openai.com/v1/embeddings"
        payload = {"input": texts, "model": spec["model"],
                   "dimensions": spec["dims"]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {env[spec['keyEnv']]}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise EmbedError(f"{spec['provider']} HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise EmbedError(f"{spec['provider']} unreachable: {e}") from e
    data = body.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise EmbedError(
            f"{spec['provider']} returned {0 if not isinstance(data, list) else len(data)} "
            f"vector(s) for {len(texts)} text(s)")
    return [row.get("embedding") for row in data]


def _normalize(vec, dims):
    if not isinstance(vec, list) or len(vec) != dims:
        raise EmbedError(
            f"vector has {len(vec) if isinstance(vec, list) else 'no'} "
            f"dimension(s), expected {dims}")
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0:
        raise EmbedError("zero-norm vector")
    return [x / norm for x in vec]


def embed(texts, kind, spec, transport=None, env=None):
    """Unit-normalized vectors for texts, batched. kind: document|query.

    `transport` exists for tests: it replaces the HTTP call with a fake so
    the harness never touches the network. Raises EmbedError on any
    provider failure — the caller decides what a missing leg means.
    """
    env = os.environ if env is None else env
    if spec["provider"] == "none":
        raise EmbedError("no embedder configured")
    call = transport or _request
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        out.extend(_normalize(v, spec["dims"])
                   for v in call(spec, chunk, kind, env))
    return out


def pack(vec):
    """base64 of little-endian float32, the vector-file row encoding."""
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode("ascii")


def unpack(b64, dims):
    raw = base64.b64decode(b64)
    if len(raw) != dims * 4:
        raise ValueError(f"packed vector is {len(raw)} bytes, expected {dims * 4}")
    return list(struct.unpack(f"<{dims}f", raw))


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true", required=True,
                    help="print the provider that would serve, and why")
    ap.parse_args()
    spec = resolve()
    if spec["provider"] == "none":
        print("embedder: none (no VOYAGE_API_KEY or OPENAI_API_KEY in the "
              "environment; recall runs lexical + graph only)")
    else:
        print(f"embedder: {spec['provider']} {spec['model']} @{spec['dims']}d "
              f"(key from {spec['keyEnv']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
