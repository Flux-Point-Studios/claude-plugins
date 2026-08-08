#!/usr/bin/env bash
# SessionStart hook. Stdout is injected as context Claude can read; content
# is phrased as factual statements because imperative "system command"
# phrasing can trip prompt-injection defenses and surface the text to the
# user instead. Runs on startup, resume, clear, and post-compaction.
set -u

# Interpreter name differs by platform: `python3` on Linux/macOS, `python` on a
# standard Windows install. Resolve once rather than hardcoding either.
if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
# Force UTF-8 on every embedded interpreter's stdio. Without it Windows writes
# cp1252, so a header like "## Plan --" emitted with an em-dash comes back as
# 0x97 and every consumer that greps for the UTF-8 bytes silently misses it.
export PYTHONIOENCODING=utf-8

[ "${FPL_DISABLE:-0}" = "1" ] && exit 0
here="${0%/*}"; [ "$here" = "$0" ] && here=.
. "$here/lib.sh"

input="$(cat)"
proj="${CLAUDE_PROJECT_DIR:-$(printf '%s' "$input" | fpl_json_get cwd)}"
cd "${proj:-.}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

sd="$(fpl_state_dir)"
find "$sd" -maxdepth 1 \( -name '*.dirty' -o -name '*.blocks' \) -mtime +3 -delete 2>/dev/null

branch="$(git branch --show-current 2>/dev/null)"
dirtyn="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

echo "Flux Point work context, generated $(date -u +%FT%TZ):"
echo "- Branch: ${branch:-detached}; uncommitted changes: ${dirtyn} path(s)."

verdict=""
for d in "$sd" "$(fpl_legacy_state_dir)"; do
  [ -f "$d/last-harness" ] && { verdict="$(cat "$d/last-harness")"; break; }
done
if [ -n "$verdict" ]; then
  echo "- Last recorded harness verdict: ${verdict}."
else
  echo "- No harness verdict has been recorded in this repo yet."
fi

if [ -f scripts/harness.sh ]; then
  echo "- Harness: scripts/harness.sh is present. A Stop-hook DoD gate runs '--full' plus a hygiene scan whenever code changed this session; red results block the stop, up to ${FPL_MAX_BLOCKS:-3} consecutive times, after which the gate yields with a checkpoint notice. Green is the only clean exit."
else
  echo "- Harness: scripts/harness.sh is absent, so the DoD gate is dormant in this repo. The /fluxpoint:init command scaffolds it."
fi

# What is waiting on a person, first. A fresh context is re-oriented with
# the goal and the Definition of Done but not with what is blocking, which
# is how a parked campaign sits unnoticed for days.
inbox_py="$(dirname "$0")/inbox.py"
if [ -f "$inbox_py" ]; then
  open_items="$("$FPL_PY" "$inbox_py" --count 2>/dev/null || echo 0)"
  if [ "${open_items:-0}" -gt 0 ] 2>/dev/null; then
    echo "- BLOCKED ON YOU: ${open_items} item(s) waiting on a person. Run /fluxpoint:status for the list, /fluxpoint:release <node> to clear one."
  fi
fi

# A gate claim the attest log contradicts makes a run's verdict worthless,
# so it outranks everything below it except what is blocked on a person.
if [ -f .fluxpoint-gates.json ] && [ -f "$sd/attest.jsonl" ]; then
  mm="$("$FPL_PY" - "$sd/runs" <<'PY' 2>/dev/null || true
import json, os, sys
d = sys.argv[1]
n = 0
for fn in os.listdir(d) if os.path.isdir(d) else []:
    if not fn.endswith(".json"):
        continue
    try:
        with open(os.path.join(d, fn), encoding="utf-8") as fh:
            a = json.load(fh)
    except Exception:
        continue
    n += ((a.get("attestation") or {}).get("tally", {}) or {}).get("mismatch", 0)
print(n)
PY
)"
  if [ "${mm:-0}" -gt 0 ] 2>/dev/null; then
    echo "- ATTESTATION MISMATCH: ${mm} recorded gate claim(s) contradict .claude/fluxpoint/attest.jsonl, which is the hook-minted record of what those commands actually exited. Those runs' verdicts are not trustworthy; /fluxpoint:status lists them."
  fi
