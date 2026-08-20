#!/usr/bin/env python3
"""Record a decision so the next context cannot silently re-decide it.

The Decisions table is the one bus in this system that carries a *choice*
across context death — SessionStart injects the newest rows, and the graph
compiler can bind a frozen decision into a later campaign's prompt. Until
now only graph nodes could fill it: `record-run.py` files a `DecisionV1` a
campaign produced, and loop mode had no way in at all. So the exact failure
the schema was written against — "a decision that overturned the prior is
the one a fresh context re-decides the other way" — stayed wide open for
loop work, which is most work.

  decision.py --record [--graph WORK.md] < decision.json
  decision.py --none "<why nothing was decided>" --session <id>

`--record` validates against the shipped `contracts/DecisionV1.schema.json`
before writing anything. The floors in that schema are the point and are
enforced here rather than described: at least two options, each with a
`strongest_objection` of real length (including against the one that won —
an option with no objection was not examined), and a rationale long enough
that a lazy sentence cannot satisfy it.

Unlike an Evidence row, a decision has no verdict to witness: it is
inherently an assertion, and there is no gate that could certify it. What
this buys is not proof but survival — the choice, the alternatives, and the
objections outlive the context that weighed them, in a form the next one
inherits. That is why the schema floors matter more here than provenance
would.

`--none` writes a per-session marker saying no decision was made and why,
so silence is a statement rather than an absence. Nothing reads it unless
the optional distill check is armed (`FPL_DISTILL=1`), which is deliberately
off by default: a gate that starts by blocking stops gets switched off
before it has established what normal looks like.

This script executes nothing and decides nothing. It validates, and it
writes one row.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile

DEC_HDR = "| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |"
SCHEMA = "DecisionV1.schema.json"


def cell(s, n=160):
    """One table cell: no pipes, no newlines, bounded. record-run.py's rule."""
    s = str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def load_schema(plugin_root):
    p = os.path.join(plugin_root, "contracts", SCHEMA)
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"decision: cannot read {p}: {e}")


def validate(rec, schema):
    """Findings against DecisionV1. Shallow by design, floors enforced.

    release.py validates a pasted proof the same way and for the same
    reason: this is not a general JSON Schema engine, it is the specific
    set of floors that stop a well-formed nothing from passing.
    """
    f = []
    if not isinstance(rec, dict):
        return ["the decision must be a JSON object"]
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in rec:
            f.append(f"missing required field '{key}'")
    for key, spec in props.items():
        if key not in rec:
            continue
        val = rec[key]
        want = spec.get("type")
        if want == "string" and not isinstance(val, str):
            f.append(f"{key} must be a string")
        elif want == "boolean" and not isinstance(val, bool):
            f.append(f"{key} must be true or false")
        elif want == "array" and not isinstance(val, list):
            f.append(f"{key} must be an array")
        if isinstance(val, str) and len(val.strip()) < spec.get("minLength", 0):
            f.append(f"{key} is shorter than {spec['minLength']} characters — "
                     f"{spec.get('description', 'the floor exists so a lazy output cannot satisfy it')}")
        if isinstance(val, list) and len(val) < spec.get("minItems", 0):
            f.append(f"{key} needs at least {spec['minItems']} entries — "
                     f"{spec.get('description', '')}".rstrip(" —"))

    opts = rec.get("options")
    if isinstance(opts, list):
        ospec = (props.get("options", {}).get("items", {}))
        oprops = ospec.get("properties", {})
        for i, o in enumerate(opts):
            if not isinstance(o, dict):
                f.append(f"options[{i}] must be an object")
                continue
            for key in ospec.get("required", []):
                if key not in o:
                    f.append(f"options[{i}] missing '{key}'")
            for key, spec in oprops.items():
                v = o.get(key)
                if isinstance(v, str) and len(v.strip()) < spec.get("minLength", 0):
                    f.append(
                        f"options[{i}].{key} is shorter than {spec['minLength']} "
                        f"characters — an option nobody argued against was not examined")
        chosen = rec.get("chosen")
        names = [o.get("option") for o in opts if isinstance(o, dict)]
        if isinstance(chosen, str) and names and chosen not in names:
            f.append(f"chosen '{chosen}' is not one of the options considered "
                     f"({', '.join(str(n) for n in names)}) — a decision is a "
                     f"choice among the alternatives it weighed")
    return f


def splice(path, row):
    if not os.path.exists(path):
        return "no-file"
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if DEC_HDR not in text:
        return "no-header"
    new, n = re.subn(re.escape(DEC_HDR) + r"\n(\|[-:| ]+\|)\n",
                     lambda m: m.group(0) + row + "\n", text, count=1)
    if not n:
        return "no-separator"
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".decision-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return "written"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--graph", default="WORK.md")
    ap.add_argument("--plugin-root", default=os.path.dirname(here))
    ap.add_argument("--id", default="", help="short kebab-case id for the row")
    ap.add_argument("--result", help="decision JSON file (default stdin)")
    ap.add_argument("--session", default="nosession")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true")
    g.add_argument("--none", metavar="REASON")
    a = ap.parse_args()

    if a.none:
        if len(a.none.strip()) < 20:
            print("decision: --none needs a real reason (20+ chars). Saying why "
                  "nothing was decided is the point; 'n/a' is not a statement.",
                  file=sys.stderr)
            return 2
        sd = os.path.join(a.root, ".claude", "fluxpoint")
        os.makedirs(sd, exist_ok=True)
        p = os.path.join(sd, f"{a.session}.nodecision")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(a.none.strip() + "\n")
        print(f"decision: recorded that this session decided nothing — {p}")
        return 0

    raw = (open(a.result, encoding="utf-8").read() if a.result else sys.stdin.read())
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"decision: input is not JSON: {e}", file=sys.stderr)
        return 1

    findings = validate(rec, load_schema(a.plugin_root))
    if findings:
        print("decision: REFUSED — this is not a DecisionV1\n", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        print("\n  The floors are the point: a decision worth surviving context "
              "death\n  names the alternatives it beat and the best case against "
              "each,\n  including against the one that won.", file=sys.stderr)
        return 1

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    did = a.id or re.sub(r"[^a-z0-9]+", "-",
                         str(rec.get("question", ""))[:40].lower()).strip("-")
    row = (f"| {ts} | {cell(did, 40)} | {cell(rec.get('chosen'), 60)} "
           f"| {'YES' if rec.get('overturned_prior') else 'no'} "
           f"| {cell(rec.get('frozen_by') or 'none', 40)} "
           f"| {cell(rec.get('rationale'))} |")

    graph = a.graph if os.path.isabs(a.graph) else os.path.join(a.root, a.graph)
    status = splice(graph, row)
    if status == "written":
        print(f"decision: recorded '{did}' in {a.graph}")
        print(row)
        if rec.get("overturned_prior"):
            print("decision: this one overturned the prior — it is exactly the "
                  "kind a fresh context re-decides the other way, which is why "
                  "it is now on disk.")
        return 0
    msgs = {
        "no-file": f"{graph} does not exist",
        "no-header": (f"{graph} has no Decisions table — add one so a frozen "
                      f"choice outlives this run"),
        "no-separator": f"{graph} has a Decisions header with no separator row",
    }
    print(f"decision: {msgs.get(status, status)}; nothing written", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
