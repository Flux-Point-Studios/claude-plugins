#!/usr/bin/env python3
"""Migrate a pre-1.0 repo onto the unified WORK.md contract.

This used to be prose in a slash command, which meant a `git rm` of a repo's
work-state files was driven by an agent following instructions. The
mechanical parts are now code with tests, and the command drives this.

Three phases, deliberately separate so nothing is deleted before the result
has been checked:

  --plan       report what would change; touch nothing
  --apply      write WORK.md, move local state, rewire config; the old files
               are left in place so the result can be inspected and diffed
  --finalize   remove the superseded files, only after verifying WORK.md
               carries at least as many Evidence rows as the sources did

Only one thing is left to judgment and stays with the agent: converting a
pre-0.2 prose GRAPH.md (tables, no IR block) into a graph-ir block. This
script reports that case and refuses to invent one.
"""
import argparse
import json
import os
import re
import shutil
import sys

LOOP = "LOOP.md"
GRAPH = "GRAPH.md"
WORK = "WORK.md"
OLD_PROMPT = "LOOP_PROMPT.md"
NEW_PROMPT = "WORK_PROMPT.md"
OLD_DIRS = [".claude/fluxpoint-loop", ".claude/fluxpoint-graph"]
NEW_DIR = ".claude/fluxpoint"

UNIFIED_HDR = "| When (UTC) | Source | Outcome | Claim | Proof |"
UNIFIED_SEP = "|---|---|---|---|---|"
IR_FENCE = re.compile(r"```json\s+graph-ir\s*\n.*?\n```", re.S)


# ------------------------------------------------------------------ parsing
def sections(text):
    """Split markdown into (heading, body) by '## ', preserving order."""
    out, head, buf = [], None, []
    for line in text.splitlines():
        if line.startswith("## "):
            out.append((head, "\n".join(buf).strip("\n")))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf).strip("\n")))
    return out


def table_rows(body):
    """Data rows of the first markdown table in a section."""
    rows, seen_sep = [], False
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") and c for c in cells):
            seen_sep = True
            continue
        if seen_sep and any(cells):
            rows.append(cells)
    return rows


def to_unified(rows):
    """Map pre-1.0 Evidence rows onto | When | Source | Outcome | Claim | Proof |."""
    out = []
    for r in rows:
        if len(r) >= 7:  # graph: when runId outcome nodes findings harness redteam
            when, run, outcome, nodes, findings, harness, red = r[:7]
            out.append([when, run, outcome,
                        f"graph run: nodes {nodes}, {findings} finding(s)",
                        f"harness {harness}; red-team {red}"])
        elif len(r) >= 5:  # already unified
            out.append(r[:5])
        elif len(r) >= 3:  # loop: when claim proof
            out.append([r[0], "loop", "—", r[1], r[2]])
        elif r:
            out.append([r[0], "loop", "—", " ".join(r[1:]) or "—", "—"])
    return out


def survey(root):
    p = lambda *a: os.path.join(root, *a)
    ir_block = None
    graph_text = ""
    if os.path.exists(p(GRAPH)):
        graph_text = open(p(GRAPH)).read()
        m = IR_FENCE.search(graph_text)
        ir_block = m.group(0) if m else None
    return {
        "loop": os.path.exists(p(LOOP)),
        "graph": os.path.exists(p(GRAPH)),
        "work": os.path.exists(p(WORK)),
        "old_prompt": os.path.exists(p(OLD_PROMPT)),
        "graph_has_ir": ir_block is not None,
        "ir_block": ir_block,
        "graph_text": graph_text,
        "loop_text": open(p(LOOP)).read() if os.path.exists(p(LOOP)) else "",
        "old_dirs": [d for d in OLD_DIRS if os.path.isdir(p(d))],
        "settings": os.path.exists(p(".claude/settings.json")),
        "gitignore": os.path.exists(p(".gitignore")),
        "workflows": sorted(
            f for f in os.listdir(p(".claude/workflows"))
            if f.endswith(".graph.js")
        ) if os.path.isdir(p(".claude/workflows")) else [],
    }


