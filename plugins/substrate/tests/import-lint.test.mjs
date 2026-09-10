// The registry verifies itself: a real intra-repo import that crosses a
// primitive boundary with no declared `consumes` edge fails the emit.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { makeWorkspace, writeManifest, run, prim } from "./helpers.mjs";

function has(text, needle) {
  assert.ok(text.includes(needle), `expected output to contain:\n${needle}\n--- got ---\n${text}`);
}

function writeSources(root, dir, files) {
  for (const [rel, content] of Object.entries(files)) {
    const f = path.join(root, dir, rel);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    fs.writeFileSync(f, content);
  }
}

test("a cross-primitive import with no declared consumes fails the emit and names the offender", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [prim("core", { paths: ["src/core.mjs"] }), prim("tool", { paths: ["src/tool.mjs"] })],
  });
  writeSources(ws, "js", {
    "src/core.mjs": "export const x = 1;\n",
    "src/tool.mjs": "import { x } from './core.mjs';\nexport default x;\n",
  });

  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1, "emit exits non-zero on an undeclared import edge");
  has(res.stderr, "problem: import lint: tool -> core undeclared (js/src/tool.mjs:1 imports ./core.mjs)");
  const md = fs.readFileSync(path.join(ws, "SUBSTRATE.md"), "utf8");
  has(md, "- import lint: tool -> core undeclared (js/src/tool.mjs:1 imports ./core.mjs)");

  const check = run(["--check", "--root", ws]);
  assert.equal(check.status, 0, "--check still always exits 0");
  has(check.stdout, "PROBLEM: import lint: tool -> core undeclared");
});

test("a declared consumes edge satisfies the lint", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [prim("core", { paths: ["src/core.mjs"] }), prim("tool", { paths: ["src/tool.mjs"], consumes: ["core"] })],
  });
  writeSources(ws, "js", {
    "src/core.mjs": "export const x = 1;\n",
    "src/tool.mjs": "import { x } from './core.mjs';\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0, res.stderr);
  assert.ok(!res.stderr.includes("import lint"), `unexpected lint failure:\n${res.stderr}`);
});

test("imports inside one primitive, and into files no primitive declares, are not misses", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [prim("pair", { paths: ["src/a.mjs", "src/b.mjs"] })],
  });
  writeSources(ws, "js", {
    "src/a.mjs": "import './b.mjs';\nimport { h } from './helper.mjs';\n",
    "src/b.mjs": "export const b = 1;\n",
    "src/helper.mjs": "export const h = 1;\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0, res.stderr);
});

test("require(), dynamic import() and export-from are all detected", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [
      prim("core", { paths: ["src/core.js"] }),
      prim("req", { paths: ["src/req.js"] }),
      prim("dyn", { paths: ["src/dyn.js"] }),
      prim("reexport", { paths: ["src/reexport.js"] }),
    ],
  });
  writeSources(ws, "js", {
    "src/core.js": "module.exports = { x: 1 };\n",
    "src/req.js": "const c = require('./core.js');\n",
    "src/dyn.js": "const p = import('./core.js');\n",
    "src/reexport.js": "export { x } from './core.js';\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1);
  has(res.stderr, "import lint: dyn -> core undeclared (js/src/dyn.js:1 imports ./core.js)");
  has(res.stderr, "import lint: reexport -> core undeclared (js/src/reexport.js:1 imports ./core.js)");
  has(res.stderr, "import lint: req -> core undeclared (js/src/req.js:1 imports ./core.js)");
});

test("extensionless and index specifiers resolve", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [prim("core", { paths: ["src/core"] }), prim("tool", { paths: ["src/tool.mjs"] })],
  });
  writeSources(ws, "js", {
    "src/core/index.mjs": "export const x = 1;\n",
    "src/tool.mjs": "import { x } from './core';\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1);
  has(res.stderr, "import lint: tool -> core undeclared (js/src/tool.mjs:1 imports ./core)");
});

test("specifiers inside comments and docstrings never create a miss", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "mix",
    primitives: [
      prim("core", { paths: ["src/core.mjs"] }),
      prim("prose", { paths: ["src/prose.mjs"] }),
      prim("pycore", { paths: ["pkg/pycore.py"] }),
      prim("pyprose", { paths: ["pkg/pyprose.py"] }),
    ],
  });
  writeSources(ws, "mix", {
    "src/core.mjs": "export const x = 1;\n",
    "src/prose.mjs": "// import { x } from './core.mjs';\n/* const c = require('./core.mjs'); */\nexport const y = 2;\n",
    "pkg/pycore.py": "X = 1\n",
    "pkg/pyprose.py": '"""Design note.\n\nfrom .pycore import X\n"""\n# import pycore\nY = 2\n',
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0, res.stderr);
});

