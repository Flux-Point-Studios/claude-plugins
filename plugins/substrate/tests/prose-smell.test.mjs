// The prose-smell gate: deterministic detection of the AI-writing frames that
// actually mark generated text (contrastive negation, whether-you're triples,
// assistant artifacts, stock LLM vocabulary) while leaving normal writing —
// metaphors, qualifiers, a reasonable em dash — alone. The harness decides
// whether a draft smells, never the author's self-report.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("../scripts/prose-smell.mjs", import.meta.url));

function runOnText(text, args = []) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-"));
  const file = path.join(dir, "draft.md");
  fs.writeFileSync(file, text);
  const r = spawnSync("node", [SCRIPT, ...args, file], { encoding: "utf8" });
  return { ...r, file };
}

function runHook(toolInput, toolName = "Write") {
  const payload = JSON.stringify({ tool_name: toolName, tool_input: toolInput });
  return spawnSync("node", [SCRIPT, "--hook"], { input: payload, encoding: "utf8" });
}

// ---------------------------------------------------------------- high rules
test("contrastive negation frames are high findings", () => {
  for (const line of [
    "Reconciliation isn't just about matching numbers — it's about trust.",
    "This work is not just about speed; it's about honesty.",
    "The pilot is more than just a proof of concept.",
    "Success is not merely about output, it's about outcomes.",
  ]) {
    const r = runOnText(line);
    assert.equal(r.status, 2, `expected high finding for: ${line}\n${r.stdout}${r.stderr}`);
    assert.match(r.stderr, /negation-contrast|more-than-just/);
  }
});

test("whether-you're inclusive triples are high findings", () => {
  const r = runOnText(
    "Whether you're an agent, a TM, or a state director, the wizard adapts.");
  assert.equal(r.status, 2);
  assert.match(r.stderr, /whether-your/);
});

test("assistant artifacts are high findings", () => {
  for (const line of [
    "Would you like me to add a summary table?",
    "I hope this helps! Reach out anytime.",
    "Let me know if you'd like a deeper breakdown.",
  ]) {
    const r = runOnText(line);
    assert.equal(r.status, 2, `expected artifact hit for: ${line}`);
    assert.match(r.stderr, /assistant-artifact/);
  }
});

test("stock LLM vocabulary is a high finding", () => {
  for (const line of [
    "Let's delve into the numbers.",
    "A rich tapestry of carrier relationships.",
    "In today's fast-paced insurance landscape, speed wins.",
    "This is a testament to the team's work.",
    "The broker plays a pivotal role in the flow.",
  ]) {
    const r = runOnText(line);
    assert.equal(r.status, 2, `expected stock-vocab hit for: ${line}`);
  }
});

// -------------------------------------------------------------- medium rules
test("sentence-initial from-to coverage parallelism is medium, not failing", () => {
  const r = runOnText(
    "From daily budgeting apps to sophisticated portfolios, money management is easier.");
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /from-to-coverage/);
});

test("em-dash density is reported as medium only when genuinely heavy", () => {
  const heavy = Array(6).fill(
    "The run — measured, not modeled — proved it — twice — cleanly.").join("\n");
  const r = runOnText(heavy);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /em-dash-density/);
  const light = "The run — measured, not modeled — proved it across two hundred and " +
    "forty statements yesterday, and the cache made the second pass free. " +
    "Nothing in the report needed a caveat beyond the snapshot date itself.";
  const r2 = runOnText(light);
  assert.doesNotMatch(r2.stdout, /em-dash-density/);
});

// ------------------------------------------------------- normal writing safe
test("normal writing with metaphors, qualifiers and a dash passes clean", () => {
  const r = runOnText(
    "The queue drained like a bathtub with the plug half out — slowly, then all " +
    "at once. It was remarkably difficult to verify the older scans, and the " +
    "results were slightly confusing until we checked the snapshot date. " +
    "Whether the carrier resends the file is their call, not ours.");
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), "");
});

