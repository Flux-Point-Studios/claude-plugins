#!/usr/bin/env python3
"""Seam ratchet: a mock of code you own is an unverified contract.

`vi.mock('@/services/x')` does not test anything about `@/services/x`. It
asserts what the author BELIEVED that module does, and nothing ever checks
the belief. When the belief is wrong the suite stays green — not because
the code works, but because the test never reached it.

That is not a hypothetical. The campaign that produced this script shipped
eight defects of one shape into a feature with a green, mutation-verified
suite: the unit was correct and the thing that reaches it was not. Four
were invisible until the ledger, the compiler or an adversarial reader
refused. The measured cause was a suite that mocked its own modules three
to one over real boundaries.

Mutation testing does not catch this. A mutant only reddens a test whose
input reaches the mutated line, so a suite that mocks the caller reports
the same green either way.

So the count is recorded in a committed baseline and this script fails when
it rises. Adding a mock stops being a reflex and becomes a diff someone has
to justify — and the honest justification is usually a wire test instead.

  seam-guard.py --scan       report current counts and where they are
  seam-guard.py --baseline   record counts to .fluxpoint-proof-baseline.json
  seam-guard.py --check      fail if any category rose above the baseline

Two categories, because they fail differently:

  first_party   mocking a module in this repo. The contract is one you own
                and could have tested for real; the mock asserts a shape
                you are free to change without anything noticing.

  third_party   mocking a library. Worse in one specific way: you are
                asserting a shape someone ELSE controls, from memory. Every
                one of the eight defects above lived here — the CBOR tag
                shape, the ledger's escaping, the error class a wallet
                throws. A library's real output is a capture, never a
                recollection.

Dormant by design: a repo with no test files exits 0 in silence, exactly
like the DoD gate in a repo with no harness.

Deliberately NOT counted, because each is a real boundary rather than a
contract you could have verified:

  * `stubGlobal('fetch')`, `window.*`, `globalThis.*` — the process edge.
  * a mock inside a file that also imports the real module under test,
    which is the shape of a partial mock via importActual.
"""

import argparse
import json
import os
import re
import subprocess
import sys

BASELINE = ".fluxpoint-proof-baseline.json"
SECTION = "seams"

TEST_SUFFIXES = (".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".test.mjs",
                 ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", ".spec.mjs")

# vi.mock('x') / jest.mock("x") / vi.doMock(`x`) — the specifier is what matters.
MOCK = re.compile(r"""\b(?:vi|jest)\s*\.\s*(?:do)?[Mm]ock\s*\(\s*(['"`])([^'"`]+)\1""")

# importActual means the real module is still in play; that is a partial mock,
# not a wall, and it does not hide the contract.
ACTUAL = re.compile(r"\bimportActual\b|\brequireActual\b")


def tracked_files(root):
    """Test files git knows about. Falls back to a walk outside a repo."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             capture_output=True, text=True, check=True).stdout
        names = [n for n in out.split("\0") if n]
    except Exception:
        names = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("node_modules", ".git", "dist", "build", "out", "coverage")]
            for f in filenames:
                names.append(os.path.relpath(os.path.join(dirpath, f), root))
    return [n for n in names if n.endswith(TEST_SUFFIXES)]


def is_first_party(spec):
    """A specifier that resolves inside this repo rather than to a package."""
    return spec.startswith((".", "/", "@/", "~/", "#"))


def scan(root):
    counts = {"first_party": 0, "third_party": 0}
    where = {"first_party": [], "third_party": []}
    for rel in tracked_files(root):
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        if ACTUAL.search(text):
            # A partial mock keeps the real module in the graph; the contract is
            # still exercised, so walling it off is not what happened here.
            continue
        for m in MOCK.finditer(text):
            spec = m.group(2)
            key = "first_party" if is_first_party(spec) else "third_party"
            counts[key] += 1
            line = text.count("\n", 0, m.start()) + 1
            where[key].append("%s:%d  %s" % (rel, line, spec))
    return counts, where


def applicable(root):
    """A repo with no test files at all has nothing to say here."""
    return bool(tracked_files(root))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    bpath = os.path.join(root, BASELINE)

    if not applicable(root):
        if args.scan:
            print("seam-guard: no test files; dormant")
        return 0

    counts, where = scan(root)

    if args.scan:
        for key in ("first_party", "third_party"):
            print("seam-guard: %s = %d" % (key, counts[key]))
            for line in where[key]:
                print("    " + line)
        return 0

    doc = {}
    if os.path.exists(bpath):
        try:
            with open(bpath, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:
            doc = {}

    if args.baseline:
        doc[SECTION] = counts
        with open(bpath, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("seam-guard: baseline armed at first_party=%d third_party=%d"
              % (counts["first_party"], counts["third_party"]))
        return 0

    base = doc.get(SECTION)
    if base is None:
        print("seam-guard: no baseline recorded; run --baseline to arm", file=sys.stderr)
        return 0

    risen = [(k, base.get(k, 0), counts[k]) for k in counts if counts[k] > base.get(k, 0)]
    if not risen:
        print("seam-guard: OK — first_party=%d third_party=%d, at or below baseline"
              % (counts["first_party"], counts["third_party"]))
        return 0

    for key, was, now in risen:
        print("seam-guard: FAIL — %s mocks rose %d -> %d" % (key, was, now), file=sys.stderr)
        for line in where[key]:
            print("    " + line, file=sys.stderr)
    print("", file=sys.stderr)
    print("A mock of a module you own asserts a contract nothing verifies. If the new", file=sys.stderr)
    print("mock is standing in for something the test could drive for real, drive it:", file=sys.stderr)
    print("stub only the process boundary (fetch, the wallet, the clock) and let the", file=sys.stderr)
    print("modules between be themselves. If the mock is genuinely right, re-arm with", file=sys.stderr)
    print("--baseline and say why in the commit.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
