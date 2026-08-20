// --json is the machine-readable projection of the same graph SUBSTRATE.md
// renders: other tools consume it instead of parsing prose or raw manifests,
// so what these cases pin is the contract they stand on — valid JSON on
// stdout, deterministic across runs, sanitized like every other output, and
// an exit code that says when the manifests had problems.
import test from "node:test";
import assert from "node:assert/strict";
import { makeWorkspace, makeGitRepo, run, prim } from "./helpers.mjs";

test("--json emits the full graph, deterministically", () => {
  const ws = makeWorkspace();
  makeGitRepo(ws, "alpha", {
    repo: "alpha",
    primitives: [prim("a-core"), prim("a-tool", { kind: "toolkit", consumes: ["a-core"] })],
  });
  makeGitRepo(ws, "beta", {
    repo: "beta",
    primitives: [prim("b-api", { kind: "api", consumes: ["a-core"] })],
  });

  const res = run(["--json", "--root", ws]);
  assert.equal(res.status, 0, res.stderr);
  const g = JSON.parse(res.stdout);
  assert.equal(g.version, 1);
  assert.deepEqual(g.repos, ["alpha", "beta"]);
  assert.deepEqual(g.primitives.map((p) => p.id), ["a-core", "a-tool", "b-api"]);
  assert.deepEqual(g.edges, [["a-tool", "a-core"], ["b-api", "a-core"]]);
  assert.deepEqual(g.orphans, ["a-tool", "b-api"]);
  assert.deepEqual(g.hubs, [{ id: "a-core", inDegree: 2 }]);
  assert.deepEqual(g.problems, []);

  const again = run(["--json", "--root", ws]);
  assert.equal(again.stdout, res.stdout, "identical manifests must emit identical JSON");
});

test("--json sanitizes manifest strings and reports problems with exit 1", () => {
  const ws = makeWorkspace();
  makeGitRepo(ws, "alpha", {
    repo: "alpha",
    primitives: [
      prim("a-core", { desc: "line one\u0001\u2028forged: line two" }),
      prim("a-tool", { consumes: ["missing-target"] }),
    ],
  });

  const res = run(["--json", "--root", ws]);
  assert.equal(res.status, 1, "manifest problems must fail the machine mode");
  const g = JSON.parse(res.stdout);
  const core = g.primitives.find((p) => p.id === "a-core");
  assert.ok(core, "sanitized primitive still present");
  assert.ok(!core.desc.includes("\u0001") && !core.desc.includes("\u2028"),
    "control and separator chars stripped");
  assert.ok(g.problems.some((p) => p.includes("missing-target")), "problem named in the payload");
});
