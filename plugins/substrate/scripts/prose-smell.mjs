#!/usr/bin/env node
// prose-smell.mjs — deterministic AI-writing-smell gate for outward prose.
//
// WHAT IT CATCHES, and the evidence for each choice (Lillywhite 2026-05/07,
// Contawe 2026-02): the frames that actually mark generated text —
//   * contrastive negation ("isn't just about X — it's about Y"), named by
//     Contawe as the single most prevalent tell;
//   * "whether you're A, B, or C" inclusive parallelism (runner-up);
//   * sentence-initial "From X to Y," coverage parallelism (runner-up;
//     MEDIUM here, because literal ranges are legitimate);
//   * leftover assistant artifacts ("Would you like me to…");
//   * stock LLM vocabulary (delve/tapestry/testament/pivotal role/…).
//
// WHAT IT DELIBERATELY LEAVES ALONE: metaphors, similes, qualifiers
// ("remarkably difficult"), clean structure, and em dashes in moderation —
// the same articles' own point is that normal writing techniques are not
// evidence. Em dashes get a density note only when genuinely heavy, never a
// failure. The deeper tells (inhuman volume, claims the author cannot defend
// cold) are not regex-able; they live in the WRITING doctrine snippet.
//
// Exit codes: 2 = at least one HIGH finding (stderr carries the findings, so
// a PostToolUse hook feeds them straight back to the author for a rewrite);
// 0 = clean or medium-only (mediums print to stdout as advice).
import fs from "node:fs";
import path from "node:path";

const PROSE_EXT = new Set([".md", ".markdown", ".txt", ".html", ".htm", ".eml"]);
// Generated/ledger files carry structured text, not authored prose.
const SKIP_BASENAMES = new Set(["substrate.md", "deliverables.json", "substrate.json"]);
const SKIP_SEGMENTS = new Set(["node_modules", ".git", "dist", "out"]);

