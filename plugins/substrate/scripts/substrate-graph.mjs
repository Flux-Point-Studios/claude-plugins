#!/usr/bin/env node
// substrate-graph.mjs — generates SUBSTRATE.md from per-repo substrate.json manifests.
// Zero dependencies, Node >= 18, cross-platform. Deterministic output (sorted; no
// timestamps in the body).
//
//   node substrate-graph.mjs --emit  [--root <dir>]   write <root>/SUBSTRATE.md; exit 1 on manifest problems
//   node substrate-graph.mjs --check [--root <dir>]   compact summary + staleness; ALWAYS exits 0
//
// Root resolution: --root arg, else CLAUDE_PROJECT_DIR, else cwd.
// --check stdout is injected as session context by the SessionStart hook, so every
// manifest-derived string is validated + control-char-stripped + length-capped
// before it can reach stdout or the committed SUBSTRATE.md: a manifest a third
// party drops into the workspace root (a vendored dir, an unpacked tarball, a
// clone) must not be able to forge output lines, forge a reassuring "all current"
// line, forge a <system-reminder> block, or flood the context window.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const DEFAULT_EXCLUDE_DIRS = ["node_modules", ".git", "dist", "build", "out", "coverage", "__pycache__"];
const WALK_MAX_DEPTH = 6;
const WALK_MAX_ENTRIES = 5000;
const GIT_MAX_BUFFER = 64 * 1024 * 1024;
const STRING_CAP = 2000; // one manifest string is one line/cell; long is fine, unbounded is not
const LIST_CAP = 50; // printed problem/alarm/note lines before "N more suppressed"

// control chars (C0 + DEL) and the Unicode line/paragraph separators, so a
// manifest string can never break out of its line into forged output
const CONTROL_CHARS = new RegExp("[\u0000-\u001f\u007f-\u009f\u2028\u2029]", "g");

function clean(value, max = STRING_CAP) {
  const s = String(value).replace(CONTROL_CHARS, " ");
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function capList(list, max = LIST_CAP) {
  if (list.length <= max) return list;
  return [...list.slice(0, max), `…and ${list.length - max} more suppressed`];
}

function resolveRoot(argv) {
  const i = argv.indexOf("--root");
  if (i !== -1) {
    if (!argv[i + 1]) throw new Error("--root requires a directory argument");
    return path.resolve(argv[i + 1]);
  }
  if (process.env.CLAUDE_PROJECT_DIR) return path.resolve(process.env.CLAUDE_PROJECT_DIR);
  return process.cwd();
}

function loadConfig(root, problems) {
  const config = { graceHours: 6, excludeDirs: new Set(DEFAULT_EXCLUDE_DIRS), nonGitRepos: Object.create(null) };
  const cf = path.join(root, "substrate.config.json");
  if (!fs.existsSync(cf)) return config;
  try {
    const parsed = JSON.parse(fs.readFileSync(cf, "utf8"));
    if (typeof parsed.graceHours === "number") config.graceHours = parsed.graceHours;
    if (Array.isArray(parsed.excludeDirs)) for (const d of parsed.excludeDirs) config.excludeDirs.add(String(d));
    if (parsed.nonGitRepos && typeof parsed.nonGitRepos === "object") {
      for (const [dir, rels] of Object.entries(parsed.nonGitRepos)) {
        if (dir === "__proto__" || dir === "constructor" || dir === "prototype") continue;
        if (Array.isArray(rels)) config.nonGitRepos[dir] = rels.map(String);
      }
    }
  } catch (e) {
    problems.push(`unreadable config: substrate.config.json (${clean(e.message)})`);
  }
  return config;
}

function isStr(x) {
  return typeof x === "string";
}

function strArray(x) {
  return Array.isArray(x) && x.every(isStr);
}

function validateManifest(parsed, dir, problems) {
  // Reject a malformed/hostile manifest as a named problem rather than letting
  // it crash the run or override internal fields — the returned object carries
  // ONLY validated, sanitized data; the caller supplies dir/manifest/mtimeMs.
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    problems.push(`invalid manifest: ${clean(dir)}/substrate.json is not a JSON object`);
    return null;
  }
  if (!isStr(parsed.repo)) {
    problems.push(`invalid manifest: ${clean(dir)}/substrate.json needs a string "repo"`);
    return null;
  }
  if (parsed.primitives !== undefined && !Array.isArray(parsed.primitives)) {
    problems.push(`invalid manifest: ${clean(parsed.repo)} field "primitives" is not an array`);
    return null;
  }
  const repo = clean(parsed.repo);
  const primitives = [];
  for (const p of parsed.primitives || []) {
    if (!p || typeof p !== "object" || !isStr(p.id)) {
      problems.push(`invalid primitive in ${repo}: each needs a string "id"`);
      continue;
    }
    if (![p.kind, p.status, p.desc].every(isStr) || !strArray(p.paths) || !strArray(p.consumes || [])) {
      problems.push(`invalid primitive ${clean(p.id)} in ${repo}: kind/status/desc must be strings, paths/consumes string arrays`);
      continue;
    }
    primitives.push({
      id: clean(p.id),
      kind: clean(p.kind),
      status: clean(p.status),
      desc: clean(p.desc),
      paths: p.paths.map((x) => clean(x)),
      consumes: (p.consumes || []).map((x) => clean(x)),
    });
  }
  return { repo, primitives };
}

