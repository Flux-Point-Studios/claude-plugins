#!/usr/bin/env python3
"""One STATUS vocabulary, or the templates and the commands drift apart.

`graph-run`'s preflight accepts READY or RUNNING and is told to stop and
report rather than improvise, so a work file whose STATUS the run gate does
not accept is a campaign that correctly refuses to start. Nothing asserted
that the files the plugin itself writes carry a status the plugin itself
accepts, and a migrated graph defaulted to ACTIVE -- a file the run gate
refuses, with the command that fixes it (`graph-design`, which flips DESIGN
to READY) named nowhere in the migrate flow.

The drift IS the bug, so this pins the vocabulary in both directions: every
status a shipped artifact can carry must be one some command accepts, and
every state in the documented lifecycle must have a producer.
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name:<54} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


def read(*parts):
    with open(os.path.join(PLUGIN, *parts), encoding="utf-8") as fh:
        return fh.read()


# The lifecycle, and who may hold each state.
LIFECYCLE = {"DESIGN", "READY", "RUNNING", "ACTIVE", "DONE"}
RUN_GATE = {"READY", "RUNNING"}

# ==================== every shipped template is in the vocabulary ========
tdir = os.path.join(PLUGIN, "templates")
statuses = {}
for fn in sorted(os.listdir(tdir)):
    if not (fn.startswith("WORK") and fn.endswith(".md")):
        continue
    m = re.search(r"^STATUS:\s*(\S+)\s*$", read("templates", fn), re.M)
    if m:
        statuses[fn] = m.group(1)

report("every shipped WORK template declares a STATUS",
       len(statuses) >= 4, f"{len(statuses)} template(s)")
unknown = {f: s for f, s in statuses.items() if s not in LIFECYCLE}
report("and every one is a state the lifecycle names",
       not unknown, "all known" if not unknown else str(unknown))

# A graph-shaped template must not ship in a state the run gate refuses
# UNLESS graph-design is the documented way out of it.
design_md = read("commands", "graph-design.md")
report("graph-design produces READY",
       "STATUS: READY" in design_md or "`READY`" in design_md, "produces READY")

graphish = {f: s for f, s in statuses.items() if f != "WORK.md"}
stuck = {f: s for f, s in graphish.items()
         if s not in RUN_GATE and s != "DESIGN"}
report("graph templates ship READY, RUNNING, or DESIGN",
       not stuck, "all runnable-or-designable" if not stuck else str(stuck))

# ==================== the run gate's vocabulary is the documented one ====
run_md = read("commands", "graph-run.md")
accepted = set(re.findall(r"`(DESIGN|READY|RUNNING|ACTIVE|DONE)`", run_md))
report("graph-run names the states it accepts",
       RUN_GATE <= accepted, ", ".join(sorted(accepted & LIFECYCLE)))

# ==================== migrate emits something the gate can reach =========
# The reported path: migrate a loop->graph repo and land at a status the run
# gate refuses. Executed against the real script, not grepped.
mig = os.path.join(PLUGIN, "scripts", "migrate.py")
got = {}
for label, files in (
        ("graph", {"GRAPH.md": "# GRAPH: audit the validators\nSTATUS: ACTIVE\n\n"
                               "## Definition of Done\n\n- [ ] a thing\n"}),
        ("loop", {"LOOP.md": "# LOOP: ship the path\nSTATUS: ACTIVE\n\n"
                             "## Definition of Done\n\n- [ ] a thing\n",
                  "LOOP_PROMPT.md": "Read LOOP.md in the repo root.\n"})):
    with tempfile.TemporaryDirectory() as d:
        for fn, body in files.items():
            with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        r = subprocess.run([sys.executable, mig, "--root", d, "--apply"],
                           capture_output=True, text=True)
        work = os.path.join(d, "WORK.md")
        if r.returncode == 0 and os.path.exists(work):
            m = re.search(r"^STATUS:\s*(\S+)\s*$",
                          open(work, encoding="utf-8").read(), re.M)
            got[label] = m.group(1) if m else "(none)"
        else:
            got[label] = f"rc={r.returncode} {r.stderr.strip()[:40]}"

report("a migrated graph lands in a state some command accepts",
       got.get("graph") in (RUN_GATE | {"DESIGN"}), got.get("graph"))
report("and a migrated loop still lands at ACTIVE",
       got.get("loop") == "ACTIVE", got.get("loop"))

# ==================== and the way out is named where the user is =========
report("migrate.md routes a graph to graph-design",
       "graph-design" in read("commands", "migrate.md"), "routed")
report("init.md names graph-design before graph-run",
       "graph-design" in read("commands", "init.md"), "named")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