# ------------------------------------------------------------------ building
def build_work(s):
    """Merge LOOP.md and GRAPH.md into one WORK.md, preserving content."""
    loop_secs = sections(s["loop_text"]) if s["loop_text"] else []
    graph_secs = sections(s["graph_text"]) if s["graph_text"] else []
    get = lambda secs, name: next((b for h, b in secs if h == name), "")

    preamble = loop_secs[0][1] if loop_secs else (graph_secs[0][1] if graph_secs else "")
    lines = preamble.splitlines()
    title = next((l for l in lines if l.startswith("# ")), "# WORK: <goal>")
    title = re.sub(r"^# (LOOP|GRAPH):", "# WORK:", title)
    status = next((l for l in lines if l.startswith("STATUS:")), "STATUS: ACTIVE")
    mode = "both" if (s["loop"] and s["graph"]) else ("graph" if s["graph"] else "loop")

    ev_rows = to_unified(table_rows(get(loop_secs, "Evidence"))) + \
              to_unified(table_rows(get(graph_secs, "Evidence")))
    ev_rows.sort(key=lambda r: r[0])

    notes = "\n\n".join(
        b for b in (get(loop_secs, "Notes for the next iteration"),
                    get(graph_secs, "Notes for the next run")) if b
    ) or "<current state, blockers, resume point>"

    out = [title, "", status, f"MODE: {mode}", ""]

    def add(name, body, fallback=""):
        out.extend([f"## {name}", body or fallback, ""])

    add("Definition of Done", get(loop_secs, "Definition of Done"),
        "- [ ] `scripts/harness.sh --full` exits 0")
    if mode in ("loop", "both"):
        add("Plan", get(loop_secs, "Plan"), "- [ ] <next slice>")
    if mode in ("graph", "both"):
        if s["ir_block"]:
            body = s["ir_block"]
        else:
            body = ("<!-- MIGRATION: the previous GRAPH.md predates the IR and\n"
                    "     described its campaign in prose. Convert it to a\n"
                    "     ```json graph-ir block per the graph-engineering skill,\n"
                    "     then run: compile-graph.py WORK.md --check -->\n\n"
                    + (get(graph_secs, "Work graph") or "").strip())
        add("Campaign", body)
    add("Constraints", get(loop_secs, "Constraints"), "- <what must not change>")
    add("Merge policy", get(loop_secs, "Merge policy"), "- Auto-merge: <yes | no>.")

    ev = ["## Evidence",
          "Graph rows are appended by `scripts/record-run.py`; loop rows are "
          "written per slice.", "", UNIFIED_HDR, UNIFIED_SEP]
    ev += ["| " + " | ".join(r) + " |" for r in ev_rows]
    out.extend(ev + [""])
    out.extend(["## Notes for the next iteration", notes, ""])
    return "\n".join(out), len(ev_rows)


def rewire_gitignore(path):
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    kept = [l for l in lines if l.strip().rstrip("/") not in
            (".claude/fluxpoint-loop", ".claude/fluxpoint-graph")]
    # .claude/worktrees/ holds the isolated trees mutating nodes run in;
    # they are transient and must never be committed.
    for want in (f"{NEW_DIR}/", ".claude/worktrees/", "__pycache__/"):
        if want not in [l.strip() for l in kept]:
            kept.append(want)
    open(path, "w").write("\n".join(kept) + "\n")
    return kept