function loadManifests(root, excludeDirs, problems) {
  const repos = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!entry.isDirectory()) continue;
    if (excludeDirs.has(entry.name)) continue; // never ingest manifests from node_modules/.git/vendored build dirs
    const mf = path.join(root, entry.name, "substrate.json");
    if (!fs.existsSync(mf)) continue; // manifest-less repos are demos/synthetic by doctrine — never alarmed on
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(mf, "utf8"));
    } catch (e) {
      problems.push(`unreadable manifest: ${clean(entry.name)}/substrate.json (${clean(e.message)})`);
      continue;
    }
    const v = validateManifest(parsed, entry.name, problems);
    if (!v) continue;
    // internal fields are set here, never from the manifest — a manifest cannot
    // override dir/mtimeMs to steer git -C outside the repo or backdate staleness
    repos.push({ repo: v.repo, primitives: v.primitives, dir: entry.name, manifest: mf, mtimeMs: fs.statSync(mf).mtimeMs });
  }
  return repos;
}

function buildGraph(repos, problems) {
  const nodes = new Map(); // id -> { ...primitive, repo }
  for (const r of repos) {
    for (const p of r.primitives || []) {
      if (nodes.has(p.id)) problems.push(`duplicate primitive id: ${p.id} (${nodes.get(p.id).repo} and ${r.repo})`);
      nodes.set(p.id, { ...p, repo: r.repo });
    }
  }
  const edges = []; // [from, to] = from consumes to
  for (const n of nodes.values()) {
    for (const target of n.consumes || []) {
      if (!nodes.has(target)) problems.push(`unknown consumes target: ${n.id} -> ${target}`);
      else edges.push([n.id, target]);
    }
  }
  edges.sort((a, b) => a[0].localeCompare(b[0]) || a[1].localeCompare(b[1]));
  const inDegree = new Map([...nodes.keys()].map((id) => [id, 0]));
  const consumers = new Map([...nodes.keys()].map((id) => [id, []]));
  for (const [from, to] of edges) {
    inDegree.set(to, inDegree.get(to) + 1);
    consumers.get(to).push(from);
  }
  const ids = [...nodes.keys()].sort();
  const orphans = ids.filter((id) => inDegree.get(id) === 0);
  const hubs = ids
    .filter((id) => inDegree.get(id) >= 2)
    .sort((a, b) => inDegree.get(b) - inDegree.get(a) || a.localeCompare(b));
  return { nodes, edges, inDegree, consumers, orphans, hubs };
}

function newestUnder(base, rel, sinceMs, excludeDirs) {
  // { newestMs, newerCount, capped, escaped } for files under base/rel; capped
  // means the depth or entry limit was hit (counts are a lower bound); escaped
  // means rel resolved outside base and was refused.
  let newestMs = 0, newerCount = 0, entries = 0, capped = false;
  const start = path.join(base, rel);
  const within = path.relative(base, start);
  if (within.startsWith("..") || path.isAbsolute(within)) return { newestMs, newerCount, capped, escaped: true };
  if (!fs.existsSync(start)) return { newestMs, newerCount, capped, escaped: false };
  const stack = [{ p: start, depth: 0 }];
  while (stack.length) {
    if (entries >= WALK_MAX_ENTRIES) {
      capped = true;
      break;
    }
    const { p: cur, depth } = stack.pop();
    entries++;
    const st = fs.statSync(cur, { throwIfNoEntry: false }); // broken symlinks resolve to nothing
    if (!st) continue;
    if (st.isDirectory()) {
      if (excludeDirs.has(path.basename(cur))) continue;
      if (depth >= WALK_MAX_DEPTH) {
        capped = true;
        continue;
      }
      for (const child of fs.readdirSync(cur)) stack.push({ p: path.join(cur, child), depth: depth + 1 });
    } else {
      if (path.basename(cur) === "substrate.json") continue;
      if (st.mtimeMs > newestMs) newestMs = st.mtimeMs;
      if (st.mtimeMs > sinceMs) newerCount++;
    }
  }
  return { newestMs, newerCount, capped, escaped: false };
}

