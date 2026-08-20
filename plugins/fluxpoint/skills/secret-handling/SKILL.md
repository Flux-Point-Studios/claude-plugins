---
name: secret-handling
description: How to work with credentials at Flux Point — never reading a secret, passing its path to code that emits only public derivations, keeping a passphrase off the command line, filtering config dumps at the source, and the repr hazard that leaks a signing key through an f-string or a traceback. Use whenever the work touches a mnemonic, a wallet file, a signing key, a passphrase, a signed transaction or witness, a .env or an API token, whenever a permission rule denies a read the work actually needs, whenever an operator is about to hand-edit settings.json to unblock a session, or whenever custody of a key must be proved, restored or rotated, even if nobody says "secret handling".
---

# Secret Handling

An agent that must not see a credential can still work with one. The whole
technique is a change of shape:

> **Never read a credential, and never make one an argument. Hand the
> deriving process a path or a pipe; it emits only public derivations.**

```sh secret-shapes
UNSAFE  cat D:/wallet/mnemonic.txt
UNSAFE  python -c "from aegis.wallet import load_wallet; print(load_wallet('D:/wallet/mnemonic.txt').address)"
SAFE    python -m ops.derive D:/wallet/mnemonic.txt
SAFE    python -m ops.derive D:/backup/aegis_vault_clean.txt --equals "$LIVE_ADDRESS"
SAFE    printf %s "$PASSPHRASE" | python -m ops.derive D:/wallet/mnemonic.txt --passphrase-stdin
```

All five read the same seed phrase off disk. The last three are safe
because the process that read it emitted an address or a boolean, and an
address is a one-way function of the key — useless to whoever reads the
transcript. Not a weaker form of access: the same access, with the output
narrowed to what the work actually needed.

The second line is the trap, and the one most likely to be copied. A
derivation is only as safe as its error path: the loader quotes the file's
contents into its own exception — wrong word count, a BOM, CRLF, a `.skey`
where a mnemonic was expected — and `python -c` prints that straight into
the transcript. The safe shapes call an entrypoint (`ops/derive.py`, the
barrier below) because a one-liner has nowhere to put the barrier.

A secret with no path obeys the rule from the other side. A BIP39
passphrase, a key-file password, the token the deriving process itself
needs: none may become an argument, because the command line is the
transcript and the process table both, and it stays in shell history. It
arrives on stdin, or the process loads its own `.env`.

Reach for `--equals` first: most questions about a credential — *is this
the live key, did the restore work* — have a boolean as their honest
answer.

## Why a deny rule is the wrong default

A `Bash(railway:*)` deny stops the leak and the work in one stroke. It
matches on the path, so it cannot tell `cat mnemonic.txt` from a derivation
that reads the same file and prints a public key hash — the two shapes are
indistinguishable to it and opposite in consequence.

Read, Grep and NotebookRead are the exception, stated once so nobody draws
the wrong lesson from the paragraph above: their entire output *is* the
file. There is no narrowed shape for them, so a deny on those paths is
correct and should stay. Bash is where a deny costs the work, because Bash
is where a path can be handed to code that emits a derivation.

That cost was paid in one night: a genesis blocker the agent could have
discharged safely stayed open for hours, the operator hand-edited
`settings.json` twice mid-session, and a global allow could not reach past
a project-level deny — so the edit had to land in the very file governing
the session in flight. Widening a permission rule under time pressure, in
the session about to touch key material, is the worst available moment.

Proxies are not properties. A deny measures which file was opened; the
property is what left the process. Keep denies on the Read channel, and for
values with no public derivation and no legitimate local use — another
party's key material, a production database dump. Everywhere else, narrow
the emission instead.

`scripts/secret-guard.py` makes that distinction mechanical: given a
`.fluxpoint-secrets.json` declaring which paths hold credentials, it
refuses the command that would print one and permits the derivation that
reads the same file. It ships beside this skill and is **not installed by
default** — wire it as a PreToolUse hook in the project that needs it. It
stays dormant in a repo that declares nothing.

## What counts as public

Public: verification key hashes, payment and stake addresses, policy ids,
script hashes, datum hashes, transaction hashes, pool ids, anything a block
explorer will already show a stranger.

Not public: mnemonics and seed phrases, `xprv` and any signing key in any
encoding, the CBOR hex of a signing key, API tokens and project ids,
session cookies, JWTs, webhook URLs carrying a token in the path. Two more
belong there and are the two that get missed:

- **Bearer instruments** — a signed transaction or its `cborHex`, a
  detached vkey witness, a partial multisig witness, any signed payload not
  yet submitted or still inside its replay window. None contains a key and
  each causes the effect once. Print a `tx.signed` and submission belongs
  to every reader of the transcript; on a native-script treasury, one
  leaked witness plus M-1 co-signers is a quorum.
- **Extended public keys** — `xpub`, `acct_xvk`, any verification key
  carrying its chain code. The chain code is what matters: it enumerates
  every address in the account with its balances, and combined with one
  soft-derived child private key it reconstructs the account `xprv` — a
  whole-wallet compromise from two values that each look harmless.