test("code fences are never scanned", () => {
  const r = runOnText(
    "Here is the command:\n\n```js\n// isn't just about X — it's about Y\n" +
    "const s = \"Would you like me to\";\n```\n\nRun it once.");
  assert.equal(r.status, 0, r.stderr);
});

// ------------------------------------------------------------------ hook mode
test("hook mode scans prose writes and feeds findings back on exit 2", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-hook-"));
  const file = path.join(dir, "reply-draft.md");
  fs.writeFileSync(file, "This isn't just about OCR — it's about capacity.");
  const r = runHook({ file_path: file });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /negation-contrast/);
});

test("hook mode ignores non-prose files and generated registries", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-hook-"));
  const code = path.join(dir, "engine.mjs");
  fs.writeFileSync(code, "// isn't just about X — it's about Y");
  assert.equal(runHook({ file_path: code }).status, 0);
  const reg = path.join(dir, "SUBSTRATE.md");
  fs.writeFileSync(reg, "This isn't just about nodes — it's about edges.");
  assert.equal(runHook({ file_path: reg }).status, 0);
  assert.equal(runHook({}).status, 0);              // no file_path at all
  assert.equal(runHook({ file_path: path.join(dir, "gone.md") }).status, 0);
});

test("match-dense files stay inside the hook budget (the quadratic is dead)", () => {
  // Review bait: ~40k matches once took 20s via per-match slice-and-split
  // line counting. With O(n) offsets + binary search this finishes in well
  // under the 15s hook budget; the bound here is generous only for CI noise.
  const dense = Array(40_000).fill("This is more than just a line.").join("\n");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-perf-"));
  const file = path.join(dir, "dense.md");
  fs.writeFileSync(file, dense);
  const t0 = Date.now();
  const r = spawnSync("node", [SCRIPT, file], { encoding: "utf8", timeout: 14_000 });
  const elapsed = Date.now() - t0;
  assert.equal(r.status, 2, `expected HIGH findings, got ${r.status}: ${r.stderr.slice(0, 200)}`);
  assert.ok(elapsed < 10_000, `scan took ${elapsed}ms — the quadratic is back`);
});

test("finding floods are capped, not injected wholesale", () => {
  const flood = Array(500).fill("This is more than just noise.").join("\n");
  const r = runOnText(flood);
  assert.equal(r.status, 2);
  const lines = r.stderr.trim().split("\n");
  assert.ok(lines.length < 40, `expected capped output, got ${lines.length} lines`);
  assert.match(r.stderr, /more HIGH suppressed/);
});

test("a non-string file_path in hook JSON exits 0 silently", () => {
  for (const fp of [123, { a: 1 }, ["a.md"], null, true]) {
    const r = runHook({ file_path: fp });
    assert.equal(r.status, 0, `file_path=${JSON.stringify(fp)} must exit 0`);
    assert.equal(r.stderr.trim(), "", `no stack trace for ${JSON.stringify(fp)}`);
  }
});

test("the CLI path reports bad arguments in one line, never a stack trace", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-cli-"));
  const missing = path.join(dir, "missing.md");
  const r = spawnSync("node", [SCRIPT, missing, dir], { encoding: "utf8" });
  assert.equal(r.status, 0);
  assert.match(r.stderr, /not a readable file/);
  assert.doesNotMatch(r.stderr, /at .*\(node:/);
});

test("oversized files are skipped, not scanned", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-big-"));
  const file = path.join(dir, "huge.md");
  fs.writeFileSync(file,
    "This isn't just about size — it's about budget.\n".padEnd(2_100_000, "x"));
  const r = runHook({ file_path: file });
  assert.equal(r.status, 0);
  assert.equal(r.stderr.trim(), "");
});

test("clean prose through the hook is silent", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "smell-hook-"));
  const file = path.join(dir, "note.md");
  fs.writeFileSync(file, "The deploy finished at 4pm and the gate held.");
  const r = runHook({ file_path: file });
  assert.equal(r.status, 0);
  assert.equal(r.stderr.trim(), "");
});
