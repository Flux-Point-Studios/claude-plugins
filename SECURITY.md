# Security

## Scope

These plugins run shell and Python hooks with the user's privileges on every
session start, every file edit, every shell command and every stop. A defect
in a hook is a defect in every repository that installs it, so reports are
taken seriously and handled promptly.

In scope:

- a hook that can be made to run code from a repository's own files or from
  a tool payload (`WORK.md`, a patch, a command string);
- a gate that can be bypassed or disarmed without `FPL_DISABLE=1`;
- the credential gate permitting a command that emits a declared secret;
- the compiler emitting executable code from unescaped IR values.

## Reporting

Use GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability"). Please include the runtime
(Claude Code or Codex) and its version, the plugin version, the hook or
script involved, and a payload or repository state that reproduces the
issue. Do not open a public issue for a vulnerability.

## Supported versions

The latest released version of each plugin, as listed in
`.claude-plugin/marketplace.json`.

## Posture

- Treat this repository as production infrastructure: protected default
  branch, required review, signed commits.
- Unattended loops belong in a sandboxed container with allow-listed egress
  and no path to key material.
- `WORK.md` is untrusted input to the compiler: free text is escaped, JS
  identifiers are validated, and `tests/security-test.py` executes real
  payloads to prove they stay inert.
- The credential gate exists so that a secret's path can be passed to code
  that emits public derivations while the secret itself never enters the
  agent's context.