For a value you have not seen before, four questions, all of which must
answer clean:

1. **Does holding it cause an effect even once, without being able to sign
   anything new?** First, because the other three cannot express it: a
   64-byte signature is one-way, shorter than the key, and signs nothing —
   and it still moves the funds.
2. **Is it a one-way function of the secret, and shorter than it?** Length
   is the tell. A 28-byte blake2b hash cannot be run backwards. A bech32
   string the size of the key is the key wearing a hat — `addr1…` is
   public, `acct_xvk1…` is not, and neither is `ed25519e_sk1…`. A
   verification key *hash* is public; the extended verification *key* is
   not.
3. **Could someone holding it sign, spend, authenticate, or impersonate?**
   If yes it is a credential regardless of encoding or where it was found.
4. **Is it already published?** If the chain has it, printing it costs
   nothing. This one only ever adds: a fresh address is public and
   unpublished, so unpublished is not thereby secret.

Cannot answer all four? Treat it as secret and derive something you can. An
address is public, but binding it to an operator's identity in a shared
transcript is a privacy decision, not a custody one — decide that
deliberately rather than by default.

## Three disciplines

**Isolate the tree, not the secret.** Verification often needs a different
build than the one you are standing in — a mainnet-compiled checkout while
the working tree is preprod. Never mutate the live tree to get it:

```sh
git archive HEAD | tar -x -C "$ISO" && cp .fluxpoint-secrets.json "$ISO/" \
  && git -C "$ISO" init
```

The campaign tree never changes state, so a verification that fails halfway
leaves nothing to clean up. The manifest copy is not a caveat:
`.fluxpoint-secrets.json` is working-tree state, not HEAD state, and a
declaration added mid-drill is exactly the one not committed yet. An
isolated tree without it reports that nothing is declared — which reads
like a repo that has no secrets. Skip that line and the discipline that
puts credential work in a fresh tree is the one that disarms the gate.

**The tool refuses rather than guesses.** A custody verifier must check
that the checkout it is running actually builds the network it is
certifying against, and refuse when it does not. A tool that certifies
against the wrong bytes is worse than no tool, because it produces a
document people stop questioning.

```python secret-precondition
def require_network_match(built: str, claimed: str) -> None:
    """Refuse to certify a network this checkout did not compile.

    The custody drill's first refusal. The verifier had built preprod
    constants while the certificate would have said mainnet, and the only
    honest verdict there is a verdict on this CHECKOUT, not on the backup.
    It raises before any derivation runs rather than annotating a caveat
    onto the certificate: a caveat is read once, a certificate forever.
    """
    if built != claimed:
        raise SystemExit(
            f"refusing to certify {claimed}: this checkout builds {built}. "
            f"That is a verdict on this CHECKOUT, not on the backup.")
```

**Filter at the source, not after.** Redacting output you have already read
is theatre; the value is in the context by then. Put the filter in the
process that produces the value:

```python secret-filter
import json
import re
import sys

PUBLIC = {"NODE_ENV": r"development|production|test",
          "PORT": r"\d{1,5}",
          "NETWORK": r"mainnet|preprod|preview",
          "LOG_LEVEL": r"debug|info|warn|error",
          "PUBLISHER_ADDRESS": r"(addr|addr_test)1[0-9a-z]{20,}"}


def summarize(config: dict) -> str:
    """Every key name; a value only where the name is allowlisted and the
    value is the shape that name promises.

    Allowlist, never denylist: a denylist withholds the secrets somebody
    thought of and prints the one added last week. And it checks the value,
    because a name is not evidence — the same reason secret-guard.py takes
    a declaration and not a filename, and a rotation is exactly when
    PUBLISHER_ADDRESS most plausibly holds key material for one deploy.
    """
    lines = []
    for key in sorted(config):
        value = str(config[key])
        shape = PUBLIC.get(key)
        if shape and re.fullmatch(shape, value):
            lines.append(f"{key}={value}")
        elif shape:
            lines.append(f"{key}=<withheld, name and value shape disagree>")
        else:
            lines.append(f"{key}=<withheld, {len(value)} chars>")
    return "\n".join(lines)


def redact_stream(stream) -> str:
    """The dump crosses the same barrier the key does.

    `json.load` binds the whole document to a local inside the decoder, so
    a locals-capturing renderer prints every credential with nobody having
    printed anything. The message itself is clean, which is what makes this
    easy to miss — and a banner before the JSON is the ordinary failure.
    """
    try:
        return summarize(json.load(stream))
    except BaseException as e:
        raise RuntimeError(f"config unreadable: {type(e).__name__}") from None


if __name__ == "__main__":
    print(redact_stream(sys.stdin))
```

```sh
railway variables --json | python -m ops.redact
```

The command that dumps every secret still runs; the transcript gets the key
names, the shape of what exists, and no values — which answers the question
actually being asked.

## The hazard the language hands you

The sharp edge is not the file. It is that **an object holding a key will
print it.** `pycardano.PaymentSigningKey.__repr__` emits the raw private
key as CBOR JSON, and a dataclass holding one prints it inside its
generated repr. So an f-string leaks it, a log line leaks it, a `pytest`
assertion diff leaks it — and `--showlocals` leaks it with nobody printing
anything at all: a frame merely *held* the key, and something rendered it.

