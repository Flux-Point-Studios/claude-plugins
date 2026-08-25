#!/usr/bin/env python3
"""Relation gate: check artifact PAIRS, not artifact health.

The Stop gate has two evidence channels — a harness exit code and a regex
over added lines — and both measure one artifact in isolation. That misses
an entire defect class, because the expensive failures are *relationships*:

    An on-chain predicate is tightened. Its off-chain transaction builder
    is not. Every test passes, on both sides, because each artifact is
    individually correct — and the change rejects 100% of honest
    transactions.

No count of passing tests can see that. The suite is green precisely
because nothing in it evaluates the pair together.

So the pair is declared, in a repo-owned `.fluxpoint-pairs.json`, and
checked two ways:

  co-change  A commit that moves one side of a declared pair and not the
             other is reported. Cheap, catches the common case, and says
             nothing about whether the two sides *agree* — only that
             somebody changed one and forgot the other.
  parity     A command whose job is to build the off-chain artifact and
             evaluate the on-chain one against it, so the relation itself
             becomes an exit code. This is the real check; co-change is
             the smoke alarm.

An acknowledgement clears CO-CHANGE, never parity. `.fluxpoint-pair-acks.json`
holds `{pair, source_sha, why}`; `source_sha` addresses the CURRENT bytes of the
source files the alarm named, so editing them again makes the ack stale and the
alarm re-opens on its own. That is the difference between recording that someone
read the mirror and switching the alarm off.

Only the consuming repo can write the parity vectors — they are specific
to the chain, the contracts, and the builder. This supplies the slot.

  pair-guard.py --check              co-change over the diff, then parity
  pair-guard.py --check --against origin/main    diff a branch, for CI
  pair-guard.py --list               what is declared, and what is not

Dormant by design: no manifest means no declared relations, exit 0.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

MANIFEST = ".fluxpoint-pairs.json"
ACKS = ".fluxpoint-pair-acks.json"
ACK_FIELDS = {"pair", "source_sha", "why"}
ACK_WHY_FLOOR = 60
PAIR_FIELDS = {"id", "source", "mirror", "parity", "symmetric", "why"}


def glob_to_re(pat):
    """Translate a path glob to a regex, with `**` spanning directories.

    fnmatch is not usable here: it treats `*` as matching `/` too, so
    `contracts/*.ak` would match a file three directories down and a pair
    would silently cover more than it claims.
    """
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def load_manifest(root):
    """Parse and validate the manifest. A malformed pair is a hard error.

    Accepting a half-declared pair would mean a relation nobody checks
    while the manifest claims coverage — the same silent-inertness failure
    the IR's closed field registry exists to prevent.
    """
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise SystemExit(f"pair-guard: {MANIFEST} is not valid JSON: {e}")
    if not isinstance(data, list):
        raise SystemExit(f"pair-guard: {MANIFEST} must be a list of pairs")
    for i, p in enumerate(data):
        where = f"{MANIFEST}[{i}]"
        if not isinstance(p, dict):
            raise SystemExit(f"pair-guard: {where} must be an object")
        unknown = sorted(set(p) - PAIR_FIELDS)
        if unknown:
            raise SystemExit(
                f"pair-guard: {where}: unknown field(s) {unknown} — known: "
                f"{', '.join(sorted(PAIR_FIELDS))}")
        if not p.get("id"):
            raise SystemExit(f"pair-guard: {where}: id required")
        if not p.get("source") or not as_list(p.get("mirror")):
            raise SystemExit(
                f"pair-guard: {where} ('{p.get('id')}'): both source and mirror "
                f"are required — a pair with one side is not a relation")
    return data


def changed_files(root, against):
    """Paths this change touches, tracked edits plus new untracked files.

    Mirrors the Stop gate's rule rather than inventing a second one: the
    marker cannot see files written through the Bash tool, so dirtiness is
    re-derived from git.
    """
    def git(*a):
        r = subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)
        return [x for x in r.stdout.splitlines() if x] if r.returncode == 0 else []

    if against == "HEAD":
        files = git("diff", "HEAD", "--name-only", "--diff-filter=ACMR")
        files += git("ls-files", "--others", "--exclude-standard")
    else:
        files = git("diff", f"{against}...HEAD", "--name-only", "--diff-filter=ACMR")
        files += git("diff", "HEAD", "--name-only", "--diff-filter=ACMR")
    return sorted(set(files))


def matches(patterns, files):
    hit = []
    for pat in patterns:
        rx = glob_to_re(pat)
        hit += [f for f in files if rx.match(f)]
    return sorted(set(hit))



def source_sha(root, paths):
    """Content address over the source files a co-change alarm named.

    The whole value of an acknowledgement is that it CANNOT become a blanket
    exemption. Keying it on the bytes that tripped the alarm means the next edit
    to those same files produces a different address, the ack goes stale, and the
    alarm re-opens on its own -- the pattern a consuming repo already applies to
    its provenance census and hash surface, for the same reason.

    The path is hashed with its own length in front of it, so no arrangement of
    names and contents can collide with a different arrangement.
    """
    h = hashlib.sha256()
    for rel in sorted(paths):
        b = rel.encode("utf-8")
        h.update(str(len(b)).encode("ascii") + b":" + b)
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                body = fh.read()
        except FileNotFoundError:
            body = b"<deleted>"
        h.update(str(len(body)).encode("ascii") + b":")
        h.update(body)
    return h.hexdigest()


def load_acks(root, pair_ids):
    """Parse and validate the ack file. A malformed ack is a hard error.

    An ack silences a check, so a half-declared one is strictly worse than none:
    it is a gate somebody believes is armed. Naming a pair that does not exist is
    refused for the same reason -- an ack matching nothing reads as coverage of
    something.
    """
    path = os.path.join(root, ACKS)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise SystemExit("pair-guard: %s is not valid JSON: %s" % (ACKS, e))
    if not isinstance(data, list):
        raise SystemExit("pair-guard: %s must be a list of acknowledgements" % ACKS)
    for i, a in enumerate(data):
        where = "%s[%d]" % (ACKS, i)
        if not isinstance(a, dict):
            raise SystemExit("pair-guard: %s must be an object" % where)
        unknown = sorted(set(a) - ACK_FIELDS)
        if unknown:
            raise SystemExit("pair-guard: %s has unknown field(s): %s" % (where, unknown))
        for f in ("pair", "source_sha", "why"):
            if not isinstance(a.get(f), str) or not a[f].strip():
                raise SystemExit("pair-guard: %s needs a non-empty %r" % (where, f))
        if a["pair"] not in pair_ids:
            raise SystemExit(
                "pair-guard: %s acknowledges %r, which is not a declared pair. "
                "An ack that matches nothing reads as coverage." % (where, a["pair"]))
        if len(a["why"].strip()) < ACK_WHY_FLOOR:
            raise SystemExit(
                "pair-guard: %s needs a real reason (%d+ chars). The ack records "
                "that a human READ the mirror and found it unaffected; a reason too "
                "short to say why is not that record." % (where, ACK_WHY_FLOOR))
    return data


def check(root, against, run_parity, report_only):
    pairs = load_manifest(root)
    if pairs is None:
        print(f"pair-guard: no {MANIFEST} — no declared relations to check")
        return 0
    if not pairs:
        print(f"pair-guard: {MANIFEST} declares no pairs")
        return 0

    acks = load_acks(root, {p["id"] for p in pairs})
    files = changed_files(root, against)
    failures, checked = [], 0
    honoured = set()

    for p in pairs:
        src = matches(as_list(p["source"]), files)
        mir = matches(as_list(p["mirror"]), files)
        if src and not mir:
            # An ack discharges CO-CHANGE and nothing else. It is the record that
            # a human opened the mirror and found it genuinely unaffected -- the
            # one answer this alarm asks for and had no slot for. It is keyed on
            # the CURRENT bytes of the source files named here, so the next edit
            # to them re-opens the alarm without anybody remembering to.
            now = source_sha(root, src)
            ack = next((a for a in acks
                        if a["pair"] == p["id"] and a["source_sha"] == now), None)
            if ack:
                honoured.add(id(ack))
                print(f"  {p['id']}: co-change acknowledged — {ack['why'].strip()}")
            else:
                stale = [a for a in acks if a["pair"] == p["id"]]
                failures.append(
                    f"{p['id']}: {', '.join(src[:3])} changed, but nothing matching "
                    f"{as_list(p['mirror'])} did"
                    + (f" — {p['why']}" if p.get("why") else "")
                    + (f"\n      An ack exists for this pair but addresses different "
                       f"bytes (source is now {now[:12]}...): the source was edited "
                       f"again after it was written, so it no longer says anything "
                       f"about what is there. Re-read the mirror and re-ack."
                       if stale else
                       f"\n      If the mirror genuinely needs no change, record that "
                       f"in {ACKS}: {{\"pair\": \"{p['id']}\", \"source_sha\": "
                       f"\"{now}\", \"why\": \"...\"}}"))
        elif mir and not src and p.get("symmetric"):
            failures.append(
                f"{p['id']}: {', '.join(mir[:3])} changed, but nothing matching "
                f"'{p['source']}' did"
                + (f" — {p['why']}" if p.get("why") else ""))
        if src or mir:
            checked += 1

    if files:
        print(f"pair-guard: {len(pairs)} declared pair(s), {checked} touched by "
              f"this change")
    else:
        print(f"pair-guard: {len(pairs)} declared pair(s), no changes to compare")

    # Parity is the real check and runs regardless of what moved: two sides
    # can disagree without either being edited today, and --full is meant to
    # be everything the Definition of Done requires.
    if run_parity:
        for p in pairs:
            cmd = p.get("parity")
            if not cmd:
                print(f"  {p['id']}: co-change only — no parity command declared")
                continue
            r = subprocess.run(cmd, shell=True, cwd=root,
                               capture_output=True, text=True)
            if r.returncode == 0:
                print(f"  {p['id']}: parity ok")
            else:
                tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
                failures.append(
                    f"{p['id']}: parity command failed (exit {r.returncode}): {cmd}"
                    + ("\n      " + "\n      ".join(tail) if tail else ""))

    # An ack that discharged nothing is not harmless: it reads, to the next
    # person, as a relation somebody vouched for. Say it is spent.
    for a in acks:
        if id(a) not in honoured:
            print(f"  {a['pair']}: ack on file is unused — either the mirror moved "
                  f"with the source this time, or the source changed since. Drop it.")

    if failures and not report_only:
        print("\npair-guard: RED — a declared relation is broken or unverified\n",
              file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        print("\n  Both artifacts can pass their own tests while the pair is "
              "wrong.\n", file=sys.stderr)
        return 1
    if not failures:
        print("pair-guard: green")
    return 0


def listing(root):
    pairs = load_manifest(root)
    if pairs is None:
        print(f"pair-guard: no {MANIFEST}")
        return 0
    for p in pairs:
        kind = "co-change + parity" if p.get("parity") else "co-change only"
        print(f"  {p['id']:<24} {kind}")
        print(f"      source: {p['source']}")
        print(f"      mirror: {', '.join(as_list(p['mirror']))}")
        if not p.get("parity"):
            # Named, because a co-change rule proves only that somebody
            # remembered to touch both files, never that they agree.
            print("      no parity command — nothing evaluates the two together")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--against", default="HEAD",
                    help="ref to diff against; HEAD means the working tree")
    ap.add_argument("--no-parity", action="store_true",
                    help="co-change only, for a fast pre-commit pass")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--report", action="store_true")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        return listing(a.root)
    return check(a.root, a.against, not a.no_parity, a.report)


if __name__ == "__main__":
    sys.exit(main())
