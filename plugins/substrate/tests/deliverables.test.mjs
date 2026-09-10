// The deliverables ledger: deliverables.json beside (or without) substrate.json.
// Task boards and scratchpads that hold "built but not yet sent" state do not
// survive context compaction; this file does, and --check surfaces every open
// obligation at session start.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { makeWorkspace, writeManifest, run, prim } from "./helpers.mjs";

const HOUR = 3600 * 1000;

function has(text, needle) {
  assert.ok(text.includes(needle), `expected output to contain:\n${needle}\n--- got ---\n${text}`);
}

function writeLedger(root, dir, ledger) {
  const d = path.join(root, dir);
  fs.mkdirSync(d, { recursive: true });
  const f = path.join(d, "deliverables.json");
  fs.writeFileSync(f, JSON.stringify(ledger, null, 2) + "\n");
  return f;
}

test("an unsent deliverable alarms at --check, sent ones stay silent", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "repoA", { repo: "repoA", primitives: [prim("a-core")] });
  writeLedger(ws, "repoA", {
    deliverables: [
      { id: "vendor-bundle", recipient: "Avery", artifact: "bundle-2026-08-10.zip",
        builtAt: new Date(Date.now() - 26 * HOUR).toISOString() },
      { id: "sarah-reply", recipient: "Sarah",
        builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        sentAt: new Date(Date.now() - 29 * HOUR).toISOString() },
    ],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  has(res.stdout, "ALARM: UNSENT deliverable: repoA/vendor-bundle for Avery — built 26h ago (bundle-2026-08-10.zip)");
  assert.ok(!res.stdout.includes("sarah-reply"), `sent deliverables are not obligations:\n${res.stdout}`);
});

test("a ledger alarms even in a repo with no substrate manifest", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "notes-only", {
    deliverables: [{ id: "exec-brief", recipient: "Andrew",
      builtAt: new Date(Date.now() - 72 * HOUR).toISOString() }],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  has(res.stdout, "ALARM: UNSENT deliverable: notes-only/exec-brief for Andrew — built 3d ago");
});

test("excluded dirs are never scanned for ledgers", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "node_modules", {
    deliverables: [{ id: "phantom", recipient: "nobody", builtAt: new Date().toISOString() }],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  assert.ok(!res.stdout.includes("phantom"), `excludeDirs must apply to ledgers too:\n${res.stdout}`);
});

test("malformed ledgers and undated entries are problems, never crashes or alarms", () => {
  const ws = makeWorkspace();
  const d = path.join(ws, "badrepo");
  fs.mkdirSync(d);
  fs.writeFileSync(path.join(d, "deliverables.json"), "not json at all\n");
  writeLedger(ws, "shaperepo", { deliverables: [{ id: "undated", recipient: "X" }] });
  writeLedger(ws, "arrayrepo", [{ id: "bare-array" }]);
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  has(res.stdout, "PROBLEM: unreadable ledger: badrepo/deliverables.json");
  has(res.stdout, 'PROBLEM: invalid deliverable undated in shaperepo: "builtAt" must be a parseable date');
  has(res.stdout, 'PROBLEM: invalid ledger: arrayrepo/deliverables.json needs a "deliverables" array');
  assert.ok(!res.stdout.includes("ALARM:"), `malformed entries must not alarm:\n${res.stdout}`);
});

test("ledger strings are sanitized and capped — the output is injected session context", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "hostile", {
    deliverables: [{
      id: "evil\u001b[31mred\u0007" + "x".repeat(500),
      recipient: "Bob\u0000\u001b[2J",
      builtAt: new Date(Date.now() - 2 * HOUR).toISOString(),
    }],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  assert.ok(!res.stdout.includes("\u001b"), "no escape bytes reach the session context");
  assert.ok(!res.stdout.includes("\u0007"), "no bell bytes either");
  has(res.stdout, "…"); // the 500-char id was capped
});

test("an obligation that ended without a send closes on its reason, not on a fake sentAt", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "repoC", {
    deliverables: [
      { id: "reshaped-preview", recipient: "Jordan", artifact: "preview.html",
        builtAt: new Date(Date.now() - 10 * 24 * HOUR).toISOString(),
        sentAt: null,
        closedAt: "2026-08-20T00:00:00Z",
        closedReason: "superseded",
        closedBecause: "reshaped into a mailbox lane she tests through; the preview send no longer exists" },
    ],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  assert.ok(!res.stdout.includes("reshaped-preview"), `a reasoned closure is quiet:\n${res.stdout}`);
  assert.ok(!res.stdout.includes("PROBLEM"), `a well-formed closure is not a problem:\n${res.stdout}`);
});

test("a closure with no reason or no explanation keeps alarming and says what it needs", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "repoD", {
    deliverables: [
      { id: "no-reason", recipient: "Andrew", builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        closedAt: "2026-08-20T00:00:00Z" },
      { id: "no-because", recipient: "Andrew", builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        closedAt: "2026-08-20T00:00:00Z", closedReason: "withdrawn" },
      { id: "made-up-reason", recipient: "Andrew", builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        closedAt: "2026-08-20T00:00:00Z", closedReason: "vibes", closedBecause: "felt done" },
    ],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  for (const id of ["no-reason", "no-because", "made-up-reason"]) {
    has(res.stdout, `PROBLEM: deliverable ${id} in repoD: "closedAt" needs a "closedReason" of superseded/withdrawn/answered-elsewhere and a "closedBecause" saying why`);
    has(res.stdout, `ALARM: UNSENT deliverable: repoD/${id}`);
  }
});

test("an entry cannot be both sent and closed-without-a-send", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "repoE", {
    deliverables: [
      { id: "both", recipient: "Joe", builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        sentAt: "2026-08-18T15:00:00Z", closedAt: "2026-08-18T15:00:00Z",
        closedReason: "answered-elsewhere", closedBecause: "delivered in the 1:1" },
    ],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  has(res.stdout, 'PROBLEM: deliverable both in repoE: has both "sentAt" and "closedAt" — a send and a non-send closure cannot both be true');
});

test("a warning rides the alarm so a stale artifact is never sent cold", () => {
  const ws = makeWorkspace();
  writeLedger(ws, "repoF", {
    deliverables: [
      { id: "stale-memo", recipient: "Andrew", artifact: "memo.md",
        builtAt: new Date(Date.now() - 30 * HOUR).toISOString(),
        warning: "the carrier paragraph was refuted 8/15 — rewrite before sending" },
    ],
  });
  const res = run(["--check", "--root", ws]);
  assert.equal(res.status, 0);
  has(res.stdout, "ALARM: UNSENT deliverable: repoF/stale-memo for Andrew — built 30h ago (memo.md) ⚠ DO NOT SEND COLD: the carrier paragraph was refuted 8/15 — rewrite before sending");
});

test("emit writes unsent deliverables into SUBSTRATE.md's alarm section", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "repoB", { repo: "repoB", primitives: [prim("b-core")] });
  writeLedger(ws, "repoB", {
    deliverables: [{ id: "weekly-report", recipient: "Nick",
      builtAt: new Date(Date.now() - 5 * HOUR).toISOString() }],
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0);
  const md = fs.readFileSync(path.join(ws, "SUBSTRATE.md"), "utf8");
  has(md, "UNSENT deliverable: repoB/weekly-report for Nick");
});