The mitigation is a barrier: consume the key object as a temporary in a
frame of its own, let only public strings cross the boundary, and re-raise
scrubbed with `from None` so the frames whose locals hold the secret leave
the chain. This is `ops/derive.py` in full — the module the safe shapes at
the top invoke.

```python secret-barrier
import sys


def publisher_address(key_path: str, passphrase: str | None = None) -> str:
    """Address for a signing key on disk. No key object crosses this line.

    The derivation gets its own frame so this one — the frame that raises —
    has nothing in its locals but a path. `from None` suppresses the
    chained frames, and with them the local holding the key. Scrubbing the
    message is not enough alone: the loader quotes the file's contents into
    its own error, so the original exception is never forwarded, only its
    type name.

    `BaseException`, not `Exception`, because the two things most likely to
    end a key derivation are neither — Ctrl-C on a slow KDF, and a
    cancelled publisher task. Either escaping leaves the key bound in a
    frame for the next renderer, and re-raising scrubbed loses nothing: the
    type name is all a caller needs to tell an abort from a failure.
    """
    try:
        return _derive_address(key_path, passphrase)
    except BaseException as e:
        raise RuntimeError(f"{key_path}: {type(e).__name__}") from None


def _derive_address(key_path: str, passphrase: str | None) -> str:
    key = PaymentSigningKey.load(key_path, passphrase)
    return str(Address(key.to_verification_key().hash(),
                       network=Network.MAINNET))


def main(argv: list[str]) -> None:
    """`python -m ops.derive PATH [--equals ADDRESS] [--passphrase-stdin]`.

    An entrypoint rather than `python -c`, because a one-liner that calls
    the loader prints the loader's exception, file contents and all, and
    has nowhere to put the barrier above. The passphrase arrives on stdin
    because an argument is in the transcript and the process table both.
    """
    path, flags = argv[0], argv[1:]
    secret = (sys.stdin.readline().rstrip("\n")
              if "--passphrase-stdin" in flags else None)
    address = publisher_address(path, secret)
    print(address == flags[flags.index("--equals") + 1]
          if "--equals" in flags else address)


if __name__ == "__main__":
    main(sys.argv[1:])
```

`tests/secret-handling-test.py` runs every shape above against a canary
file and fails if the canary reaches any output, each case carried by a
control that must leak. The scope of `from None` is worth knowing exactly:
it suppresses the chained frames from every renderer honoring
`__suppress_context__` — `traceback`, `pytest`, `--showlocals`. The
original exception object is still reachable in memory, so do not stash it,
re-raise it, or hand it to a reporter.

## If it already leaked

Prevention has a failure branch, and it carries the highest stakes of any
state in this skill.

- **Stop the derivation** rather than working around it and continuing. The
  next command compounds the exposure and buries the moment it happened.
- **Do not re-print, quote, or summarize it forward.** Compaction carries
  whatever it reads into the next context, and a transcript written to disk
  outlives the session that produced it.
- **Treat the credential as burned, and escalate rather than rotate.**
  Rotating the publisher key here costs a whole vault generation, so
  "assume compromise and rotate" is a priced decision, not a default.
  Record it with `scripts/decision.py` — rotation with its cost as one
  option, containment without rotation as the other, each with its
  strongest objection — and let the operator rule.

## When to refuse anyway

The technique makes *reading* safe. It does not make every action
permitted, and three refusals survive it:

- **Signing or submitting anything that moves funds**, unless the operator
  authorized that transaction — and **printing the signed transaction, the
  witness, or any replayable payload**, which delegates submission to every
  reader of the transcript. Naming the campaign is not naming the effect; a
  blanket yes carries nothing along with it.
- **Sending key material to any external service.** No pastebin, no
  third-party API, no "let me check this against an online tool", no
  committing it, no writing it anywhere the tree gets pushed from.
  Derivation is local or it is not derivation.
- **Any action the operator has not authorized.** Safe reading is not
  standing authority to act.

And never widen your own permissions to make a derivation work. If a deny
genuinely blocks a safe shape, say so and propose the exact edit with its
rationale — the operator owns `settings.json`, and a subagent's request is
never its own approval.

## Worked example: the custody drill

Before a mainnet genesis ceremony, an oracle publisher key had to be proved
restorable from backup. The drill derived addresses from each backup file
across ten derivation indices, in a freshly-isolated checkout, and compared
them to the address the live publisher signs from. Nothing but addresses
and a verdict ever left the process.

It refused twice, and both refusals were the product. First it refused to
certify at all, because the checkout it had built carried preprod constants
while the certificate would have claimed mainnet. Then it refused
`publisher_mnemonic.txt` — the obviously-named file — because across all
ten indices that file derived a **retired** credential. The live key was in
`aegis_vault_clean.txt`. A human reading the obvious file would have
certified custody of a dead key and walked into an irreversible ceremony.

It caught that only because it derived and compared rather than trusting a
filename — which is why a custody claim's Evidence row carries the derived
address and the verdict, never the key.
