"""Drive parse_stryker over the captured report, with one field optionally perturbed.

Kept as a file rather than inline heredocs: the shell suite already nests them
two deep, and a third level is where quoting stops being reviewable.

  stryker-probe.py <plugin-dir> <report.json> [status-swap FROM TO]
"""
import importlib.util
import json
import os
import sys

plugin, report = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("mg", os.path.join(plugin, "scripts", "mutation-guard.py"))
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)

doc = json.load(open(report, encoding="utf-8"))
if len(sys.argv) > 4:
    frm, to = sys.argv[3], sys.argv[4]
    for entry in doc["files"].values():
        for m in entry["mutants"]:
            if m["status"] == frm:
                m["status"] = to
                break
        break

m, f = mg.parse_stryker(doc)
print(json.dumps({"m": m, "f": f}))