fi

# Latest graph run, if this repo runs campaigns.
runs="$sd/runs"
if [ -d "$runs" ]; then
  last="$(ls -1t "$runs"/*.json 2>/dev/null | head -1)"
  if [ -n "$last" ]; then
    echo "- Last graph run: $(fpl_run_summary "$last")."
  fi
fi

state="$(fpl_state_file || true)"
if [ -n "$state" ]; then
  # A fixed line window is the wrong shape for this file. In the shipped
  # 90-line template the Evidence header sits at 85 and Notes at 88, and a
  # real campaign is worse — the graph-ir block alone can run 30+ lines. So
  # record-run.py did the hard part correctly and the bootstrap never showed
  # it: a fresh context was re-oriented with the goal and the Definition of
  # Done but not with what was proven or what is blocking.
  echo "- ${state} (sections that matter, newest evidence first):"
  "$FPL_PY" - "$state" <<'PY'
import re, sys

text = open(sys.argv[1], errors="replace").read()
lines = text.splitlines()
out, elided = [], []
BUDGET = 90  # lines emitted, not lines scanned


def section(name):
    """Body lines of '## name', up to the next header."""
    body, inside = [], False
    for ln in lines:
        if re.match(r"^##+\s", ln):
            if inside:
                break
            inside = ln.strip().lower().startswith(f"## {name.lower()}")
            continue
        if inside:
            body.append(ln)
    return [b for b in body if b.strip()]


for ln in lines[:12]:
    if re.match(r"^(STATUS|MODE):", ln.strip()):
        out.append(ln.strip())

# Open work only, and blocked items carry who they are blocked on — the
# loop skips `[~]`, so hiding them here would hide why nothing is moving.
plan = [b for b in section("Plan") if re.match(r"^\s*-\s*\[( |~)\]", b)]
if plan:
    out.append("")
    out.append(f"## Plan — {len(plan)} open item(s)")
    out += [p.rstrip() for p in plan[:12]]
    if len(plan) > 12:
        elided.append(f"{len(plan) - 12} more Plan item(s)")

dod = [b for b in section("Definition of Done") if re.match(r"^\s*-\s*\[ \]", b)]
if dod:
    out.append("")
    out.append(f"## Definition of Done — {len(dod)} unmet")
    out += [d.rstrip() for d in dod[:8]]
    if len(dod) > 8:
        elided.append(f"{len(dod) - 8} more unmet DoD item(s)")

for name, keep in (("Decisions", 3), ("Evidence", 5)):
    rows = [b for b in section(name)
            if b.strip().startswith("|") and not re.match(r"^\|[-| ]+\|$", b.strip())]
    rows = [r for r in rows[1:]]  # drop the header row itself
    if rows:
        out.append("")
        out.append(f"## {name} — newest {min(keep, len(rows))} of {len(rows)}")
        out += [r.rstrip() for r in rows[:keep]]
        if len(rows) > keep:
            elided.append(f"{len(rows) - keep} older {name} row(s)")

notes = section("Notes for the next iteration") or section("Notes for the next run")
if notes:
    out.append("")
    out.append("## Notes")
    out += [n.rstrip() for n in notes[:10]]

if len(out) > BUDGET:
    elided.append(f"{len(out) - BUDGET} further line(s)")
    out = out[:BUDGET]
print("\n".join(out) if out else "(no recognizable sections; showing nothing "
                                 "rather than a truncated head)")
if elided:
    # Named, because silent truncation is how a bootstrap reads as complete.
    print(f"[elided: {'; '.join(elided)} — read {sys.argv[1]} directly]")
PY
  if [ "$state" = "LOOP.md" ]; then
    echo "- Note: LOOP.md is the pre-1.0 state file and is still honored. /fluxpoint:migrate folds it into WORK.md, which carries one Definition of Done and one Evidence table for both loop slices and graph campaigns."
  fi
fi
exit 0
