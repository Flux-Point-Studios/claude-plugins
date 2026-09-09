#!/usr/bin/env python3
"""Counterexample ledger: a prover's failing input outlives the run that found it.

The most valuable thing a prover ever produces is the shrunk input that
breaks the property. It is minimal, concrete, and reproducible — and it
lives in a log file the next command overwrites. So the same bug is
rediscovered, re-shrunk, and re-explained from scratch, and a fix ships
with no permanent evidence that the case it fixed is still handled.

This records it, and makes the fix permanent:

  cex.py --ingest --tool T --from FILE --exit N   record failures from prover output
  cex.py --pin <cexId>                     write the evidence and a draft
  cex.py --pin <cexId> --file F --test-name T   record the regression test
  cex.py --check                           a pinned counterexample still bites
  cex.py --retire <cexId> --reason "..."   release the ratchet, with a diff
  cex.py --list                            what is recorded, for humans

The store is `.fluxpoint-cex.jsonl` at the repo root, next to
`.fluxpoint-proof-baseline.json` and `.fluxpoint-pairs.json` and for the
same reason: it is a ratchet, so it has to be committed. Everything under
`.claude/fluxpoint/` is gitignored by this plugin's own installer, and a
ratchet nobody else can see is a ratchet that only fails on the machine
that found the bug.

## Provers

One ledger, one pinning discipline, one parser per prover. `TOOLS` is a
registry: each entry knows how to read that prover's output into rows and
how to read a regression test written in that prover's language. The
ledger, the containment rule, the refusal to mint a counterexample from a
green test, and the INGEST-FAILED inbox row are shared, and a second parser
must not relax any of them.

### aiken

There is no `--json`. Aiken picks its output listener at runtime: when
stdout is not a TTY it emits structured JSON and sends every diagnostic to
stderr. A plain redirect therefore yields the machine form for free, and
nothing here parses a terminal box. The published `--show-json-schema` is
wrong in two ways that break a schema-driven parser — it declares a `kind`
discriminator that is never emitted, and names the array `test` where the
real key is `tests` — so kinds are told apart by which fields are present.

### dafny

`dafny verify` prints one line per failed obligation —
`file.dfy(LINE,COL): Error: <what could not be proved>` — and, when run
with `--extract-counterexample` (legacy `/extractCounterexample`), a block
headed `Counterexample for first failing assertion:` made of state blocks:
a location line `file.dfy(LINE,COL): initial state:` followed by indented
`name : type = value` lines, one state per program point. Dafny 4 renders
the same model as `assume name == value && ...;` statements meant to be
pasted into the code. The parser reads both spellings and keeps the
`initial state` block, which is the method's inputs — the shrunk-input
equivalent. Dafny extracts a model for the FIRST failing assertion only;
later failures are recorded without one. A `Counterexample` heading with no
assignment under it is parser drift and files INGEST-FAILED rather than
reading as a run with no failures. Resolution and type errors also print
as `Error:` lines; only messages that name a verification failure ("could
not be proved", "might not hold", …) become rows.

## What a pin has to survive

`--pin` cannot write the regression test itself: the recorded value does
not carry the test's parameter type or the predicate it broke, so a
generated test would be a guess, and a wrong guess is a non-compiling file
dropped into a source tree that was merely red. The agent writes the test;
this tool refuses to record it unless the recorded value is physically in
it. Specifically, a pin is rejected when:

  * the file is untracked, outside a directory the prover compiles (Aiken:
    `lib/`, `validators/`, `env/`; Dafny: any tracked `.dfy`), or under
    `.claude/` (where this tool writes its own draft — a tool must not mint
    the artifact that satisfies its own check),
  * the named test inverts or disables the oracle: an Aiken test annotated
    `fail`, which passes *because* the predicate is broken and goes red the
    day someone fixes it; a Dafny declaration carrying `{:verify false}` or
    `{:axiom}`, or a body containing `assume`, which makes anything verify,
  * the recorded literals are absent from that test's own body, in order,
    on token boundaries — not merely somewhere in the file, where a
    counterexample of `0` or `True` would match by accident,
  * or the body is hollow: a bare boolean, a self-comparison, a value bound
    and then ignored (`let v = <value> True`), or an `assert true`.

Comments and string literals are stripped before any of that, or pasting
the value into a comment would satisfy it.

## What this does NOT claim

A pinned test is recorded as *containing* the counterexample. It is not
re-run against the un-fixed code to prove it would have caught it. That is
the honest gap, it is why `signature` is in the schema already, and it is
the next increment — until then a pin means "the evidence is in the tree",
not "the regression bites".
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

CEX = ".fluxpoint-cex.jsonl"
EVIDENCE = ".fluxpoint-cex"
SCRATCH = os.path.join(".claude", "fluxpoint", "cex")
# `fixed` and `superseded` are declared so the schema never needs migrating
# and unreachable on purpose: the only honest way to set `fixed` is to re-run
# the pinned test against the un-fixed code, which this slice does not do.
# A status an agent could set by assertion is a self-report where an exit
# code belongs.
STATUSES = {"open", "pinned", "retired", "fixed", "superseded"}
COMPILE_ROOTS = ("lib/", "validators/", "env/")

AK_COMMENT = re.compile(r"//[^\n]*")
AK_STRING = re.compile(r'@"(?:[^"\\]|\\.)*"')
TEST_HEAD = re.compile(r"(?:^|\n)[ \t]*test[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
# Ordered literal leaves of a reified Aiken value. Bytearrays and strings
# first so their contents are not re-tokenized as integers.
LEAF = re.compile(
    r'(#"[0-9a-fA-F]*")'
    r'|(@"(?:[^"\\]|\\.)*")'
    r"|(-?\d+)"
    r"|([A-Z][A-Za-z0-9_]*)"
)
VACUOUS = re.compile(r"^(True|False|(?P<x>[A-Za-z0-9_.]+)\s*==\s*(?P=x))$")
TAIL_OPS = {"==", "!=", "&&", "||", ">", "<", ">=", "<=", "?", "|>"}

# ---- dafny ------------------------------------------------------------------
DFY_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
DFY_STRING = re.compile(r'@"(?:[^"]|"")*"|"(?:[^"\\\n]|\\.)*"')
DFY_ERROR = re.compile(
    r"^(?P<file>[^\s(]+\.dfy)\((?P<line>\d+),(?P<col>\d+)\):\s*Error:\s*(?P<msg>.+?)\s*$",
    re.M)
# Only these are counterexamples. A resolution error ("unresolved
# identifier") prints in the same shape and records nothing about the code's
# behaviour, so it must not be minted as a failing input.
DFY_VERIFICATION = re.compile(
    r"could not be proved|might not|cannot prove|could not prove|"
    r"is not proved|violat|does not hold|not maintained|not established|"
    r"nontermination|decreases", re.I)
DFY_SUMMARY = re.compile(r"verifier finished with \d+ verified, (?P<n>\d+) error", re.I)
DFY_CEX_HEAD = re.compile(r"^\s*Counterexample(?: for first failing assertion)?:\s*$", re.M)
DFY_STATE = re.compile(
    r"^(?P<file>[^\s(]+\.dfy)\((?P<line>\d+),(?P<col>\d+)\)(?::\s*(?P<label>[^:\n]*?))?\s*:\s*$")
DFY_ASSIGN = re.compile(
    r"^\s+(?P<name>[A-Za-z_][\w'#$]*)\s*:\s*(?P<type>[^=\n]+?)\s*=\s*(?P<value>.+?)\s*$")
DFY_ASSUME = re.compile(r"^\s*assume\s+(?P<expr>.+?)\s*;\s*$")
DFY_CONJUNCT = re.compile(r"([A-Za-z_][\w'#$]*)\s*==\s*([^&]+?)\s*(?=&&|$)")
DFY_DECL = re.compile(
    r"^\s*(?:ghost\s+|static\s+|twostate\s+)*"
    r"(?:method|function|lemma|predicate|function method|greatest lemma|least lemma)"
    r"(?:\s*\{:[^}]*\})*\s+([A-Za-z_][A-Za-z0-9_']*)")
DFY_LEAF = re.compile(
    r'("(?:[^"\\]|\\.)*")'
    r"|('(?:[^'\\]|\\.)')"
    r"|(-?\d+(?:\.\d+)?)"
    r"|\b(true|false|null)\b"
    r"|\b([A-Z][A-Za-z0-9_]*)\b"
)
DFY_VACUOUS = re.compile(r"^(?:(?:assert|expect)\s+true\s*;?\s*)+$")


def path_for(root):
    return os.path.join(root, CEX)


def sha(text):
    return hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(s):
    """Whitespace-collapsed, formatter-tolerant form.

    `aiken fmt` adds a trailing comma before a closing bracket when it wraps
    a value across lines, and the recorder may have captured the one-line
    form. Dropping it here keeps a reformat from reading as a weakening.
    """
    s = " ".join(str(s or "").split())
    return re.sub(r",\s*(?=[}\])])", "", s)


def strip_ak(text):
    """Comments and string literals removed — both are places to hide a paste."""
    return AK_STRING.sub(' @"" ', AK_COMMENT.sub(" ", text))


def strip_dfy(text):
    """Dafny comments (line and block) and string literals removed."""
    return DFY_STRING.sub(' "" ', DFY_COMMENT.sub(" ", text))


def leaves(value, tool="aiken"):
    """The literals of a reified value, in order, as (kind, text).

    Constructor names are collected separately because opaque types render
    as something that is not writable source: `Dict([(#"ab", True)])` has to
    be built with `dict.from_list(...)`, so requiring the word `Dict` would
    make a legitimate pin impossible. The data inside it is still required.
    """
    vals, ctors = [], []
    if tool == "dafny":
        for m in DFY_LEAF.finditer(str(value or "")):
            string, char, number, keyword, ident = m.groups()
            if string or char or number or keyword:
                vals.append(string or char or number or keyword)
            else:
                ctors.append(ident)
        return vals, ctors
    for m in LEAF.finditer(str(value or "")):
        byte, string, integer, ident = m.groups()
        if byte or string or integer:
            vals.append(byte or string or integer)
        elif ident in ("True", "False"):
            vals.append(ident)
        else:
            ctors.append(ident)
    return vals, ctors


def required_leaves(value, tool="aiken"):
    """What a pinned test must physically contain, in order."""
    vals, ctors = leaves(value, tool)
    if vals:
        return vals
    if ctors:
        return ctors
    n = norm(value)
    return [n] if n else []


def contains_in_order(body, needles):
    """Every needle present, in order, on token boundaries."""
    cursor = 0
    for nd in needles:
        pat = re.compile(
            r"(?<![A-Za-z0-9_.#\"@])" + re.escape(nd) + r"(?![A-Za-z0-9_.])")
        m = pat.search(body, cursor)
        if not m:
            return False, nd
        cursor = m.end()
    return True, None


def _brace_body(src, start):
    """The text between the `{` at or after `start` and its matching `}`."""
    brace = src.find("{", start)
    if brace < 0:
        return None
    depth, body = 0, []
    for j in range(brace, len(src)):
        ch = src[j]
        if ch == "{":
            depth += 1
            if depth == 1:
                continue
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(body)
        body.append(ch)
    return None


def test_body(text, name):
    """(body, is_fail) for `test name(...)` in Aiken source, else (None, None).

    Body is comment- and string-stripped and whitespace-collapsed, so
    containment is judged against code the compiler would actually run.
    """
    src = strip_ak(text)
    for m in TEST_HEAD.finditer(src):
        if m.group(1) != name:
            continue
        depth, i = 0, m.end() - 1
        close = -1
        for j in range(i, len(src)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    close = j
                    break
        if close < 0:
            continue
        rest = src[close + 1:]
        brace = rest.find("{")
        if brace < 0:
            continue
        is_fail = bool(re.match(r"\s*fail\b", rest[:brace]))
        body = _brace_body(src, close + 1)
        if body is None:
            return None, None
        return norm(body), is_fail
    return None, None


def dafny_test_body(text, name):
    """(body, disabled) for a Dafny method/lemma named `name`, else (None, None).

    `disabled` is true when the declaration carries `{:verify false}` or
    `{:axiom}` — a test whose obligations the verifier is told to skip is
    decoration — or when its body contains `assume`, under which anything
    verifies. Attributes sit between the keyword and the name in Dafny
    (`method {:test} Name()`), so they are read off the declaration head.
    """
    src = strip_dfy(text)
    head = re.compile(
        r"(?:^|\n)[ \t]*(?:(?:ghost|static|twostate)\s+)*"
        r"(?:method|lemma|function|predicate|greatest lemma|least lemma)"
        r"(?P<attrs>(?:\s*\{:[^}]*\})*)\s+" + re.escape(name) + r"\s*(?:<[^>]*>)?\s*\(")
    for m in head.finditer(src):
        attrs = m.group("attrs") or ""
        disabled = bool(re.search(r"\{:\s*(?:verify\s+false|axiom)\b", attrs))
        # Skip the parameter list, then any spec clauses, to the body brace.
        depth, close = 0, -1
        for j in range(m.end() - 1, len(src)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    close = j
                    break
        if close < 0:
            continue
        body = _brace_body(src, close + 1)
        if body is None:
            return None, None
        if re.search(r"\bassume\b", body):
            disabled = True
        return norm(body), disabled
    return None, None


def hollow(body, tool="aiken"):
    """A body that runs but asserts nothing. Returns a reason or ''."""
    if not body:
        return "the body is empty"
    if tool == "dafny":
        if DFY_VACUOUS.match(body):
            return f"the body is `{body}` — it proves nothing"
        return ""
    if VACUOUS.match(body):
        return f"the body is `{body}` — it cannot fail"
    toks = body.split()
    if len(toks) >= 2 and toks[-1] in ("True", "False") and toks[-2] not in TAIL_OPS:
        return (f"the body ends in a bare `{toks[-1]}` that no operator "
                f"consumes — the value is bound and then ignored")
    return ""


def tracked(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"],
                             capture_output=True, text=True, check=True).stdout
    except Exception:  # noqa: BLE001
        return set()
    return {f for f in out.splitlines() if f}


def head_sha(root):
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def tool_version(tool):
    try:
        r = subprocess.run([tool, "--version"], capture_output=True, text=True,
                           timeout=10)
        return r.stdout.strip()[:80] if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def read(root):
    """Every recorded row, oldest first. A malformed line is fatal.

    Rows are kept small — the bulky prover payload lives in the evidence
    sidecar — so a partial append is unlikely; but a store that silently
    shrank would let a pinned counterexample disappear, which is the one
    direction this file must never fail in.
    """
    p = path_for(root)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"cex: {p}:{i} is not valid JSON: {e}")
    return out


def current(root):
    """Latest state per dedupeKey, in first-seen order."""
    state = {}
    for r in read(root):
        state[r.get("dedupeKey")] = r
    return state


def append(root, row):
    p = path_for(root)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def write_evidence(root, row, raw):
    d = os.path.join(root, EVIDENCE)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{row['cexId']}.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"row": row, "raw": raw}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return p


def file_inbox(root, kind, node, detail):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import inbox as _inbox
        _inbox.add(root, kind, node, "", detail)
    except Exception as e:  # noqa: BLE001 - a recorder never decides green
        print(f"cex: could not file an inbox row: {e}", file=sys.stderr)


def ingest_failed(root, output, why, tool="aiken"):
    """Loud, and never red. The prover's exit code is the gate, not ours."""
    d = os.path.join(root, SCRATCH)
    os.makedirs(d, exist_ok=True)
    stamp = now().replace(":", "").replace("-", "")
    p = os.path.join(d, f"ingest-failed-{stamp}.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(output[:20000])
    # Distinct failure shapes get distinct inbox ids, or the second parser
    # break is swallowed as a duplicate of the first and never surfaces.
    # The tool is in the id too: two parsers drifting in the same week must
    # not collapse into one row.
    node = f"{tool}:{sha(output[:2000])[:8]}"
    file_inbox(root, "cex-ingest-failed", node,
               f"[{tool}] {why}. Unparsed output head saved to {p}")
    print(f"cex: INGEST-FAILED ({tool}) — {why}", file=sys.stderr)
    print(f"cex: saved {p}; filed an inbox item. Not failing the build: the "
          f"prover's exit code is the gate, not the recorder's.", file=sys.stderr)
    return 0


# ------------------------------------------------------------------- parsers
# Each parser returns one of:
#   ("nothing", None)          the run recorded no failures (green, or nothing ran)
#   ("failed", why)            parser drift — file INGEST-FAILED, never red
#   ("rows", rows, raw_for)    rows to record; raw_for(row) is the evidence blob

def parse_aiken(root, output, exit_code):
    text = output.strip()
    if not text:
        if exit_code not in (0, 1):
            # An ill-formed fuzzer panics with nothing on stdout; reading that
            # as "no failures" would be silently wrong.
            return ("failed", f"the prover exited {exit_code} and wrote nothing "
                              f"to stdout")
        return ("nothing", None)  # nothing ran, or a type error that never reached the tests
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        if exit_code == 0:
            return ("nothing", None)
        return ("failed", "stdout is not JSON (a pre-1.1.6 aiken writes "
                          "terminal boxes; JSON needs a non-TTY stdout)")
    mods = doc.get("modules")
    if not isinstance(mods, list) or not all(
            isinstance(m, dict) and isinstance(m.get("tests"), list) for m in mods):
        return ("failed", "no recognizable modules[].tests[] (note: the "
                          "published --show-json-schema names it 'test', "
                          "real output says 'tests')")

    seed = doc.get("seed")
    rows, extracted = [], 0
    for mod in mods:
        module = str(mod.get("name") or "")
        for t in mod.get("tests", []):
            if not isinstance(t, dict) or t.get("status") != "fail":
                # A `fail`-annotated property that correctly failed is a PASS
                # carrying a counterexample. Recording it would mint a
                # counterexample out of a green test.
                continue
            extracted += 1
            title = str(t.get("title") or "")
            cx = t.get("counterexample")
            is_prop = "iterations" in t or "counterexample" in t
            if isinstance(cx, str):
                payload, signature = cx, "counterexample"
            elif isinstance(cx, dict):
                payload, signature = "", "fuzzer-error"
            elif is_prop:
                payload, signature = "", "no-counterexample-found"
            else:
                payload, signature = "", "assertion"
            assertion = t.get("assertion") if isinstance(t.get("assertion"), str) else ""
            kind = "property" if is_prop else ("unit" if not is_prop else "unknown")
            # Containment needs something to check. A unit test has no
            # generated input, so its assertion block is what a regression
            # must reproduce.
            contain = payload or (assertion if kind == "unit" else "")
            key = f"aiken|{module}|{title}|{norm(payload) or norm(assertion)}"
            rows.append({
                "cexId": "cex_" + sha(key)[:12],
                "dedupeKey": key,
                "tool": "aiken",
                "toolVersion": tool_version("aiken"),
                "module": module,
                "title": title,
                "selector": f"{module}.{{{title}}}",
                "kind": kind,
                "input": payload or None,
                "inputForm": "aiken-source" if payload else None,
                "assertion": assertion,
                "containment": norm(contain),
                "signature": signature,
                "iterations": t.get("iterations"),
                "seed": seed,
                "status": "open",
                "firstSeen": now(),
                "headSha": head_sha(root),
            })

    # Extraction is an invariant, not a best effort: if the run reported
    # failures and none were extracted, the format moved under us and
    # silence would read as a clean sweep forever after.
    reported = ((doc.get("summary") or {}).get("failed")
                if isinstance(doc.get("summary"), dict) else None)
    if isinstance(reported, int) and reported > 0 and extracted == 0:
        return ("failed", f"summary.failed is {reported} but no failing test "
                          f"could be extracted — the output format changed")

    def raw_for(row):
        return next((t for m in mods for t in m.get("tests", [])
                     if isinstance(t, dict) and t.get("title") == row["title"]), {})
    return ("rows", rows, raw_for)


def dafny_model(section):
    """(assignments, label) from a Dafny counterexample section.

    The `initial state` block is preferred: it holds the method's inputs,
    which is the shrunk-input equivalent. Both spellings are read — the
    `name : type = value` lines Dafny 3 prints and the `assume a == 1 &&
    b == 2;` statements Dafny 4 prints — so the parser survives either.
    """
    blocks, cur = [], None
    for line in section.splitlines():
        st = DFY_STATE.match(line)
        if st:
            cur = {"label": (st.group("label") or "").strip(), "assign": []}
            blocks.append(cur)
            continue
        if cur is None:
            cur = {"label": "", "assign": []}
            blocks.append(cur)
        a = DFY_ASSIGN.match(line)
        if a:
            cur["assign"].append((a.group("name"), a.group("value")))
            continue
        s = DFY_ASSUME.match(line)
        if s:
            for name, value in DFY_CONJUNCT.findall(s.group("expr")):
                cur["assign"].append((name, value.strip()))
    initial = next((b for b in blocks if "initial" in b["label"].lower() and b["assign"]), None)
    first = next((b for b in blocks if b["assign"]), None)
    chosen = initial or first
    if not chosen:
        return [], ""
    return chosen["assign"], chosen["label"]


def dafny_enclosing(root, rel, line):
    """Name of the declaration containing `line` in a Dafny file, or ''."""
    p = os.path.join(root, rel)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    name = ""
    for i, raw in enumerate(lines[:line], 1):
        m = DFY_DECL.match(DFY_COMMENT.sub("", raw))
        if m:
            name = m.group(1)
    return name


def parse_dafny(root, output, exit_code):
    text = output.strip()
    if exit_code == 0:
        return ("nothing", None)
    if not text:
        return ("failed", f"dafny exited {exit_code} and wrote nothing to stdout")
    errors = [m for m in DFY_ERROR.finditer(text)]
    summary = DFY_SUMMARY.search(text)
    reported = int(summary.group("n")) if summary else None
    head = DFY_CEX_HEAD.search(text)
    assigns, label = ([], "")
    if head:
        assigns, label = dafny_model(text[head.end():])
        if not assigns:
            return ("failed", "a Counterexample heading with no `name : type = "
                              "value` or `assume name == value;` line under it — "
                              "the model format changed")
    if not errors and reported is None:
        return ("failed", "no `file.dfy(line,col): Error:` line and no verifier "
                          "summary — this is not dafny verify output as this "
                          "parser knows it")
    verification = [e for e in errors if DFY_VERIFICATION.search(e.group("msg"))]
    if not verification:
        if errors:
            # Resolution or type errors: the run never verified anything, so
            # there is no failing input to record. Said, not swallowed.
            print(f"cex: {len(errors)} dafny error(s) but none is a verification "
                  f"failure (resolution or type errors) — nothing to record")
            return ("nothing", None)
        if reported and reported > 0:
            return ("failed", f"the verifier summary reports {reported} error(s) "
                              f"but no Error: line could be parsed — the output "
                              f"format changed")
        return ("nothing", None)

    rows = []
    for i, e in enumerate(verification):
        rel = e.group("file").replace(os.sep, "/")
        line = int(e.group("line"))
        method = dafny_enclosing(root, rel, line) or f"line-{line}"
        msg = e.group("msg")
        # Dafny models the FIRST failing assertion only. The model belongs to
        # that error; later failures are recorded as assertions with no input.
        model = assigns if (i == 0 and assigns) else []
        payload = ", ".join(f"{n} == {v}" for n, v in model)
        signature = "counterexample" if model else "assertion"
        key = f"dafny|{rel}|{method}|{norm(payload) or norm(msg)}"
        rows.append({
            "cexId": "cex_" + sha(key)[:12],
            "dedupeKey": key,
            "tool": "dafny",
            "toolVersion": tool_version("dafny"),
            "module": rel,
            "title": method,
            "selector": f"{rel}:{method}",
            "kind": "method",
            "input": payload or None,
            "inputForm": "dafny-model" if payload else None,
            "assertion": f"{rel}({line},{e.group('col')}): {msg}",
            "containment": norm(payload),
            "signature": signature,
            "iterations": None,
            "seed": None,
            "status": "open",
            "firstSeen": now(),
            "headSha": head_sha(root),
            "modelState": label or None,
        })

    def raw_for(row):
        return {"error": row["assertion"],
                "model": [{"name": n, "value": v} for n, v in
                          (assigns if row["signature"] == "counterexample" else [])],
                "output": text[:20000]}
    return ("rows", rows, raw_for)


# The registry. `parse` reads a run; `body` reads a regression test in that
# prover's language into (body, oracle_inverted); `roots` bounds where a pin
# may live (None: anywhere tracked, judged by `ext`); `rerun` is the command a
# human types to reproduce; `draft` is the skeleton --pin writes to scratch.
TOOLS = {
    "aiken": {
        "parse": parse_aiken,
        "body": test_body,
        "roots": COMPILE_ROOTS,
        "ext": ".ak",
        "inverted": ("annotated `fail`, which inverts the oracle: it passes "
                     "because the code is still broken and goes red the day "
                     "someone fixes it"),
        "rerun": lambda row: (f"aiken check -e -m '{row.get('selector')}'"
                              f" --seed {row.get('seed')}"),
        "head": lambda cexid: f"test {cexid}() {{",
        "hint": "// replace with the real predicate this value broke",
        "call": lambda row: f"<predicate>({row.get('input') or row.get('assertion') or ''})",
    },
    "dafny": {
        "parse": parse_dafny,
        "body": dafny_test_body,
        "roots": None,
        "ext": ".dfy",
        "inverted": ("verification-disabled: `{:verify false}`, `{:axiom}`, or "
                     "an `assume` in its body, under which anything verifies"),
        "rerun": lambda row: (f"dafny verify --extract-counterexample "
                              f"{row.get('module')}"),
        "head": lambda cexid: f"method {{:test}} {cexid}() {{",
        "hint": "// call the method with the recorded values and expect the fixed result",
        "call": lambda row: f"var r := <method>({', '.join(v for _, v in _pairs(row))});",
    },
}


def _pairs(row):
    """(name, value) pairs out of a recorded `n == 1, m == 2` payload."""
    out = []
    for part in str(row.get("input") or "").split(", "):
        if " == " in part:
            n, v = part.split(" == ", 1)
            out.append((n.strip(), v.strip()))
    return out


def ingest(root, output, exit_code, tool):
    spec = TOOLS.get(tool)
    if spec is None:
        print(f"cex: unknown tool '{tool}' (known: {sorted(TOOLS)})", file=sys.stderr)
        return 1
    parsed = spec["parse"](root, output, exit_code)
    if parsed[0] == "failed":
        return ingest_failed(root, output, parsed[1], tool)
    if parsed[0] == "nothing":
        return 0
    rows, raw_for = parsed[1], parsed[2]
    if not rows:
        print("cex: no failing tests in this run")
        return 0

    os.makedirs(os.path.dirname(path_for(root)) or ".", exist_ok=True)
    state = current(root)
    written = 0
    for row in rows:
        prior = state.get(row["dedupeKey"])
        if prior:
            status = prior.get("status")
            if status == "pinned":
                # The prover just produced the exact value a pinned test
                # claims to guard. That is the only mechanical evidence this
                # slice can offer that a pin does not bite, so it is never
                # swallowed as a duplicate.
                file_inbox(root, "cex-pin-inert", prior["cexId"],
                           f"{prior['selector']} was re-found with the recorded "
                           f"counterexample while pinned to "
                           f"{(prior.get('pin') or {}).get('file')} — the "
                           f"regression test does not prevent it")
                print(f"cex: PIN INERT — {prior['cexId']} re-found while pinned",
                      file=sys.stderr)
            else:
                print(f"cex: already {status} {prior['cexId']} ({row['selector']})")
            continue
        append(root, row)
        write_evidence(root, row, raw_for(row))
        written += 1
        print(f"cex: recorded {row['cexId']} {row['selector']} "
              f"[{row['signature']}] {(row['input'] or row['assertion'] or '')[:70]}")
    if written:
        print(f"cex: {written} new counterexample(s) in {CEX} — commit it; "
              f"pin one with: cex.py --pin <cexId>")
    return 0


def resolve(root, cexid):
    for row in current(root).values():
        if row.get("cexId") == cexid:
            return row
    return None


def pin(root, cexid, path, testname):
    row = resolve(root, cexid)
    if not row:
        print(f"cex: no such counterexample '{cexid}'", file=sys.stderr)
        return 1
    if row.get("status") == "retired":
        print(f"cex: {cexid} is retired; re-ingest it before pinning",
              file=sys.stderr)
        return 1
    tool = row.get("tool") or "aiken"
    spec = TOOLS.get(tool)
    if spec is None:
        print(f"cex: {cexid} was recorded by '{tool}', which this cex.py does "
              f"not know (known: {sorted(TOOLS)})", file=sys.stderr)
        return 1

    if not path:
        # Step 1: freeze the evidence and offer a draft. The draft goes to
        # scratch, which is not a legal pin target — a tool must not write
        # the artifact that satisfies its own containment check.
        d = os.path.join(root, SCRATCH)
        os.makedirs(d, exist_ok=True)
        draft = os.path.join(d, f"{cexid}{spec['ext']}.draft")
        need = required_leaves(row.get("containment"), tool)
        with open(draft, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                f"// DRAFT from fluxpoint cex.py --pin. Not a pin yet, and not\n"
                f"// compiled from here — write the real test into the source\n"
                f"// tree, then record it with:\n"
                f"//   cex.py --pin {cexid} --file <path> --test-name {cexid}\n"
                f"//\n"
                f"// cexId:  {cexid}\n"
                f"// source: {row.get('selector')} ({tool} "
                f"{row.get('toolVersion')}"
                + (f", seed {row.get('seed')}, after {row.get('iterations')} test(s)"
                   if row.get("seed") is not None else "")
                + ")\n"
                f"// re-run: {spec['rerun'](row)}\n"
                f"//\n"
                f"// The value below is copied verbatim from {CEX}. It is\n"
                f"// evidence, not a restatement — retyping it from memory is\n"
                f"// the one thing the pin check refuses.\n"
                f"// Literals your test must contain, in order: "
                f"{', '.join(need) if need else '(none)'}\n\n"
                f"{spec['head'](cexid)}\n"
                f"  {spec['hint']}\n"
                f"  {spec['call'](row)}\n"
                f"}}\n")
        print(f"cex: evidence at {os.path.join(EVIDENCE, cexid + '.json')}")
        print(f"cex: draft at {draft}")
        print(f"cex: still OPEN. Write the compiling test, then re-run with "
              f"--file and --test-name.")
        return 0

    # Step 2: record a real test, but only if the tree actually carries it.
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    if rel.startswith(".."):
        print(f"cex: {path} is outside the repo", file=sys.stderr)
        return 1
    rel = rel.replace(os.sep, "/")
    if rel.startswith(".claude/"):
        print(f"cex: {rel} is scratch, not source — a pin must live where "
              f"{tool} compiles it", file=sys.stderr)
        return 1
    roots = spec["roots"]
    if roots is not None and not rel.startswith(roots):
        print(f"cex: {rel} is not under {', '.join(roots)} — a test "
              f"{tool} never compiles cannot guard anything", file=sys.stderr)
        return 1
    if not rel.endswith(spec["ext"]):
        print(f"cex: {rel} is not a {spec['ext']} file — {tool} never reads it, "
              f"so it cannot guard anything", file=sys.stderr)
        return 1
    if rel not in tracked(root):
        print(f"cex: {rel} is not tracked by git — commit it first, or the "
              f"pin guards a file nobody else has", file=sys.stderr)
        return 1
    testname = testname or cexid
    if cexid not in testname:
        print(f"cex: --test-name must contain {cexid}, so one test cannot "
              f"silently discharge several counterexamples", file=sys.stderr)
        return 1
    for other in current(root).values():
        opin = other.get("pin") or {}
        if (other.get("status") == "pinned" and other.get("cexId") != cexid
                and opin.get("file") == rel and opin.get("testName") == testname):
            print(f"cex: {rel}:{testname} is already the pin for "
                  f"{other['cexId']}", file=sys.stderr)
            return 1

    ok, why = verify_pin(root, row, rel, testname)
    if not ok:
        print(f"cex: refusing the pin — {why}", file=sys.stderr)
        return 1

    newrow = dict(row)
    newrow["status"] = "pinned"
    newrow["pin"] = {"file": rel, "testName": testname, "pinnedWhen": now()}
    append(root, newrow)
    print(f"cex: pinned {cexid} to {rel}:{testname}")
    print(f"cex: commit {CEX} — the guard only exists for others once it is "
          f"in the tree")
    return 0


def verify_pin(root, row, rel, testname):
    """(ok, why). The mechanical part of trusting a pin."""
    tool = row.get("tool") or "aiken"
    spec = TOOLS.get(tool)
    if spec is None:
        return False, f"recorded by unknown tool '{tool}'"
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        return False, f"{rel} does not exist"
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return False, f"{rel} is unreadable: {e}"
    body, inverted = spec["body"](text, testname)
    if body is None:
        return False, f"{rel} has no test/method named `{testname}`"
    if inverted:
        return False, f"`{testname}` is {spec['inverted']}"
    why = hollow(body, tool)
    if why:
        return False, f"`{testname}` proves nothing — {why}"
    need = required_leaves(row.get("containment"), tool)
    if not need:
        return False, ("this counterexample recorded no literal to check for; "
                       "it cannot be pinned mechanically yet")
    ok, missing = contains_in_order(body, need)
    if not ok:
        return False, (
            f"`{missing}` from the recorded counterexample is not in the body of "
            f"`{testname}` (needed, in order: {', '.join(need)}). The value "
            f"is evidence, not a restatement — paste it, do not retype it")
    return True, ""


def check(root):
    p = path_for(root)
    if not os.path.exists(p):
        print("cex: no counterexamples recorded — dormant")
        return 0
    rows = current(root)
    pinned = [r for r in rows.values() if r.get("status") == "pinned"]
    openrows = [r for r in rows.values() if r.get("status") == "open"]
    if not pinned:
        print(f"cex: {len(openrows)} open counterexample(s), none pinned yet")
        return 0
    bad = []
    for row in pinned:
        pinrec = row.get("pin") or {}
        ok, why = verify_pin(root, row, pinrec.get("file", ""),
                             pinrec.get("testName", ""))
        if not ok:
            bad.append((row, why))
    if not bad:
        print(f"cex: green — {len(pinned)} pinned counterexample(s) still in "
              f"the tree, {len(openrows)} open")
        return 0
    print("\ncex: RED — a pinned counterexample lost its regression\n",
          file=sys.stderr)
    for row, why in bad:
        pinrec = row.get("pin") or {}
        print(f"  {row['cexId']} ({row.get('selector')})", file=sys.stderr)
        print(f"      pinned to {pinrec.get('file')}:{pinrec.get('testName')}",
              file=sys.stderr)
        print(f"      {why}", file=sys.stderr)
    print(
        "\n  A counterexample whose test is gone is a bug with no evidence it is\n"
        "  still handled. Two ways out, both leaving a diff:\n"
        "    re-pin it   cex.py --pin <cexId> --file <path> --test-name <name>\n"
        "    retire it   cex.py --retire <cexId> --reason \"...\"  (also needs a\n"
        "                Decisions row in the work file naming the cexId)\n"
        "  If the pinned test was deleted and spec-guard is armed, it will want\n"
        "  a Decisions row for the obligation too.",
        file=sys.stderr)
    return 1


def work_text(root):
    """The Decisions section of the work file — the tracked, reviewed place."""
    for name in ("WORK.md", "LOOP.md"):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"^##\s+Decisions\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M | re.I)
        return m.group(1) if m else ""
    return ""


def retire(root, cexid, reason):
    row = resolve(root, cexid)
    if not row:
        print(f"cex: no such counterexample '{cexid}'", file=sys.stderr)
        return 1
    if len((reason or "").strip()) < 20:
        print("cex: --reason must be at least 20 characters — retiring is the "
              "one move that weakens this ratchet", file=sys.stderr)
        return 1
    if cexid not in work_text(root):
        print(f"cex: refusing to retire {cexid} — no Decisions row in the work "
              f"file names it.\n"
              f"  A reason in a store nobody reads is not accountability. Add a "
              f"row naming {cexid},\n"
              f"  the same way spec-guard requires one to forgive a removed "
              f"obligation.", file=sys.stderr)
        return 1
    newrow = dict(row)
    newrow["status"] = "retired"
    newrow["retire"] = {"reason": reason.strip(), "when": now()}
    append(root, newrow)
    print(f"cex: retired {cexid}")
    pinrec = row.get("pin") or {}
    if pinrec.get("testName"):
        print(f"cex: if you also delete the test and spec-guard is armed, it "
              f"will want a Decisions row for:\n"
              f"      {row.get('tool')}:{pinrec.get('file')}:{pinrec.get('testName')}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--from", dest="src", help="prover output file (default stdin)")
    ap.add_argument("--exit", type=int, default=0, help="the prover's exit code")
    ap.add_argument("--tool", default="aiken", help=f"one of {sorted(TOOLS)}")
    ap.add_argument("--file")
    ap.add_argument("--test-name")
    ap.add_argument("--reason", default="")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ingest", action="store_true")
    g.add_argument("--pin", metavar="CEXID")
    g.add_argument("--retire", metavar="CEXID")
    g.add_argument("--check", action="store_true")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.ingest:
        raw = (open(a.src, encoding="utf-8", errors="replace").read()
               if a.src else sys.stdin.read())
        return ingest(a.root, raw, a.exit, a.tool)
    if a.pin:
        return pin(a.root, a.pin, a.file, a.test_name)
    if a.retire:
        return retire(a.root, a.retire, a.reason)
    if a.check:
        return check(a.root)

    rows = current(a.root)
    if not rows:
        print("cex: no counterexamples recorded")
        return 0
    by = {}
    for r in rows.values():
        by.setdefault(r.get("status"), []).append(r)
    print(f"cex: {len(rows)} counterexample(s)")
    for status in sorted(by):
        print(f"  [{status}] {len(by[status])}")
        for r in by[status][:10]:
            val = (r.get("input") or r.get("assertion") or "")[:60]
            print(f"      {r['cexId']}  [{r.get('tool')}] {r.get('selector')}  {val}")
            if r.get("pin"):
                print(f"          pinned to {r['pin'].get('file')}:"
                      f"{r['pin'].get('testName')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
