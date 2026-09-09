#!/usr/bin/env python3
"""Prompt hygiene: the anti-patterns that hobble a frontier model, kept out.

Prompt bloat accretes invisibly. Every future contributor adding one more
"CRITICAL:" or one more "double-check your work" is individually
reasonable, and the cost shows up only in aggregate — as verbosity, extra
tool calls, and a model that takes the emphasis literally. This is a
deterministic scan of the prompt-bearing surface (agents, commands,
skills, templates) for the classes the Claude Platform cost guidance
names, so drift shows up as a line in the harness rather than as a slow
tax:

  booster        thoroughness and emphasis boosters: shouted ALL-CAPS
                 imperatives, "exhaustively", "be thorough", "double-check",
                 "make sure to", "it is crucial", "at all costs"
  ritual         verification rituals: "verify your work", "re-read your
                 answer", "check your work before responding"
  scaffold       mandatory scratchpad scaffolds: "think step by step",
                 <thinking> / <scratchpad> tags, "chain of thought"
  dated          configuration that has gone stale: superseded model ids
  contradiction  "always X" and "never X" for the same X in one file
  repeat         the same imperative sentence more than once in one file

It is advisory by default (findings on stdout, exit 0) so the false-positive
rate on this repo's deliberately dense idiom is learned before anything
routes around it; `--strict` exits 1 on any finding and is the promotion
path. Two suppressions, both on the record: an inline
`<!-- prompt-audit: allow <rule> -->` on the line or the line before, and a
repo-owned `.fluxpoint-prompt-audit.json` allowlist of
`{"path", "rule", "text"}` entries — an allowlist row that matches nothing
is reported as spent, never left to read as coverage.

  prompt-audit.py [--root .] [--strict] [--json] [paths...]

Paths default to plugins/*/{agents,commands,skills,templates}. Standard
library only, no model in the loop.
"""
import argparse
import glob
import json
import os
import re
import sys

ALLOWLIST = ".fluxpoint-prompt-audit.json"
DEFAULT_GLOBS = ("plugins/*/agents", "plugins/*/commands", "plugins/*/skills",
                 "plugins/*/templates")
EXTS = {".md", ".txt"}
INLINE = re.compile(r"<!--\s*prompt-audit:\s*allow\s+([a-z-]+)\s*-->")

RULES = [
    # Shouting: an ALL-CAPS imperative used as a label ("CRITICAL:") or two
    # or more shouted words in a row ("NEVER SKIP"). A severity vocabulary
    # ("Severity is CRITICAL, HIGH") is a word list, and is left alone.
    ("booster", re.compile(
        r"\b(?:CRITICAL|IMPORTANT|ALWAYS|NEVER|MUST|MANDATORY|ABSOLUTELY|EXTREMELY|"
        r"WARNING|NOTE|REMEMBER)\b(?=[:!]|\s+[A-Z]{3,}\b)")),
    ("booster", re.compile(
        r"\b(?:exhaustively|be exhaustive|exhaustive (?:review|search|list|scan|check|audit|sweep)|"
        r"be (?:very |extremely |as )?thorough|double-check|"
        r"triple-check|make (?:absolutely )?sure (?:to|that|you)|it is (?:crucial|critical|"
        r"essential|imperative|vital) (?:to|that)|at all costs|under no circumstances|"
        r"very carefully|extremely carefully|as (?:thorough|detailed|comprehensive) as possible)\b",
        re.I)),
    ("booster", re.compile(r"!{2,}")),
    ("ritual", re.compile(
        r"\b(?:verify|check|review|re-?read|double-check|proofread|validate)\s+your\s+"
        r"(?:own\s+)?(?:work|answer|output|response|result|reasoning)\b|"
        r"\bbefore\s+(?:responding|answering|replying|finishing),?\s+(?:make sure|ensure|"
        r"verify|check)\b", re.I)),
    ("scaffold", re.compile(
        r"\bthink\s+step[- ]by[- ]step\b|</?(?:thinking|scratchpad|reasoning|thought)>|"
        r"\bchain[- ]of[- ]thought\b|\bthink\s+(?:out\s+loud|aloud)\b|"
        r"\blet(?:'|’)?s\s+think\b|\bshow\s+your\s+(?:reasoning|work)\b", re.I)),
    ("dated", re.compile(
        r"\bclaude-(?:instant|2|3)(?:[.-]\d+)?\b|\bclaude-(?:opus|sonnet)-4(?:-\d+)*\b|"
        r"\bgpt-[345]\b|\btext-davinci\b", re.I)),
]

# Sentences that read as a rule: an imperative opener, six words or more.
IMPERATIVE = re.compile(
    r"^(?:(?:do not|don(?:'|’)t|never|always|only|make sure|ensure|you must|"
    r"you should|remember to|be sure to)\b|[A-Z][a-z]+\b)", re.I)
ALWAYS = re.compile(r"\b(?:always|must)\s+((?:\w+\s+){1,2}\w+)", re.I)
NEVER = re.compile(r"\b(?:never|must not|do not|don(?:'|’)t)\s+((?:\w+\s+){1,2}\w+)", re.I)


def files_under(root, paths):
    out = []
    for p in paths:
        for hit in sorted(glob.glob(os.path.join(root, p))):
            if os.path.isfile(hit):
                out.append(hit)
                continue
            for dp, dns, fns in os.walk(hit):
                dns[:] = [d for d in dns if not d.startswith(".")]
                for fn in sorted(fns):
                    if os.path.splitext(fn)[1] in EXTS:
                        out.append(os.path.join(dp, fn))
    seen, uniq = set(), []
    for f in out:
        rp = os.path.relpath(f, root).replace(os.sep, "/")
        if rp not in seen:
            seen.add(rp)
            uniq.append(rp)
    return uniq