function git(repoDir, args) {
  return execFileSync("git", ["-C", repoDir, ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    maxBuffer: GIT_MAX_BUFFER,
  }).trim();
}

function gitAvailable() {
  try {
    execFileSync("git", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function samePath(a, b) {
  // git prints forward slashes; realpath both sides so symlinked temp dirs
  // (macOS /var -> /private/var) and Windows casing cannot break the compare.
  const canon = (p) => {
    const r = fs.realpathSync(path.resolve(p));
    return process.platform === "win32" ? r.toLowerCase() : r;
  };
  return canon(a) === canon(b);
}

function isGitWorkTree(dir) {
  // rev-parse handles worktrees and gitfile setups; the toplevel compare stops
  // a plain subdir from inheriting an ANCESTOR repo's work tree.
  try {
    if (git(dir, ["rev-parse", "--is-inside-work-tree"]) !== "true") return false;
    return samePath(git(dir, ["rev-parse", "--show-toplevel"]), dir);
  } catch {
    return false;
  }
}

function stalenessAlarms(repos, root, config) {
  const alarms = [];
  const notes = [];
  const graceMs = config.graceHours * 60 * 60 * 1000;
  const gitOk = gitAvailable();
  let gitSkipped = false;
  for (const r of [...repos].sort((a, b) => a.repo.localeCompare(b.repo))) {
    try {
      const repoDir = path.join(root, r.dir);
      const declared = Object.prototype.hasOwnProperty.call(config.nonGitRepos, r.dir);
      if (gitOk && isGitWorkTree(repoDir)) {
        const lastCommitMs = parseInt(git(repoDir, ["log", "-1", "--format=%ct"]), 10) * 1000;
        if (lastCommitMs > r.mtimeMs + graceMs) {
          const files = new Set(
            git(repoDir, ["log", `--since=@${Math.floor(r.mtimeMs / 1000)}`, "--name-only", "--pretty=format:"])
              .split("\n").map((s) => s.trim()).filter((s) => s && s !== "substrate.json"),
          );
          // only alarm when a non-manifest file actually changed (a lone re-commit of substrate.json is not drift)
          if (files.size) alarms.push(`manifest may be stale: ${r.repo} changed ${files.size} files since manifest`);
        }
      } else if (declared) {
        let newestMs = 0, newerCount = 0, capped = false, escaped = false;
        for (const rel of config.nonGitRepos[r.dir]) {
          const res = newestUnder(repoDir, rel, r.mtimeMs, config.excludeDirs);
          if (res.newestMs > newestMs) newestMs = res.newestMs;
          newerCount += res.newerCount;
          capped = capped || res.capped;
          escaped = escaped || res.escaped;
        }
        if (escaped) notes.push(`config path escaped ${r.repo}'s directory and was ignored — nonGitRepos paths must stay inside the repo`);
        if (capped) notes.push(`walk capped for ${r.repo}: limits are depth ${WALK_MAX_DEPTH} / ${WALK_MAX_ENTRIES} entries, so newer-file counts are a lower bound`);
        if (newestMs > r.mtimeMs + graceMs) {
          alarms.push(`manifest may be stale: ${r.repo} changed ${newerCount} files since manifest`);
        }
      } else if (gitOk) {
        notes.push(`staleness skipped: ${r.repo} is not a git repo and has no nonGitRepos entry in substrate.config.json`);
      } else {
        gitSkipped = true; // cannot classify without git; the single note below covers it
      }
    } catch (e) {
      alarms.push(`staleness check failed: ${r.repo} (${clean(e.message.split("\n")[0])})`);
    }
  }
  if (gitSkipped) notes.unshift("git not found — staleness skipped for git repos");
  return { alarms, notes };
}

function emitMarkdown(repos, graph, alarms, notes, problems) {
  const { nodes, edges, inDegree, consumers, orphans, hubs } = graph;
  const L = [];
  L.push("# SUBSTRATE — generated primitive registry");
  L.push("");
  L.push(`Generated by \`substrate-graph.mjs\` from ${repos.length} \`substrate.json\` manifests. Do not edit by hand — regenerate with \`/substrate:emit\`. Shipping a primitive means updating that repo's \`substrate.json\` in the same commit.`);
  L.push("");
  L.push(`## Nodes (${nodes.size} primitives, by repo)`);
  for (const r of [...repos].sort((a, b) => a.repo.localeCompare(b.repo))) {
    L.push("");
    L.push(`### ${r.repo}`);
    for (const p of [...(r.primitives || [])].sort((a, b) => a.id.localeCompare(b.id))) {
      L.push(`- **${p.id}** (${p.kind}, ${p.status}) — ${p.desc}`);
      L.push(`  - paths: ${p.paths.map((x) => `\`${x}\``).join(", ")}`);
      if ((p.consumes || []).length) L.push(`  - consumes: ${p.consumes.join(", ")}`);
    }
  }
  L.push("");
  L.push(`## Edges (${edges.length} — "A consumes B")`);
  L.push("");
  for (const [from, to] of edges) L.push(`- ${from} → ${to}`);
  L.push("");
  L.push("## Analytics");
  L.push("");
  L.push(`### Orphans (${orphans.length} — no in-edges; dormant-value candidates)`);
  L.push("");
  for (const id of orphans) L.push(`- ${id} (${nodes.get(id).repo})`);
  L.push("");
  L.push(`### Hubs (in-degree ≥ 2 — harden first)`);
  L.push("");
  for (const id of hubs) L.push(`- ${id} (${nodes.get(id).repo}) ← ${inDegree.get(id)}: ${consumers.get(id).sort().join(", ")}`);
  L.push("");
  L.push("### Staleness alarms (as of last generation)");
  L.push("");
  const stale = [...alarms, ...notes];
  if (stale.length) for (const a of stale) L.push(`- ${a}`);
  else L.push("- none");
  if (problems.length) {
    L.push("");
    L.push("### Manifest problems");
    L.push("");
    for (const p of problems) L.push(`- ${p}`);
  }
  L.push("");
  return L.join("\n");
}

function writeSubstrateMd(out, text) {
  // never write THROUGH a symlink: the content is manifest-derived, so a symlink
  // at <root>/SUBSTRATE.md pointing at an auto-loaded file would be a write primitive
  if (fs.existsSync(out) && fs.lstatSync(out).isSymbolicLink()) fs.unlinkSync(out);
  fs.writeFileSync(out, text, "utf8");
}

function main() {
  const argv = process.argv.slice(2);
  const mode = argv.includes("--emit") ? "emit" : "check";
  const root = resolveRoot(argv);
  const out = path.join(root, "SUBSTRATE.md");
  const problems = [];
  const config = loadConfig(root, problems);
  const repos = loadManifests(root, config.excludeDirs, problems);
  const graph = buildGraph(repos, problems);
  const { alarms, notes } = stalenessAlarms(repos, root, config);
  if (mode === "emit") {
    writeSubstrateMd(out, emitMarkdown(repos, graph, alarms, notes, problems));
    console.log(`wrote ${out}: ${repos.length} repos, ${graph.nodes.size} primitives, ${graph.edges.length} edges, ${graph.orphans.length} orphans, ${graph.hubs.length} hubs, ${alarms.length} alarms`);
    if (problems.length) {
      for (const p of problems) console.error(`problem: ${p}`);
      process.exitCode = 1;
    }
  } else {
    const hubStr = graph.hubs.map((id) => `${id}(${graph.inDegree.get(id)})`).join(", ") || "none";
    console.log(`substrate: ${repos.length} repos / ${graph.nodes.size} primitives / ${graph.edges.length} edges | orphans: ${graph.orphans.length} | hubs: ${hubStr}`);
    console.log(`full graph: ${out} (regenerated by /substrate:emit)`);
    for (const p of capList(problems)) console.log(`PROBLEM: ${p}`);
    for (const a of capList(alarms)) console.log(`ALARM: ${a}`);
    for (const n of capList(notes)) console.log(`NOTE: ${n}`);
    if (!alarms.length && !notes.length) console.log("staleness: all manifests current");
  }
}

try {
  main();
} catch (e) {
  console.log(`substrate-graph error: ${e.message}`);
  if (process.argv.includes("--emit")) process.exitCode = 1;
}