def rewire_settings(path):
    if not os.path.exists(path):
        return None
    d = json.load(open(path))
    ep = d.get("enabledPlugins")
    if isinstance(ep, dict):
        changed = False
        for old in ("fluxpoint-loop@fluxpoint", "fluxpoint-graph@fluxpoint"):
            if old in ep:
                del ep[old]
                changed = True
        if changed or "fluxpoint@fluxpoint" not in ep:
            ep["fluxpoint@fluxpoint"] = True
        d["enabledPlugins"] = ep
    json.dump(d, open(path, "w"), indent=2)
    open(path, "a").write("\n")
    return d.get("enabledPlugins")


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--finalize", action="store_true")
    args = ap.parse_args()
    root = args.root
    p = lambda *a: os.path.join(root, *a)
    s = survey(root)

    if not (s["loop"] or s["graph"]):
        print("migrate: no LOOP.md or GRAPH.md — nothing to migrate "
              "(this repo is already unified, or was never onboarded)")
        return 0

    if args.plan:
        print("migrate --plan")
        print(f"  sources        : {'LOOP.md ' if s['loop'] else ''}"
              f"{'GRAPH.md' if s['graph'] else ''}".strip())
        _, n = build_work(s)
        print(f"  WORK.md        : would be written, MODE "
              f"{'both' if s['loop'] and s['graph'] else ('graph' if s['graph'] else 'loop')}, "
              f"{n} Evidence row(s) carried")
        if s["graph"] and not s["graph_has_ir"]:
            print("  campaign       : GRAPH.md predates the IR — a graph-ir block "
                  "must be authored by hand; this script will not invent one")
        if s["old_prompt"]:
            print(f"  {OLD_PROMPT} : would be renamed to {NEW_PROMPT}")
        for d in s["old_dirs"]:
            print(f"  state          : {d} -> {NEW_DIR}")
        if s["workflows"]:
            print(f"  workflows      : {len(s['workflows'])} compiled script(s) "
                  f"would be removed (build output; recompile from WORK.md)")
        print("  deletions      : none in this phase (--finalize removes sources)")
        return 0

    if args.apply:
        if s["work"]:
            print("migrate: WORK.md already exists — refusing to overwrite. "
                  "Move it aside and re-run, or finish the migration by hand.",
                  file=sys.stderr)
            return 1
        text, n = build_work(s)
        open(p(WORK), "w").write(text)
        print(f"migrate --apply")
        print(f"  wrote {WORK} ({n} Evidence row(s) carried)")

        if s["old_prompt"] and not os.path.exists(p(NEW_PROMPT)):
            body = open(p(OLD_PROMPT)).read().replace(LOOP, WORK).replace(OLD_PROMPT, NEW_PROMPT)
            open(p(NEW_PROMPT), "w").write(body)
            os.remove(p(OLD_PROMPT))
            print(f"  renamed {OLD_PROMPT} -> {NEW_PROMPT} (references updated)")

        os.makedirs(p(NEW_DIR), exist_ok=True)
        for d in s["old_dirs"]:
            for item in os.listdir(p(d)):
                src, dst = p(d, item), p(NEW_DIR, item)
                if item in ("runs",) and os.path.isdir(src):
                    os.makedirs(dst, exist_ok=True)
                    for f in os.listdir(src):
                        shutil.move(os.path.join(src, f), os.path.join(dst, f))
                elif item.endswith((".dirty", ".blocks")):
                    continue  # per-session, expires on its own
                elif not os.path.exists(dst):
                    shutil.move(src, dst)
            print(f"  moved state {d} -> {NEW_DIR}")

        rewire_gitignore(p(".gitignore"))
        print("  rewired .gitignore")
        ep = rewire_settings(p(".claude/settings.json"))
        if ep is not None:
            print(f"  rewired enabledPlugins -> {sorted(ep)}")

        for f in s["workflows"]:
            os.remove(p(".claude/workflows", f))
        if s["workflows"]:
            print(f"  removed {len(s['workflows'])} compiled workflow script(s) "
                  f"(build output)")

        print(f"  sources left in place: verify, then run --finalize")
        if s["graph"] and not s["graph_has_ir"]:
            print("  ACTION REQUIRED: author the graph-ir block in WORK.md's "
                  "Campaign section before finalizing")
        return 0

    # --finalize
    if not s["work"]:
        print("migrate: no WORK.md — run --apply first", file=sys.stderr)
        return 1
    work_rows = len(table_rows(next((b for h, b in sections(open(p(WORK)).read())
                                     if h == "Evidence"), "")))
    src_rows = len(table_rows(next((b for h, b in sections(s["loop_text"])
                                    if h == "Evidence"), ""))) + \
               len(table_rows(next((b for h, b in sections(s["graph_text"])
                                    if h == "Evidence"), "")))
    if work_rows < src_rows:
        print(f"migrate: WORK.md carries {work_rows} Evidence row(s) but the "
              f"sources had {src_rows} — refusing to delete evidence",
              file=sys.stderr)
        return 1
    removed = []
    for f in (LOOP, GRAPH):
        if os.path.exists(p(f)):
            os.remove(p(f))
            removed.append(f)
    print(f"migrate --finalize: removed {', '.join(removed)} "
          f"({work_rows} Evidence row(s) preserved in {WORK})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
