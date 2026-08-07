#!/usr/bin/env python3
"""Guard ratchet: a safety guard must be provable, and proven guards may only go up.

The companion to proof-guard.py, which counts the ways a proof can be waved through.
This counts the opposite thing: guards that stop money moving wrongly, and whether
each one is actually held down by a test.

WHY. Three guards failed the same way inside one week, on the same product:

  - a validator's fee bound had never been evaluated by a ledger, because every
    on-chain proof took the client-escape branch and short-circuited before reaching it
  - a tool deciding what a client is asked to WITNESS had a suite that went red the day
    the thing it guards changed shape, and nothing ran it for a fortnight
  - a co-signer's deployed-key pin had NO test in any commit, so a refactor deleted it
    along with the module it sat in and 95 tests still passed

None of these was a bug in the guard. Each was a guard nobody could see was gone. A
guard whose only proof lives in the file being refactored dies with it in silence, and
a green suite is what you get either way.

WHAT THIS DOES THAT A CHECKLIST DOES NOT. Registering a guard costs nothing and rots
quietly, so --verify does the thing by hand nobody keeps doing: it DISABLES each guard,
runs the one test that names it, and requires that test to FAIL. A guard whose proof
still passes with the guard disabled is decoration, and this says so. That is the whole
mechanism; everything else is bookkeeping around it.

  guard-guard.py --scan       list registered guards and their proofs
  guard-guard.py --check      fast: every guard and proof still present, count >= floor
  guard-guard.py --verify     slow: mutate each guard, require its proof to redden
  guard-guard.py --baseline   record the current count as the floor

--check belongs in every CI run; it is a grep and costs nothing. --verify runs the test
suite once per guard, so it belongs wherever the full suite already runs.

MANIFEST (.fluxpoint-guards.json), committed beside the code:

  {"guards": [
    {"id": "cosigner-deployed-key-pin",
     "protects": "serving a key that is not the pinned one silently produces channel "
                 "addresses no client can reconstruct",
     "guard":  {"file": "server.py", "contains": "if self.vkh != pinned_vkh:"},
     "proof":  {"file": "test_signer_pin.py",
                "test": "test_a_key_that_does_not_match_its_pin_is_fatal"},
     "mutation": {"find": "if self.vkh != pinned_vkh:", "replace": "if False:"},
     "run": "python3 -m pytest -q {file}::{test}"}
  ]}

`protects` is prose and is never parsed. It is there because the next person to read a
failure needs to know what the guard was FOR, and a rule nobody understands gets deleted.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MANIFEST = ".fluxpoint-guards.json"
BASELINE = ".fluxpoint-guards-baseline.json"
DEFAULT_RUN = "python3 -m pytest -q {file}::{test}"


def _die(msg):
    print(f"guard-guard: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_manifest(root):
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        _die(f"no {MANIFEST} at {root}. Register the guards this repo relies on, or "
             f"state in the PR that it has none worth naming.")
    try:
        data = json.load(open(path))
    except json.JSONDecodeError as exc:
        _die(f"{MANIFEST} is not valid JSON: {exc}")
    guards = data.get("guards")
    if not isinstance(guards, list):
        _die(f"{MANIFEST} must contain a 'guards' list")
    seen = set()
    for g in guards:
        for field in ("id", "protects", "guard", "proof", "mutation"):
            if field not in g:
                _die(f"guard {g.get('id', '<unnamed>')!r} is missing {field!r}")
        if g["id"] in seen:
            _die(f"duplicate guard id {g['id']!r}")
        seen.add(g["id"])
    return guards


def _read(root, rel):
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def check(root, guards):
    """Structural: the guard is still in the code and the proof still names it."""
    problems = []
    for g in guards:
        gf, pf = g["guard"]["file"], g["proof"]["file"]
        gsrc, psrc = _read(root, gf), _read(root, pf)

        if gsrc is None:
            problems.append(f"{g['id']}: guard file {gf} is gone")
        elif g["guard"]["contains"] not in gsrc:
            problems.append(
                f"{g['id']}: {gf} no longer contains the guard\n"
                f"      looked for: {g['guard']['contains']}\n"
                f"      it protects: {g['protects']}")

        if psrc is None:
            problems.append(f"{g['id']}: proof file {pf} is gone — the guard is now unwatched")
        elif not re.search(rf"\b{re.escape(g['proof']['test'])}\b", psrc):
            problems.append(
                f"{g['id']}: {pf} no longer defines {g['proof']['test']}")

        if gf == pf:
            problems.append(
                f"{g['id']}: the proof lives in the same file as the guard ({gf}). "
                f"A refactor that deletes one deletes the other — that is the failure "
                f"this ratchet exists for. Move the proof to its own file.")
    return problems


def verify_one(root, g, quiet=False):
    """Disable the guard, run its proof, require the proof to fail. Always restore."""
    rel = g["guard"]["file"]
    path = os.path.join(root, rel)
    original = _read(root, rel)
    if original is None:
        return False, f"guard file {rel} is gone"

    find, replace = g["mutation"]["find"], g["mutation"]["replace"]
    if find not in original:
        return False, f"mutation target not present in {rel}: {find!r}"
    mutated = original.replace(find, replace, 1)
    if mutated == original:
        return False, f"mutation changed nothing in {rel}"

    cmd = g.get("run", DEFAULT_RUN).format(file=g["proof"]["file"], test=g["proof"]["test"])
    backup = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    backup.write(original)
    backup.close()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        proc = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=900)
        if proc.returncode == 0:
            tail = (proc.stdout or proc.stderr).strip().splitlines()[-3:]
            return False, ("the proof PASSED with the guard disabled — it proves nothing.\n"
                           f"      command: {cmd}\n"
                           + "\n".join(f"      {line}" for line in tail))
        if not quiet:
            print(f"    disabled -> proof failed (rc={proc.returncode}) — the guard is real")
        return True, None
    except subprocess.TimeoutExpired:
        return False, f"the proof did not finish in 900s: {cmd}"
    finally:
        # Restore unconditionally: an interrupted run must never leave a guard disabled.
        shutil.copyfile(backup.name, path)
        os.unlink(backup.name)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scan", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--baseline", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    guards = load_manifest(root)

    if args.scan:
        print(f"{len(guards)} registered guard(s) in {root}:\n")
        for g in guards:
            print(f"  {g['id']}")
            print(f"      protects: {g['protects']}")
            print(f"      guard:    {g['guard']['file']}")
            print(f"      proof:    {g['proof']['file']}::{g['proof']['test']}\n")
        return 0

    if args.baseline:
        with open(os.path.join(root, BASELINE), "w", encoding="utf-8") as fh:
            json.dump({"count": len(guards), "ids": sorted(g["id"] for g in guards)}, fh, indent=2)
            fh.write("\n")
        print(f"recorded {len(guards)} guard(s) to {BASELINE}")
        return 0

    problems = check(root, guards)

    # The floor. Guards may be added freely; removing one has to be a diff someone signs.
    base_path = os.path.join(root, BASELINE)
    if os.path.exists(base_path):
        base = json.load(open(base_path))
        missing = sorted(set(base.get("ids", [])) - {g["id"] for g in guards})
        if missing:
            problems.append(
                "guards were removed from the manifest: " + ", ".join(missing) +
                "\n      Adding a guard is free. Removing one means something that used to be "
                "\n      proven no longer is — re-record with --baseline and say why.")

    if args.verify and not problems:
        print(f"verifying {len(guards)} guard(s) by disabling each one:\n")
        for g in guards:
            print(f"  {g['id']}")
            ok, why = verify_one(root, g)
            if not ok:
                problems.append(f"{g['id']}: {why}")

    if problems:
        print("\nguard-guard: FAILED\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    print(f"\nguard-guard: {len(guards)} guard(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