def load_allowlist(root):
    """(entries, problems). A malformed allowlist is a problem, never a pass."""
    p = os.path.join(root, ALLOWLIST)
    if not os.path.exists(p):
        return [], []
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return [], [f"{ALLOWLIST} is not readable JSON ({e}) — ignored, nothing is allowed"]
    entries = doc.get("allow") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return [], [f"{ALLOWLIST} must be an object with an 'allow' list — ignored"]
    ok, problems = [], []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != {"path", "rule", "text"} or not all(
                isinstance(e[k], str) and e[k] for k in ("path", "rule", "text")):
            problems.append(f"{ALLOWLIST}[{i}]: needs exactly path, rule and text, all "
                            f"non-empty strings — ignored")
            continue
        e["_used"] = False
        ok.append(e)
    return ok, problems


CODE_SPAN = re.compile(r"`[^`\n]*`")


def scan_file(rel, text, allow):
    findings = []
    # An inline code span quotes a command, a label or an output line; it is
    # never an instruction to the model. The pattern rules run over a copy
    # with every span blanked in place (line numbers and columns survive);
    # excerpts, suppressions and the repeat rule keep the original text, so
    # an allow entry can quote the span and two sentences that differ only
    # inside a span stay two sentences.
    lines = text.split("\n")
    blanked = CODE_SPAN.sub(lambda m: " " * len(m.group(0)), text).split("\n")

    def allowed(i, rule, excerpt):
        for j in (i, i - 1):
            if 0 <= j < len(lines):
                for m in INLINE.finditer(lines[j]):
                    if m.group(1) == rule:
                        return True
        for e in allow:
            if e["path"] == rel and e["rule"] == rule and e["text"] in excerpt:
                e["_used"] = True
                return True
        return False

    for i, line in enumerate(blanked):
        for rule, rx in RULES:
            for m in rx.finditer(line):
                excerpt = lines[i].strip()[:100]
                if allowed(i, rule, lines[i]):
                    continue
                findings.append({"file": rel, "line": i + 1, "rule": rule,
                                 "match": m.group(0), "excerpt": excerpt})
    # Contradiction: the same three-word tail after "always" and "never".
    always = {m.group(1).lower().strip(): i + 1 for i, l in enumerate(blanked) for m in ALWAYS.finditer(l)}
    never = {m.group(1).lower().strip(): i + 1 for i, l in enumerate(blanked) for m in NEVER.finditer(l)}
    for tail in sorted(set(always) & set(never)):
        i = never[tail] - 1
        if allowed(i, "contradiction", lines[i]):
            continue
        findings.append({"file": rel, "line": never[tail], "rule": "contradiction",
                         "match": tail,
                         "excerpt": f"'always {tail}' (line {always[tail]}) and "
                                    f"'never {tail}' (line {never[tail]})"})
    # Repeat: an imperative sentence of six or more words said more than once.
    seen = {}
    for i, line in enumerate(lines):
        for s in re.split(r"(?<=[.!?])\s+", line.strip()):
            words = s.split()
            if len(words) < 6 or not IMPERATIVE.match(s):
                continue
            key = re.sub(r"[^a-z0-9 ]", "", s.lower())
            key = " ".join(key.split())
            if key in seen:
                if not allowed(i, "repeat", line):
                    findings.append({"file": rel, "line": i + 1, "rule": "repeat",
                                     "match": s[:60],
                                     "excerpt": f"repeats line {seen[key]}: {s[:80]}"})
            else:
                seen[key] = i + 1
    return findings


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any finding (the promotion path from advisory)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("paths", nargs="*", help="files or directories under root")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    paths = a.paths or list(DEFAULT_GLOBS)
    files = files_under(root, paths)
    if not files:
        print(f"prompt-audit: no prompt files under {', '.join(paths)} — nothing scanned",
              file=sys.stderr)
        return 2
    allow, problems = load_allowlist(root)
    findings = []
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            problems.append(f"{rel}: unreadable ({e})")
            continue
        findings += scan_file(rel, text, allow)
    for e in allow:
        if not e["_used"]:
            problems.append(f"{ALLOWLIST}: allow entry for {e['path']} ({e['rule']}: "
                            f"{e['text']!r}) matched nothing — it is spent; drop it")
    mode = "strict" if a.strict else "advisory"
    if a.as_json:
        json.dump({"mode": mode, "files": len(files), "findings": findings,
                   "problems": problems}, sys.stdout, indent=2)
        print()
    else:
        for f in findings:
            print(f"{f['file']}:{f['line']} [{f['rule']}] {f['match']!r} — {f['excerpt']}")
        for p in problems:
            print(f"prompt-audit: {p}", file=sys.stderr)
        by = {}
        for f in findings:
            by[f["rule"]] = by.get(f["rule"], 0) + 1
        tally = ", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"
        print(f"prompt-audit: {len(findings)} finding(s) across {len(files)} file(s) "
              f"({mode}): {tally}")
        if findings and not a.strict:
            print("prompt-audit: advisory — read them as a trend, not a gate; "
                  "--strict is the promotion path once the false-positive rate is known")
    if problems and not findings:
        return 0
    return 1 if (a.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
