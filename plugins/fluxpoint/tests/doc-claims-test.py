#!/usr/bin/env python3
"""The docs may not promise a guarantee the code does not provide.

Three claims went stale in the direction that matters -- each described a
property the reader would rely on, and each was false against the shipped
code. A doc that overstates a guarantee is worse than one that says nothing,
because the reader stops looking.

Pinned here so they cannot drift back:
  - the irreversible ledger is gitignored, so it cannot be described as
    committed and cannot promise once-only across clones
  - attestation records passes and (measured) never reds, so the log's own
    docstring must count that among its limits
  - record-run must distinguish a node claiming green with no row from one
    claiming red with no row, because only the first is suspicious
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PLUGIN))
passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name:<54} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


def read(*parts):
    with open(os.path.join(PLUGIN, *parts), encoding="utf-8") as fh:
        return fh.read()


SKILL = read("skills", "graph-engineering", "SKILL.md")
ATTEST = read("scripts", "attest.py")
LEDGER = read("scripts", "ledger.py")
INIT = read("commands", "init.md")

# ==================== the ledger is local, and says so ==================
m = re.search(r'LEDGER_PATH\s*=\s*os\.path\.join\((.*?)\)', LEDGER)
ledger_rel = "/".join(x.strip().strip('"\'') for x in m.group(1).split(",")) if m else ""
report("the ledger lives under .claude/fluxpoint",
       ledger_rel.startswith(".claude/fluxpoint"), ledger_rel or "NOT FOUND")

gitignore = ""
gi = os.path.join(REPO, ".gitignore")
if os.path.exists(gi):
    gitignore = open(gi, encoding="utf-8").read()
ignored = ".claude/fluxpoint/" in gitignore
report("and that directory is gitignored", ignored,
       "gitignored" if ignored else "TRACKED")

# The claim that must not come back while the path above is ignored.
SKILL_FLAT = re.sub(r"\s+", " ", SKILL)
report("the skill no longer calls the ledger committed",
       "committed ledger" not in SKILL_FLAT,
       "clean" if "committed ledger" not in SKILL_FLAT else "STILL CLAIMED")
_bound = "not committed" in SKILL_FLAT and "fresh clone" in SKILL_FLAT
report("and states the once-only bound explicitly", _bound,
       "stated" if _bound else "NOT STATED")

# Executed: a fresh tree reads as a first run, which is the whole point.
with tempfile.TemporaryDirectory() as d:
    r = subprocess.run([sys.executable, os.path.join(PLUGIN, "scripts", "ledger.py"),
                        "--root", d, "--load", "--campaign", "x"],
                       capture_output=True, text=True)
    empty = r.returncode == 0 and r.stdout.strip() in ("{}", "")
report("a fresh clone's ledger reads as a first run", empty,
       "empty, exit 0" if empty else f"rc={r.returncode} {r.stdout[:20]}")

# ==================== attestation states its asymmetry ==================
report("attest.py counts the failure-delivery limit",
       "Three limits" in ATTEST, "three limits")
# Collapse wrapped prose before matching: these are paragraphs, and a claim
# that happens to fall across a line break is still the claim.
FLAT = re.sub(r"\s+", " ", ATTEST)
ok = "carries no information about whether the gate passed" in FLAT
report("and says UNATTESTED carries no information about a red gate",
       ok, "stated" if ok else "NOT STATED")

# ==================== record-run splits the two absences ================
RR = read("scripts", "record-run.py")
report("record-run distinguishes a claimed pass with no row",
       "claimedExit" in RR and "silent_green" in RR, "split")
report("and records it in the durable tally",
       "unattested_claiming_pass" in RR, "recorded")

# ==================== isolation hazards are written down ================
for label, needle in (("the worktree base is not chosen here", "You do not choose the base"),
                      ("measuring nodes need isolation", "measurement*"),
                      ("a dirtied shared tree invalidates a gate", "no longer exists"),
                      ("the harness must be proven in a worktree", "tracked files only")):
    needle = needle.rstrip("*")
    report(label, needle in SKILL_FLAT,
           "documented" if needle in SKILL_FLAT else "MISSING")

report("init.md tells you to prove the harness in a worktree",
       "worktree add --detach" in read("commands", "init.md"), "step present")

# ===== and the snippet it tells you to run reports what it found ========
# EXECUTED, not grepped. The block ended `...harness.sh --full); git worktree
# remove --force ...` — a `;` before cleanup, so the whole thing exits with
# the REMOVE's status and a red harness reported success. `/fluxpoint:init`
# is a command an agent runs, and the exit code is the machine-readable
# answer, so following the documented step gave a false green.
_blocks = re.findall(r"```[a-z]*\n(.*?)```", INIT, re.S)
_probe = next((b for b in _blocks if "worktree add --detach" in b), None)
report("the worktree probe snippet is extractable", bool(_probe),
       "found" if _probe else "NOT FOUND")


def _bash():
    """A bash that RUNS, resolved by an explicit path rather than by name.

    subprocess resolves a bare "bash" through CreateProcess, which searches
    System32 first and finds the WSL launcher — present on stock Windows with
    no distribution installed. It exits non-zero for every argument, which made
    the case below PASS while executing nothing: the exact shape this file
    exists to catch, in the test doing the catching.
    """
    for c in (os.environ.get("SHELL"), shutil.which("bash"),
              r"C:\Program Files\Git\bin\bash.exe", "/bin/bash"):
        if not c:
            continue
        try:
            r = subprocess.run([c, "-c", "echo FPLOK"], capture_output=True,
                               text=True, timeout=60)
        except OSError:
            continue
        if r.returncode == 0 and "FPLOK" in r.stdout:
            return c
    return None


_sh = _bash()
report("a working bash is resolved for the executed case", bool(_sh),
       os.path.basename(_sh) if _sh else "NONE — case would prove nothing")
if _probe and _sh:
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r")
        os.makedirs(os.path.join(repo, "scripts"))
        with open(os.path.join(repo, "scripts", "harness.sh"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("#!/usr/bin/env bash\necho 'harness: RED'\nexit 1\n")
        for cmd in (["git", "init", "-q", "."], ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                    ["git", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=repo, capture_output=True)
        r = subprocess.run([_sh, "-c", _probe], cwd=repo,
                           capture_output=True, text=True)
        report("a failing harness in the worktree is not reported as green",
               r.returncode != 0, f"rc={r.returncode}"
               + ("" if r.returncode else " — SWALLOWED BY CLEANUP"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
