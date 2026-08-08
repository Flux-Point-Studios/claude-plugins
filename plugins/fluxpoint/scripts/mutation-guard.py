#!/usr/bin/env python3
"""Mutation score: the judge of the judge.

Every other gate in this plugin asks whether the tests pass. None of them
asks whether the tests can fail. In loop-mode TDD the same agent writes the
code and the thing that grades it, and `proof-guard.py` only catches the
unambiguous vacuities — a body that is literally `True`, a predicate that
always agrees. A suite that executes every line and asserts nothing about
any of them is green, structurally clean, and worthless.

Mutation testing is the one measure that cannot be faked by executing code:
break the implementation on purpose, and count how many broken versions the
suite notices. A test that never fails kills no mutants.

  mutation-guard.py --measure [--accept --reason "..."]   run it; enforce the ratchet
  mutation-guard.py --check                               cheap gate: is there a fresh measurement
  mutation-guard.py --report                              what was measured, for humans

## Why the work is split this way

A mutation run takes minutes to hours — it compiles and tests the project
once per mutant — so it cannot be a per-stop gate. `--measure` is the
expensive half and is where the ratchet lives: it re-measures, and fails
when the score fell or the survivor count rose against what was recorded.
Run it off-session, on the wake/Routine layer. `--check` is the cheap half
that belongs in `harness.sh --full`: it re-runs nothing and only asks
whether a measurement exists and still describes this tree.

Both directions of the ratchet matter. Score is a ratio, and a ratio can be
held flat while coverage shrinks — delete a well-tested module and the
survivors that remain are a smaller share of a smaller whole. So the
absolute survivor count is ratcheted too: it may fall, never rise.

## Loud, not fatal, where it cannot know

A repo with no `.fluxpoint-mutation.json` is dormant and silent, like the
DoD gate with no harness. A repo that declares one but has never measured
is reported NOT MEASURED and still exits 0 — arming a ratchet is a
deliberate step, exactly as in `proof-guard.py`. A measurement that has gone
stale is named, with how stale, and files an inbox item so somebody
schedules the re-run; it fails the build only if the config asks for it.
That default is not timidity: a gate that goes red because an expensive job
has not been re-run yet is a gate people switch off, and it would take the
rest of the harness with it.

Coverage: `cargo-mutants` today. Other toolchains are declared unsupported
by name rather than silently skipped, because a mutation guard that quietly
measures nothing is worse than none at all.

Stated plainly: the cargo-mutants adapter was written from the tool's
documentation, not from a run of the tool itself — no Rust toolchain was
available where this was built. So the shape it expects may be wrong. What
is *not* left to chance is the failure mode: an `outcomes.json` this parser
does not understand yields no score, records nothing, and exits non-zero
with "nothing was measured", rather than scoring zero and quietly becoming
the floor everything afterwards is ratcheted against. That behavior is
pinned by a test. Verify against a real run before relying on the number.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

BASELINE = ".fluxpoint-proof-baseline.json"
CONFIG = ".fluxpoint-mutation.json"
CONFIG_FIELDS = {"version", "tool", "failWhenStale", "maxStaleCommits", "args"}
SUPPORTED = {"cargo-mutants"}
# Named so a repo using one of these is told it is not covered, rather than
# reading a silent exit 0 as a clean bill of health.
KNOWN_UNSUPPORTED = {
    "mutmut": "Python", "stryker": "JS/TS", "pitest": "Java", "aiken": "Aiken",
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True,
                           text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def load_config(root):
    """(config, findings). Missing file means dormant, not broken."""
    p = os.path.join(root, CONFIG)
    if not os.path.exists(p):
        return None, []
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return None, [f"{CONFIG} is not readable JSON ({e}) — the mutation "
                      f"ratchet is DISARMED"]
    if not isinstance(doc, dict):
        return None, [f"{CONFIG} must be an object"]
    f = [f"{CONFIG}: unknown field '{k}' — not part of the manifest"
         for k in sorted(set(doc) - CONFIG_FIELDS)]
    if doc.get("version") != 1:
        f.append(f"{CONFIG}: version must be 1")
    tool = doc.get("tool")
    if tool in KNOWN_UNSUPPORTED:
        f.append(f"{CONFIG}: '{tool}' ({KNOWN_UNSUPPORTED[tool]}) is a real "
                 f"mutation tool but this guard does not parse it yet — "
                 f"supported: {sorted(SUPPORTED)}")
    elif tool not in SUPPORTED:
        f.append(f"{CONFIG}: tool must be one of {sorted(SUPPORTED)}")
    if "args" in doc and not (isinstance(doc["args"], list)
                              and all(isinstance(x, str) for x in doc["args"])):
        f.append(f"{CONFIG}: args must be a list of strings")
    return (None if f else doc), f


def load_baseline(root):
    p = os.path.join(root, BASELINE)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"mutation-guard: {p} is not readable JSON: {e}")


def write_measurement(root, rec):
    """Record into the shared baseline, preserving every sibling section.

    proof-guard owns `counts` and spec-guard owns `spec` in this same file.
    Rewriting the document wholesale would disarm whichever ratchet did not
    write last, which is the kind of silent disarm this plugin exists to
    refuse.
    """
    doc = load_baseline(root)
    doc.setdefault("version", 1)
    doc["mutation"] = rec
    p = os.path.join(root, BASELINE)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return p


# ------------------------------------------------------------ cargo-mutants
def run_cargo_mutants(root, extra):
    """(measurement, findings). Runs the tool and reads its own JSON."""
    out_dir = os.path.join(root, "mutants.out")
    cmd = ["cargo", "mutants", "--output", root, *extra]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    except FileNotFoundError:
        return None, ["cargo not on PATH — install cargo-mutants "
                      "(`cargo install cargo-mutants`) or drop the config"]
    except Exception as e:  # noqa: BLE001
        return None, [f"cargo mutants could not be run: {e}"]

    p = os.path.join(out_dir, "outcomes.json")
    if not os.path.exists(p):
        tail = (proc.stderr or proc.stdout or "")[-600:]
        return None, [
            f"cargo mutants exited {proc.returncode} but wrote no "
            f"{os.path.relpath(p, root)} — nothing was measured. Tail:\n{tail}"]
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return None, [f"{p} is not readable JSON ({e})"]
    return parse_cargo_mutants(doc), []


def parse_cargo_mutants(doc):
    """Counts and survivors from cargo-mutants' outcomes.json.

    Only `caught` and `missed` are scored. `unviable` mutants did not
    compile and were never a test of anything; `timeout` and `failure` say
    the run could not decide. Folding those into the denominator would let a
    build that got slower look like a suite that got better.
    """
    outcomes = doc.get("outcomes")
    if not isinstance(outcomes, list):
        return None
    tally = {"caught": 0, "missed": 0, "unviable": 0, "timeout": 0, "other": 0}
    survivors = []
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        summary = str(o.get("summary") or o.get("outcome") or "").lower()
        key = ("caught" if "caught" in summary
               else "missed" if "missed" in summary
               else "unviable" if "unviable" in summary
               else "timeout" if "timeout" in summary
               else "other")
        tally[key] += 1
        if key == "missed" and len(survivors) < 50:
            scen = o.get("scenario")
            m = scen.get("Mutant") if isinstance(scen, dict) else None
            m = m if isinstance(m, dict) else {}
            survivors.append({
                "file": m.get("file") or o.get("file") or "?",
                "line": m.get("line") or o.get("line") or 0,
                "what": (m.get("function") or {}).get("function_name")
                        if isinstance(m.get("function"), dict)
                        else (m.get("replacement") or o.get("name") or "?"),
            })
    scored = tally["caught"] + tally["missed"]
    if scored == 0:
        return None
    return {
        "tool": "cargo-mutants",
        "score": round(tally["caught"] / scored, 4),
        "killed": tally["caught"],
        "survived": tally["missed"],
        "scored": scored,
        "unviable": tally["unviable"],
        "timeout": tally["timeout"],
        "survivors": survivors,
    }


# ------------------------------------------------------------------ commands
def measure(root, cfg, accept, reason, src=None):
    prior = load_baseline(root).get("mutation") or {}
    if src:
        # The run already happened — commonly in CI, where the mutation job
        # is its own long-running step. Recording and ratcheting its result
        # should not require running it a second time here.
        try:
            with open(src, encoding="utf-8") as fh:
                rec, findings = parse_cargo_mutants(json.load(fh)), []
        except (OSError, json.JSONDecodeError) as e:
            rec, findings = None, [f"{src} is not readable JSON ({e})"]
        if rec is None and not findings:
            findings = [f"{src} carries no scored mutants — nothing to record"]
    else:
        rec, findings = run_cargo_mutants(root, list(cfg.get("args") or []))
    for f in findings:
        print(f"mutation-guard: {f}", file=sys.stderr)
    if rec is None:
        if not findings:
            print("mutation-guard: the run produced no scored mutants — "
                  "nothing was measured", file=sys.stderr)
        return 1

    rec["headSha"] = git(root, "rev-parse", "HEAD")
    rec["when"] = now()
    rec["note"] = ("Mutation score. --measure fails when the score falls or the "
                   "survivor count rises against this record; --accept re-records "
                   "a weaker one, which is a reviewable diff.")

    if prior:
        fell = rec["score"] < prior.get("score", 0) - 1e-9
        rose = rec["survived"] > prior.get("survived", 10 ** 9)
        if (fell or rose) and not accept:
            print("\nmutation-guard: RED — the suite got weaker\n", file=sys.stderr)
            if fell:
                print(f"  score:     {prior.get('score')} -> {rec['score']}",
                      file=sys.stderr)
            if rose:
                print(f"  survivors: {prior.get('survived')} -> {rec['survived']} "
                      f"(a ratio can hold flat while coverage shrinks, so the "
                      f"absolute count is ratcheted too)", file=sys.stderr)
            for s in rec["survivors"][:8]:
                print(f"      {s['file']}:{s['line']}  {s['what']}", file=sys.stderr)
            print("\n  Every survivor is a change to your code that no test "
                  "noticed.\n  Kill them, or re-record deliberately:\n"
                  "    mutation-guard.py --measure --accept --reason \"...\"",
                  file=sys.stderr)
            return 1
        if accept and (fell or rose):
            if len((reason or "").strip()) < 20:
                print("mutation-guard: --accept needs a --reason of at least 20 "
                      "characters. Lowering this floor is the one move that "
                      "weakens the ratchet.", file=sys.stderr)
                return 1
            rec["acceptedWeaker"] = {"reason": reason.strip(),
                                     "from": {"score": prior.get("score"),
                                              "survived": prior.get("survived")}}

    p = write_measurement(root, rec)
    verdict = "stronger" if prior and rec["score"] > prior.get("score", 0) else "recorded"
    print(f"mutation-guard: {verdict} — score {rec['score']}, "
          f"{rec['killed']} killed, {rec['survived']} survived "
          f"({rec['unviable']} unviable, {rec['timeout']} timeout) -> {p}")
    if rec["survived"]:
        print("mutation-guard: surviving mutants (changes no test noticed):")
        for s in rec["survivors"][:10]:
            print(f"    {s['file']}:{s['line']}  {s['what']}")
    return 0


def staleness(root, rec):
    """(commits_behind, changed_files) since the measured commit."""
    sha = rec.get("headSha")
    if not sha:
        return None, None
    rng = f"{sha}..HEAD"
    behind = git(root, "rev-list", "--count", rng)
    names = git(root, "diff", "--name-only", rng)
    if not behind and not names:
        # An unknown sha (rebased away, shallow clone) cannot be compared.
        if not git(root, "cat-file", "-t", sha):
            return -1, None
    files = [n for n in names.splitlines() if n.strip()]
    try:
        return int(behind or 0), files
    except ValueError:
        return 0, files


def check(root):
    cfg, findings = load_config(root)
    for f in findings:
        print(f"mutation-guard: {f}", file=sys.stderr)
    if findings:
        return 1
    if cfg is None:
        print("mutation-guard: no .fluxpoint-mutation.json — dormant")
        return 0

    rec = load_baseline(root).get("mutation")
    if not rec:
        print(f"mutation-guard: {CONFIG} declares {cfg['tool']} but nothing has "
              f"been measured, so the ratchet is NOT armed. Run: "
              f"mutation-guard.py --measure", file=sys.stderr)
        return 0  # arming is a deliberate step; bootstrapping must not block

    behind, changed = staleness(root, rec)
    fresh = f"score {rec.get('score')}, {rec.get('survived')} survivor(s)"
    if behind == -1:
        print(f"mutation-guard: measured at a commit this repo no longer has "
              f"({rec.get('headSha', '')[:8]}) — {fresh}, but it cannot be "
              f"related to this tree. Re-measure.", file=sys.stderr)
        return 1 if cfg.get("failWhenStale") else 0

    limit = int(cfg.get("maxStaleCommits", 20))
    if behind and behind > limit:
        msg = (f"the measurement is {behind} commit(s) behind HEAD "
               f"(limit {limit}), touching {len(changed or [])} file(s) — "
               f"{fresh} describes a tree that has moved")
        print(f"mutation-guard: STALE — {msg}", file=sys.stderr)
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import inbox as _inbox
            _inbox.add(root, "mutation-stale", "mutation-guard", "",
                       f"{msg}. Re-run: mutation-guard.py --measure")
        except Exception as e:  # noqa: BLE001
            print(f"mutation-guard: could not file an inbox row: {e}",
                  file=sys.stderr)
        if cfg.get("failWhenStale"):
            return 1
        print("mutation-guard: not failing the build for staleness "
              "(failWhenStale is off) — an expensive job left un-run is a "
              "reason to schedule it, not to block a stop.")
        return 0

    print(f"mutation-guard: green — {fresh}, measured {behind or 0} commit(s) ago")
    return 0


def report(root):
    cfg, findings = load_config(root)
    for f in findings:
        print(f"mutation-guard: {f}", file=sys.stderr)
    rec = load_baseline(root).get("mutation")
    if not rec:
        print("mutation-guard: nothing measured yet")
        return 0
    behind, changed = staleness(root, rec)
    print(f"mutation-guard: {rec.get('tool')} score {rec.get('score')} "
          f"({rec.get('killed')} killed / {rec.get('scored')} scored)")
    print(f"  measured {rec.get('when')} at {str(rec.get('headSha'))[:8]}, "
          f"{behind if behind and behind > 0 else 0} commit(s) ago")
    print(f"  unviable {rec.get('unviable')}, timeout {rec.get('timeout')} "
          f"(neither is scored: a mutant that did not compile tested nothing)")
    if rec.get("acceptedWeaker"):
        aw = rec["acceptedWeaker"]
        print(f"  a weaker score was accepted deliberately: {aw.get('reason')}")
    for s in (rec.get("survivors") or [])[:20]:
        print(f"    survivor {s['file']}:{s['line']}  {s['what']}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--accept", action="store_true",
                    help="re-record a weaker score (needs --reason)")
    ap.add_argument("--reason", default="")
    ap.add_argument("--from", dest="src",
                    help="record an outcomes.json the tool already produced "
                         "(a CI job that ran it separately) instead of "
                         "running it here")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--measure", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()

    if a.check:
        return check(a.root)
    if a.report:
        return report(a.root)

    cfg, findings = load_config(a.root)
    for f in findings:
        print(f"mutation-guard: {f}", file=sys.stderr)
    if findings:
        return 1
    if cfg is None:
        print(f"mutation-guard: no {CONFIG} — nothing to measure. Declare the "
              f"tool first, so 'unmeasured' and 'not applicable' stay "
              f"different states.", file=sys.stderr)
        return 1
    return measure(a.root, cfg, a.accept, a.reason, a.src)


if __name__ == "__main__":
    sys.exit(main())