const HIGH_RULES = [
  {
    rule: "negation-contrast",
    // "isn't just about X — it's about Y" and its punctuation variants; the
    // resolving clause must appear in the SAME sentence for a hit.
    re: /\b(?:is|are|was|were)(?:n['’]t|\s+not)\s+(?:just|only|merely|simply)\b[^.?!\n]{0,90}[;,—–-]\s*(?:it|this|that|they)['’]?s?\b/gi,
  },
  { rule: "more-than-just", re: /\b(?:is|are)\s+more\s+than\s+just\b/gi },
  {
    rule: "whether-your",
    re: /\bwhether\s+you(?:['’]re|\s+are)\b[^.?!\n]{0,100}\bor\b/gi,
  },
  {
    rule: "assistant-artifact",
    re: /\b(?:would you like me to|just say the word|i hope this helps|let me know if you(?:['’]d| would) like|as an ai\b|great question[!,])/gi,
  },
  {
    rule: "stock-vocab",
    re: /\b(?:delv(?:e|es|ing)\b|tapestry\b|game-?changer|ever-evolving|in today['’]s fast-paced|unlock(?:ing)? the (?:power|potential|value)|elevate your\b|seamlessly integrat|a testament to\b|underscores? the\b|plays? a (?:pivotal|crucial|vital) role|it['’]s (?:important to note|worth noting)|in the (?:realm|landscape) of\b|navigating the (?:complexit|landscape))/gi,
  },
];

const MEDIUM_RULES = [
  // Sentence-initial coverage parallelism; literal ranges usually sit
  // mid-sentence or lack the trailing comma, so this stays narrow.
  { rule: "from-to-coverage", re: /^From\s+[^.?!\n]{3,60}\s+to\s+[^.?!\n]{3,60},/gim },
  { rule: "concluder", re: /^(?:in conclusion|in summary)\b/gim },
];

const EMDASH_PER_100_WORDS = 1.5;
const EMDASH_MIN_COUNT = 4;

/** Blank out fenced code blocks, preserving line numbers. */
function stripFences(text) {
  const lines = text.split("\n");
  let inFence = false;
  return lines
    .map((l) => {
      if (/^\s*(?:```|~~~)/.test(l)) {
        inFence = !inFence;
        return "";
      }
      return inFence ? "" : l;
    })
    .join("\n");
}

function lineOf(text, index) {
  return text.slice(0, index).split("\n").length;
}

export function scan(text) {
  const prose = stripFences(text);
  const findings = [];
  for (const { rule, re } of HIGH_RULES) {
    re.lastIndex = 0;
    for (const m of prose.matchAll(re)) {
      findings.push({ severity: "high", rule, line: lineOf(prose, m.index),
                      excerpt: m[0].slice(0, 80).trim() });
    }
  }
  for (const { rule, re } of MEDIUM_RULES) {
    re.lastIndex = 0;
    for (const m of prose.matchAll(re)) {
      findings.push({ severity: "medium", rule, line: lineOf(prose, m.index),
                      excerpt: m[0].slice(0, 80).trim() });
    }
  }
  const words = (prose.match(/\S+/g) || []).length;
  const dashes = (prose.match(/[—–]/g) || []).length;
  if (words > 0 && dashes >= EMDASH_MIN_COUNT
      && (dashes / words) * 100 > EMDASH_PER_100_WORDS) {
    findings.push({
      severity: "medium", rule: "em-dash-density", line: 1,
      excerpt: `${dashes} dashes in ${words} words — fine in-house style, `
        + "heavy for an external artifact; thin if this leaves the building",
    });
  }
  return findings;
}

function report(file, findings) {
  const highs = findings.filter((f) => f.severity === "high");
  const meds = findings.filter((f) => f.severity === "medium");
  for (const f of meds) {
    process.stdout.write(`${file}:${f.line} [medium] ${f.rule} — "${f.excerpt}"\n`);
  }
  for (const f of highs) {
    process.stderr.write(`${file}:${f.line} [HIGH] ${f.rule} — "${f.excerpt}"\n`);
  }
  return highs.length;
}

function isProseFile(fp) {
  if (!fp) return false;
  if (!PROSE_EXT.has(path.extname(fp).toLowerCase())) return false;
  if (SKIP_BASENAMES.has(path.basename(fp).toLowerCase())) return false;
  const segs = fp.split(/[\\/]/).map((s) => s.toLowerCase());
  return !segs.some((s) => SKIP_SEGMENTS.has(s));
}

function scanFile(fp) {
  return report(fp, scan(fs.readFileSync(fp, "utf8")));
}

function main() {
  const args = process.argv.slice(2);
  if (args[0] === "--hook") {
    // PostToolUse contract: hook JSON on stdin; exit 2 feeds stderr back to
    // the author. Anything unparseable or out of scope exits 0 — a style
    // gate must never break an unrelated write.
    let raw = "";
    try { raw = fs.readFileSync(0, "utf8"); } catch { process.exit(0); }
    let fp = "";
    try { fp = (JSON.parse(raw).tool_input || {}).file_path || ""; } catch { process.exit(0); }
    if (!isProseFile(fp) || !fs.existsSync(fp)) process.exit(0);
    let highs = 0;
    try { highs = scanFile(fp); } catch { process.exit(0); }
    if (highs > 0) {
      process.stderr.write(
        "prose-smell: the flagged frames read as generated text — rewrite them "
        + "in plain declarative sentences before this leaves the building.\n");
      process.exit(2);
    }
    process.exit(0);
  }

  const files = args.filter((a) => !a.startsWith("--"));
  if (files.length === 0) {
    process.stderr.write("usage: prose-smell.mjs [--hook] <files…>\n");
    process.exit(0);
  }
  let highs = 0;
  for (const f of files) highs += scanFile(f);
  process.exit(highs > 0 ? 2 : 0);
}

main();