test("python relative, sibling and dotted-package imports resolve", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "py", {
    repo: "py",
    primitives: [
      prim("pkg-core", { paths: ["pkg/core.py"] }),
      prim("pkg-app", { paths: ["pkg/app.py"] }),
      prim("sibling", { paths: ["etl/sibling.py"] }),
      prim("etl-main", { paths: ["etl/main.py"] }),
      prim("root-tool", { paths: ["tool.py"] }),
    ],
  });
  writeSources(ws, "py", {
    "pkg/__init__.py": "",
    "pkg/core.py": "X = 1\n",
    "pkg/app.py": "from .core import X\n",
    "etl/sibling.py": "Y = 2\n",
    "etl/main.py": "import sibling\n\ndef f():\n    return sibling.Y\n",
    "tool.py": "from pkg.core import X\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1);
  has(res.stderr, "import lint: pkg-app -> pkg-core undeclared (py/pkg/app.py:1 imports .core)");
  has(res.stderr, "import lint: etl-main -> sibling undeclared (py/etl/main.py:1 imports sibling)");
  has(res.stderr, "import lint: root-tool -> pkg-core undeclared (py/tool.py:1 imports pkg.core)");
});

test("a function-local import is a note, never a fatal dependency edge", () => {
  // Deferring an import inside a function is the standard way to BREAK a
  // dependency cycle. The real direction here is one-way (oracle -> chain,
  // declared); counting chain's deferred read-back as an edge would demand
  // the manifest declare the very cycle the deferral exists to avoid.
  const ws = makeWorkspace();
  writeManifest(ws, "py", {
    repo: "py",
    primitives: [
      prim("chain-access", { paths: ["api/chain.py"] }),
      prim("oracle-read", { paths: ["oracles/self_read.py"], consumes: ["chain-access"] }),
    ],
  });
  writeSources(ws, "py", {
    "api/chain.py":
      "def price():\n" +
      "    # `oracles` imports `chain`, so the import is deferred here to keep\n" +
      "    # that one way round.\n" +
      "    from oracles.self_read import POLICY\n" +
      "    return POLICY\n",
    "oracles/self_read.py": "from api.chain import price\nPOLICY = 1\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0, `deferred import must not fail the emit:\n${res.stderr}`);
  assert.ok(!res.stderr.includes("import lint"), `no fatal lint expected:\n${res.stderr}`);
  const md = fs.readFileSync(path.join(ws, "SUBSTRATE.md"), "utf8");
  has(md, "deferred import: chain-access uses oracle-read");
});

test("a directory path owns everything under it, and the most specific declaration wins", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "d", {
    repo: "d",
    primitives: [prim("dir-owner", { paths: ["src"] }), prim("leaf", { paths: ["src/leaf.mjs"] })],
  });
  writeSources(ws, "d", {
    "src/main.mjs": "import './leaf.mjs';\n",
    "src/leaf.mjs": "export const l = 1;\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1);
  has(res.stderr, "import lint: dir-owner -> leaf undeclared (d/src/main.mjs:1 imports ./leaf.mjs)");
  assert.ok(!res.stderr.includes("leaf -> dir-owner"), "the leaf file is not also owned by the directory declaration");
});

test("imports that resolve outside the repo, or to nothing, are ignored", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "a", { repo: "a", primitives: [prim("a-core", { paths: ["src/a.mjs"] })] });
  writeManifest(ws, "b", { repo: "b", primitives: [prim("b-core", { paths: ["src/b.mjs"] })] });
  writeSources(ws, "a", {
    "src/a.mjs": "import '../../b/src/b.mjs';\nimport './gone.mjs';\nimport 'playwright';\n",
  });
  writeSources(ws, "b", { "src/b.mjs": "export const b = 1;\n" });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 0, res.stderr);
});

test("one miss line per primitive pair, not one per import site", () => {
  const ws = makeWorkspace();
  writeManifest(ws, "js", {
    repo: "js",
    primitives: [prim("core", { paths: ["src/core.mjs"] }), prim("tool", { paths: ["src/t1.mjs", "src/t2.mjs"] })],
  });
  writeSources(ws, "js", {
    "src/core.mjs": "export const x = 1;\n",
    "src/t1.mjs": "import { x } from './core.mjs';\n",
    "src/t2.mjs": "import { x } from './core.mjs';\n",
  });
  const res = run(["--emit", "--root", ws]);
  assert.equal(res.status, 1);
  const lines = res.stderr.split("\n").filter((l) => l.includes("import lint: tool -> core"));
  assert.equal(lines.length, 1, `expected one deduped miss line, got:\n${res.stderr}`);
  has(res.stderr, "(js/src/t1.mjs:1 imports ./core.mjs)");
});
