// memory-lint checks the workspace's memory files against ground truth it can
// verify deterministically: cited paths that no longer exist, index entries
// pointing at deleted memories, and "unsent" claims that deliverables.json
// says were sent. It flags; it never rewrites — the agent owns the repair.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("../scripts/memory-lint.mjs", import.meta.url));

function makeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "memlint-ws-"));
  const memDir = fs.mkdtempSync(path.join(os.tmpdir(), "memlint-mem-"));
  fs.mkdirSync(path.join(root, "sca-kpi", "etl"), { recursive: true });
  fs.writeFileSync(path.join(root, "sca-kpi", "etl", "real.py"), "x = 1\n");
  return { root, memDir };
}

function writeMemory(memDir, name, body) {
  const file = path.join(memDir, `${name}.md`);
  fs.writeFileSync(
    file,
    `---\nname: ${name}\ndescription: fixture\nmetadata:\n  type: project\n---\n\n${body}\n`
  );
  return file;
}

function run({ root, memDir }) {
  const res = spawnSync(process.execPath, [SCRIPT, "--root", root, "--dir", memDir], {
    encoding: "utf8",
  });
  return { code: res.status, out: res.stdout ?? "", err: res.stderr ?? "" };
}

test("an existing cited path stays silent", () => {
  const fx = makeFixture();
  writeMemory(fx.memDir, "good", "The module lives at `sca-kpi/etl/real.py` today.");
  const { code, out } = run(fx);
  assert.equal(code, 0);
  assert.ok(!out.includes("ALARM"), out);
  assert.match(out, /memory-lint: 1 memor/);
});

test("a missing path under an existing anchor raises an ALARM naming memory and path", () => {
  const fx = makeFixture();
  writeMemory(fx.memDir, "stale", "See `sca-kpi/etl/gone.py` for the loader.");
  const { code, out } = run(fx);
  assert.equal(code, 0, "lint must never block session start");
  assert.match(out, /ALARM: memory-lint: stale cites missing path: sca-kpi\/etl\/gone\.py/);
});

test("unanchored paths, URLs, globs, and flagged commands stay silent", () => {
  const fx = makeFixture();
  writeMemory(
    fx.memDir,
    "noisy",
    [
      "Run `node broker/cli.js demo` then `uv run --with pytest python -m pytest tests -q`.",
      "Docs at `https://example.com/a/b` and pattern `data/*.local/*.json`.",
      "Repo-relative elsewhere: `etl/unknown_anchor.py`.",
      "API endpoints: `/transactions` and `/drives/x/items` are not files.",
      "Dot-relative in some repo: `./scripts/demo.sh` has no workspace anchor.",
    ].join("\n")
  );
  const { code, out } = run(fx);
  assert.equal(code, 0);
  assert.ok(!out.includes("ALARM"), out);
});

test("a MEMORY.md index entry pointing at a deleted memory file alarms", () => {
  const fx = makeFixture();
  fs.writeFileSync(
    path.join(fx.memDir, "MEMORY.md"),
    "# Memory Index\n\n- [Ghost](ghost-memory.md) — points nowhere\n"
  );
  const { out } = run(fx);
  assert.match(out, /ALARM: memory-lint: MEMORY\.md indexes missing file: ghost-memory\.md/);
});

test("an 'unsent' claim contradicted by deliverables.json is a NOTE, not an ALARM", () => {
  const fx = makeFixture();
  fs.mkdirSync(path.join(fx.root, "notes"));
  fs.writeFileSync(
    path.join(fx.root, "notes", "deliverables.json"),
    JSON.stringify({
      deliverables: [
        { id: "widget-note", sentAt: "2026-08-20T12:00:00Z" },
        { id: "other-note", sentAt: null },
      ],
    })
  );
  writeMemory(
    fx.memDir,
    "contra",
    "The widget-note reply is drafted but NOT sent yet.\nAlso other-note is unsent."
  );
  const { out } = run(fx);
  assert.match(out, /NOTE: memory-lint: contra says unsent, but 'widget-note' was sent 2026-08-20/);
  assert.ok(!out.includes("other-note"), "an actually-unsent deliverable is not a contradiction");
  assert.ok(!out.match(/ALARM.*widget-note/), "contradictions are notes, alarms are for hard facts");
});

test("control characters in memory content never reach stdout", () => {
  const fx = makeFixture();
  writeMemory(fx.memDir, "hostile", "See `sca-kpi/etl/" + String.fromCharCode(27) + "[31mgone" + String.fromCharCode(27) + "[0m.py` now.");
  const { out } = run(fx);
  assert.ok(out.includes("ALARM"), "the missing path is still reported");
  assert.ok(!out.includes(String.fromCharCode(27)), "escape bytes must be stripped from echoed content");
});

test("a missing memory dir is a silent success", () => {
  const fx = makeFixture();
  fs.rmSync(fx.memDir, { recursive: true, force: true });
  const { code, out } = run(fx);
  assert.equal(code, 0);
  assert.equal(out.trim(), "");
});
