#!/usr/bin/env python3
"""Credential gate: refuse the read that leaks, permit the read that works.

The only defence against an agent mishandling a credential used to be a DENY
rule in `.claude/settings.json` — `Read(D:/wallet/**)`, `Bash(railway:*)`. A
deny is binary: it stops the leak AND it stops the work. On 2026-08-11 that
cost hours of a live mainnet ceremony. The operator hand-edited settings.json
twice mid-session, a global allow still could not reach past a project deny,
and an agent that was fully capable of discharging a genesis blocker safely
could not discharge it at all.

The technique that replaces the deny was proven the same night, offline,
against a live Cardano mainnet oracle key:

    NEVER READ A CREDENTIAL. PASS ITS PATH TO CODE THAT EMITS ONLY PUBLIC
    DERIVATIONS.

    UNSAFE:  cat D:/wallet/mnemonic.txt
    SAFE:    python -c "from x import load_wallet;
                        print(load_wallet('D:/wallet/mnemonic.txt').address)"

Both read the same file. The second one's secret never enters the agent
context, because the process that read it emitted an address. A public key
hash, a bech32 address, a policy id — all derived FROM the secret, none of
them revealing it. A deny rule cannot express that difference; it sees one
path and blocks both. This guard is that difference, and nothing else.

  secret-guard.py --hook              decide a PreToolUse payload on stdin
  secret-guard.py --command "..."     decide one command, for tests and CI
  secret-guard.py --list              what this repo declares

Dormant by design: a repo with no manifest costs one stat and returns, like
exec-attest.sh in a repo with no `.fluxpoint-gates.json`.

## What is refused

  EMIT    a program whose output IS the file — cat, head, xxd, strings,
          base64, diff, tar, Get-Content — plus the text utilities that print
          the whole file under one flag and a single field under another
          (grep, sed, awk, jq, cut, tr, sort). Those last are refused on the
          ambiguous side deliberately: `grep -q` leaks nothing and `grep .`
          leaks everything, this cannot tell them apart, and the safe shape
          is one line away in the same shell.
  CARRY   a copy out of the declaration — cp, mv, ln, split, rsync, scp,
          curl, nc. The bytes land at a path no entry covers, and reading
          THAT path is a command this guard permits. A copy whose destination
          is itself declared is permitted, because the bytes never leave.
  COVER   a directory that HOLDS a declaration, handed to one of the above:
          `grep -r . secrets` and `tar czf o.tgz secrets` print or pack
          `secrets/vault.txt` without ever naming it.
  OPAQUE  a declared path in a segment whose program cannot be named: a bare
          `< secret > elsewhere`, `$TOOL secret`, an `eval` whose payload
          cannot be reached, or the credential itself in program position.

Everything else is permitted, including programs this guard has never heard
of. `cardano-cli`, `cardano-address`, `openssl`, `sha256sum`, an in-repo
derive script, and the tool nobody has written yet all pass with the path as
an argument.

## Operand, not substring: the rule that keeps adoption possible

A refusal fires only when a declared path is the OPERAND of a refusing
program — the whole token, once quotes, a leading `@` and shell grouping are
off. A path that merely appears INSIDE a token is data:

    sed -i 's|SEED_PATH|D:/wallet/mnemonic.txt|' config.yaml    permitted
    awk -v seed=D:/wallet/mnemonic.txt 'BEGIN{print seed}'      permitted
    curl -d '{"wallet":"D:/wallet/mnemonic.txt"}' https://...   permitted
    sed -n 1p D:/wallet/mnemonic.txt                            refused

Wiring a credential PATH into a config file is how a repo ADOPTS the
technique this guard enforces. Blocking the adoption step was the sharpest
version of the deny-rule problem: the guard refusing the thing it exists to
teach. The manifest's own premise settles it — the path is the safe thing to
pass around, and only the content is dangerous.

## Which end of a copy the credential is on

A CARRY refuses when a declared path is a SOURCE and the destination is not
itself declared. Three roles decide that, and they are read from per-program
flag tables rather than from argument order, because the same letter means
different things: `-d` is a data payload to curl and an extract directory to
unzip, and `scp -o` is an ssh option while `curl -o` is the output file.

  DESTINATION  bytes arriving — `cp -t`, `curl -o`, `tar -x -C`, `unzip -d`,
               every positional of `tee`, and — for a CARRY program only, and
               only when no source flag already claimed one — the trailing
               positional. Scoping that last rule to CARRY is what keeps
               `tail -n 1 SECRET` from reading its own file operand as a
               place the bytes were going.
  SOURCE       bytes leaving — `curl -T`, `--upload-file`, `-F name=@path`,
               `--data-binary @path`, and plain positional sources.
  CONSUMED     a credential the program AUTHENTICATES with and never moves —
               `scp -i`, `curl --cert/--key`, `wget --certificate`,
               `rsync -e "ssh -i ..."`. These are the purest instance of the
               technique this guard protects: the key authenticates, the
               bytes never leave. Refusing them left `bash deploy.sh` as the
               only workaround, and a guard whose escape hatch is "put it in
               a file I cannot see" is the disable-me pressure made flesh.

## The ambiguity policy, because it is the whole design

Fail-closed and not-eager pull against each other, and the loser is always
the same: a guard that blocks legitimate work gets `FPL_DISABLE=1` and then
protects nothing. Both halves are held by scoping WHEN the question is even
asked.

  1. A command that mentions no declared path and matches no declared
     emitter is never analysed. Not parsed leniently — not analysed. This is
     essentially every command an agent runs, and it is where a guard would
     otherwise earn its reputation for crying wolf.
  2. Inside the small universe of commands that DO touch a declaration,
     ambiguity about *what happens to the bytes* is refused: an unnamed
     program, a redirection with no program, a nesting depth this does not
     follow.
  3. But ambiguity about *what a named program does* is permitted. The set
     of programs whose whole purpose is to print a file is small, closed and
     enumerable. The set of programs that do real work with a credential is
     open and cannot be enumerated. Whitelisting the safe side would be a
     deny list wearing a different hat, and it would have blocked the
     ceremony that motivated this file.

Shell grammar sits under rule 1 rather than rule 2. `(`, `{`, `then`, `do`
and `!` occupy the program position, so naming them as the program and
finding them in no table is not the fail-closed path — it is confidently
naming the WRONG program and permitting `( cat SECRET )`. Grammar is a fixed
closed set, so stripping it terminates; it is not an arms race.

## Why a declared manifest and not a heuristic

The same night produced the finding that settles it: the file named
`publisher_mnemonic.txt` was NOT the publisher key — it derived a RETIRED
credential, and the live key was in `aegis_vault_clean.txt`. Any guard that
inferred secrecy from a filename would have protected the dead credential
and left the live one open, while reporting full coverage. A name is not
evidence. Only a declaration is.

## Where the manifest lives, and why the repo root

`.fluxpoint-secrets.json`, beside `.fluxpoint-pairs.json` and
`.fluxpoint-gates.json`, not under `.claude/`. Three reasons, and the third
is the one that decides it:

  * `.claude/` is session state — per-session baselines and markers, written
    by machines, disposable, and skipped by `_fpl_skip_path` so it is not
    diffed or scanned. A declaration of what must never be printed is none
    of those things.
  * A change to what counts as a credential must land in a reviewed diff.
    At the root it does; under `.claude/` it is routinely gitignored.
  * The absolute machine paths inside it are not secrets. That is the whole
    premise of the technique being enforced: the PATH is the safe thing to
    pass around, and only the content is dangerous. A manifest that names
    `D:/wallet/**` in a public repo leaks nothing and tells the next clone
    exactly which files it must never print.

## What this does NOT defend against, stated rather than implied

  * `python -c "print(open('D:/wallet/x').read())"` — a permitted
    interpreter told to dump. This cannot read a program's intentions, and
    refusing every interpreter is the deny rule again. `secret-guard-test.sh`
    pins this as PERMITTED so the boundary cannot drift by accident.
  * A path reached through a variable the guard never sees (`cat $SEED`) or
    assembled at runtime. Paths are matched, not dataflow. A literal glob
    into a declared directory (`cat D:/wallet/*`) IS caught, because the
    glob is still text that matches the declaration.
  * An encoded payload — `powershell -EncodedCommand`. The path is not in
    the command in any form this can match.
  * A UNC spelling (`\\\\server\\share\\...`) of a drive-letter declaration.
    A UNC path names a different filesystem object, and there is no correct
    mapping back to `D:/` to canonicalize it through.
  * A process started earlier that already holds the bytes, and anything
    outside the Bash / Read / Grep channel: an MCP tool, a subagent, a file
    the agent read before the manifest existed.

Each of those is a reason to keep the credential out of the working tree,
not a reason to widen this guard until it refuses work.
"""
import argparse
import json
import os
import re
import sys
from collections import namedtuple

MANIFEST = ".fluxpoint-secrets.json"
ENTRY_FIELDS = {"id", "paths", "emits", "why"}

# Programs whose output IS the file. Archivers and encoders are here rather
# than with the copiers: `gzip -c`, `base64` and `tar -cf -` all put the
# bytes on stdout, and their destination flag varies by tool and by argument
# order, so a positional exemption would guess wrong on the tool that matters.
# The comparison family is here because an agent verifying a restored backup
# against an original reaches for `diff` first, and that is exactly the
# ceremony workflow this guard was written for.
EMIT = {
    "cat", "tac", "bat", "batcat", "head", "tail", "less", "more", "most",
    "pg", "type", "xxd", "od", "hexdump", "hd", "strings", "base64", "base32",
    "uuencode", "nl", "rev", "fold", "expand", "unexpand", "cut", "paste",
    "tr", "sed", "awk", "gawk", "mawk", "nawk", "grep", "egrep", "fgrep",
    "rg", "ag", "ack", "sort", "uniq", "shuf", "jq", "yq", "dd", "iconv",
    "tee", "gzip", "gunzip", "zcat", "bzip2", "bzcat", "xz", "xzcat", "zstd",
    "tar", "zip", "unzip", "7z", "7za",
    "diff", "sdiff", "vimdiff", "colordiff", "cmp", "comm", "join", "pr",
    "column", "csplit",
    "get-content", "gc", "format-hex", "select-string", "sls", "out-string",
}

# Programs that move the bytes somewhere else. Refused unless the destination
# is itself declared — a copy that stays inside the manifest leaks nothing.
# `split` is here rather than in EMIT: its output is files at an undeclared
# prefix, which is the CARRY definition exactly.
CARRY = {
    "cp", "copy", "copy-item", "mv", "move", "move-item", "install", "rsync",
    "scp", "sftp", "ln", "curl", "wget", "nc", "ncat", "socat", "split",
}

# git is mostly metadata, so a blanket EMIT entry would refuse `git status`.
# These subcommands print file CONTENT, including out of history where no
# working-tree path exists to match.
GIT_EMIT = {"show", "diff", "cat-file", "blame", "annotate", "grep", "log",
            "difftool"}

ARCHIVE = {"tar", "zip", "unzip", "7z", "7za"}

# Programs that carry another command as an argument. The payload is analysed
# rather than trusted: `bash -c "cat <secret>"` is a `cat` with a costume on.
NEST = {"bash", "sh", "zsh", "dash", "ksh", "ash", "powershell", "pwsh",
        "cmd", "eval", "script"}

# Words that stand in front of the real program without changing what it does.
# `xargs` is NOT here: it takes its arguments from a pipe, so the path and the
# dumper land in different segments and treating it as transparent related
# neither to the other.
PREFIX = {"sudo", "doas", "env", "nohup", "stdbuf", "time", "nice", "ionice",
          "timeout", "command", "builtin", "exec", "setsid", "busybox",
          "winpty", "watch"}

# Shell grouping and keywords, which occupy the program position without being
# a program. Closed set: this is the whole of the grammar that can lead a
# command, so stripping it terminates.
GRAMMAR = {"!", "if", "then", "elif", "else", "fi", "case", "esac", "in",
           "for", "select", "while", "until", "do", "done", "function",
           "coproc"}

# Redirection targets that put the bytes back on the terminal the guard is
# protecting. `> /dev/stdout` satisfied "a file stands between this and the
# transcript" while being provably not one.
TRANSCRIPT_SINKS = {
    "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/console", "/dev/fd/1",
    "/dev/fd/2", "/proc/self/fd/1", "/proc/self/fd/2", "con", "conout$", "-",
}

XARGS_OPERANDS = {"-I", "-i", "-L", "-l", "-n", "-P", "-s", "-d", "-E", "-e",
                  "-a", "--replace", "--max-lines", "--max-args",
                  "--max-procs", "--max-chars", "--delimiter", "--eof",
                  "--arg-file"}

# Per program, which flags name a DESTINATION (bytes arriving), a SOURCE
# (bytes leaving) and a credential the program CONSUMES rather than moves.
# One table per program because the same letter means different things, and a
# single shared set plus "the last positional is the destination" read
# `cp -t /tmp SECRET` backwards and exempted the credential as the target.
#
# Matched case-SENSITIVELY, because case is the only thing separating half of
# these from their opposite: `curl -T` uploads while `-t` sets a telnet
# option, `wget -O` is the output file while `-o` is the log, and `tar -C` is
# a directory while `-c` is the flag that says this run is packing, not
# unpacking.
FLOW = {
    "cp":      {"dest": {"-t", "--target-directory"}},
    "mv":      {"dest": {"-t", "--target-directory"}},
    "install": {"dest": {"-t", "--target-directory"}},
    "ln":      {"dest": {"-t", "--target-directory"}},
    "tee":     {"posdest": True},
    "curl":    {"dest": {"-o", "--output"},
                "src": {"-T", "--upload-file", "-F", "--form", "-d", "--data",
                        "--data-raw", "--data-ascii", "--data-binary",
                        "--data-urlencode"},
                "auth": {"-E", "--cert", "--key", "--cacert", "--capath",
                         "--pubkey", "--cert-type", "--key-type", "-K",
                         "--config"}},
    "wget":    {"dest": {"-O", "--output-document", "-P", "--directory-prefix"},
                "src": {"--post-file", "--body-file"},
                "auth": {"--certificate", "--private-key", "--ca-certificate",
                         "--ca-directory", "--config"}},
    "scp":     {"auth": {"-i", "-F", "-S", "-c", "-o", "-J"}},
    "sftp":    {"auth": {"-i", "-F", "-S", "-c", "-o", "-J"}},
    "rsync":   {"auth": {"-e", "--rsh"}},
    "tar":     {"extract": {"-C", "--directory"}},
    "unzip":   {"extract": {"-d"}},
    "7z":      {"extract": set()},
    "7za":     {"extract": set()},
    "zip":     {"extract": set()},
}

ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# `bash -xc "cat SECRET"` runs the payload — measured, not assumed — so the
# operand-carrying flag is a SHAPE and not a fixed string. PowerShell resolves
# any unambiguous prefix of -Command.
POSIX_C = re.compile(r"^(?:--command|-[A-Za-z]*c[A-Za-z]*)$")
PS_C = re.compile(r"^-c(?:o(?:m(?:m(?:a(?:n(?:d)?)?)?)?)?)?$", re.I)

# One path segment, and any run of path characters. Whitespace is NOT excluded
# because matching happens per shell token, and a quoted token is allowed to
# contain spaces — `cat "C:/Program Files/key.txt"` is one argument. Quotes,
# backticks and shell metacharacters are excluded so a `**` cannot swallow the
# rest of a command and report a nonsense match.
_SEG = r"[^/\"'`;|&<>()]"
_ANY = r"[^\"'`;|&<>()]"

# Shell decoration the lexer leaves glued to a path token. Stripping it is what
# makes `(cat SECRET)` and `f=@SECRET` resolve to the same operand as the bare
# spelling, without a second matcher for each shape.
_DECOR = "()[]{};,"
_AT = re.compile(r"^(?:[A-Za-z0-9_.-]+=)?@")

SegT = namedtuple("SegT", "tokens sep docs")
Ref = namedtuple("Ref", "kind entry matched detail line")


class ManifestError(Exception):
    """A manifest that does not parse guards nothing, and must say so."""


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def collapse(s):
    return " ".join(str(s).split())


def norm_text(s):
    """Every spelling of one path, reduced to one form.

    Both sides go through here, so a declaration covers all of them without
    the operator guessing which one an agent will type. Five aliases, each
    measured leaking past a `D:/wallet/**` declaration:

      D:\\wallet\\x      backslashes, and the doubled form from a Python literal
      /d/wallet/x        MSYS — what the Bash tool's own shell produces
      /cygdrive/d/...    the cygwin spelling of the same drive
      //?/D:/wallet/x    extended-length, and //./ for the device path
      ././secrets/x      `.` and `..` noise that no filesystem distinguishes
    """
    t = str(s).replace("\\", "/")
    t = re.sub(r"^/{1,2}[?.]/(?=[A-Za-z]:)", "", t)
    t = re.sub(r"/{2,}", "/", t)
    t = re.sub(r"^/cygdrive/([A-Za-z])(?=/|$)", r"\1:", t)
    t = re.sub(r"^/([A-Za-z])(?=/|$)", r"\1:", t)
    t = re.sub(r"^(?:\./)+", "", t)
    while "/./" in t:
        t = t.replace("/./", "/")
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"(?:^|(?<=/))(?!\.\./)[^/]+/\.\./", "", t, count=1)
    return t


def is_abs(p):
    return p.startswith("/") or bool(re.match(r"^[A-Za-z]:/", p))


def path_re(pat):
    """Translate a path glob to a search regex, with `**` spanning directories.

    fnmatch is not usable here: it treats `*` as matching `/` too, so a
    declaration would silently cover files three directories below what it
    claims, and this guard would start refusing work nobody declared.

    Both boundaries are load-bearing. The lookbehind keeps `D:/wallet/**` from
    matching inside `/home/x/notD:/wallet/key`; the lookahead keeps it from
    matching `D:/wallets/index.json` and `D:/wallet-public/README.md`, and
    keeps `secrets/*.txt` off `secrets/vault.txt.sha256` — a checksum sidecar
    beside the vault is custody work, not a leak. Matching is case-insensitive
    because Windows paths are, and because over-matching only decides that a
    command is worth looking at — the safe shape still passes.
    """
    out, i, n = [], 0, len(pat)
    while i < n:
        if pat.startswith("/**", i) and i + 3 == n:
            out.append(f"(?:/{_ANY}*)?")
            i += 3
        elif pat.startswith("**/", i):
            out.append(f"(?:{_ANY}*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(f"{_ANY}*")
            i += 2
        elif pat[i] == "*":
            out.append(f"{_SEG}*")
            i += 1
        elif pat[i] == "?":
            out.append(_SEG)
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile(r"(?<![A-Za-z0-9_./\\-])(?:\./)?" + "".join(out)
                      + r"(?![A-Za-z0-9_.\\-])", re.I)


def cmd_re(pat):
    """A command glob, matched against the whole command line.

    `*` spans everything here: a command line is not a path, and
    `railway variables*` must cover `railway variables --json`.
    """
    out = []
    for c in pat:
        out.append(".*" if c == "*" else "." if c == "?" else re.escape(c))
    return re.compile("".join(out), re.I)


def dir_head(pat):
    """The literal directory a path or glob is anchored in.

    `secrets/*.txt` and `secrets/vault.txt` both live in `secrets/`, and the
    question `grep -r . secrets` asks is about the directory, not the file.
    """
    lit = re.split(r"[*?]", norm_text(pat), 1)[0]
    cut = lit.rfind("/")
    return lit[:cut + 1].lower() if cut >= 0 else ""


def load_manifest(root):
    """Parse and validate. A malformed entry is a hard error, not a skip.

    pair-guard makes the same call for the same reason, but the stakes differ:
    a half-declared pair costs a red build, while a half-declared secret costs
    the credential. So this refuses everything until the file parses rather
    than quietly guarding less than the manifest claims.
    """
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{MANIFEST} is not valid JSON: {e}")
    except OSError as e:
        raise ManifestError(f"{MANIFEST} cannot be read: {e}")
    if not isinstance(data, list):
        raise ManifestError(f"{MANIFEST} must be a list of declarations")
    for i, entry in enumerate(data):
        where = f"{MANIFEST}[{i}]"
        if not isinstance(entry, dict):
            raise ManifestError(f"{where} must be an object")
        unknown = sorted(set(entry) - ENTRY_FIELDS)
        if unknown:
            raise ManifestError(
                f"{where}: unknown field(s) {unknown} — known: "
                f"{', '.join(sorted(ENTRY_FIELDS))}")
        if not entry.get("id"):
            raise ManifestError(f"{where}: id required")
        if not as_list(entry.get("paths")) and not as_list(entry.get("emits")):
            raise ManifestError(
                f"{where} ('{entry['id']}'): declares neither 'paths' nor "
                f"'emits' — an entry that names no credential guards nothing")
    return data


def compile_entries(root, entries):
    """Attach compiled matchers, and a root-anchored twin for relative paths.

    A repo-relative declaration must still fire when the same file is named
    absolutely, or `cat secrets/vault.txt` is refused and
    `cat /home/me/repo/secrets/vault.txt` is not. The root is canonicalized
    before abspath so an MSYS `--root /d/repo` anchors on `D:/repo` rather
    than on the current drive's `\\d\\repo`.
    """
    root_n = norm_text(os.path.abspath(norm_text(root))).rstrip("/")
    out = []
    for e in entries:
        raw_paths = as_list(e.get("paths"))
        matchers, heads = [], []
        for p in raw_paths:
            pn = norm_text(p)
            matchers.append(path_re(pn))
            heads.append(dir_head(pn))
            if not is_abs(pn):
                matchers.append(path_re(f"{root_n}/{pn}"))
                heads.append(dir_head(f"{root_n}/{pn}"))
        raw_emits = as_list(e.get("emits"))
        out.append({
            "id": e["id"], "why": e.get("why", ""),
            "paths": matchers, "heads": [h for h in heads if h],
            "emits": [cmd_re(c) for c in raw_emits],
            "rawPaths": raw_paths, "rawEmits": raw_emits,
        })
    return out


def secret_hit(entries, text):
    """(entry, matched text) for the first declared path found, or (None, None).

    A substring search: this only decides whether a command is worth analysing
    at all. Whether the hit is an OPERAND is a separate, stricter question.
    """
    if not text:
        return None, None
    normalized = norm_text(text)
    for e in entries:
        for rx in e["paths"]:
            m = rx.search(normalized)
            if m:
                return e, m.group(0)
    return None, None


def secret_operand(entries, token):
    """(entry, path) when the token IS a declared path, not merely contains one.

    Strips what the shell would have consumed — grouping glued to the token by
    the lexer, and curl's `@file` / `name=@file` source marker — then demands
    a full match. This is the line between `sed -n 1p SECRET`, which prints the
    credential, and `sed 's|X|SECRET|' config.yaml`, which writes its path into
    a config file and is how a repo adopts the technique in the first place.

    A `<rev>:<path>` operand is tried too, and only after the whole token has
    failed so a drive letter is never mistaken for a revision: `git show
    HEAD:secrets/vault.txt` prints a declared file that never appears in the
    working tree under a name any path matcher would recognise.
    """
    if not token:
        return None, None
    t = _AT.sub("", str(token).strip(_DECOR))
    for cand in (t, t.split(":", 1)[1] if ":" in t[2:] else ""):
        if not cand:
            continue
        n = norm_text(cand)
        for e in entries:
            for rx in e["paths"]:
                if rx.fullmatch(n):
                    return e, n
    return None, None


def cover_hit(entries, token, root_ok=False):
    """(entry, directory) when the token is a directory holding a declaration.

    Two shapes, both measured printing `secrets/vault.txt` against a
    `secrets/*.txt` declaration that names no directory:

      grep -r . secrets      the token is the directory the declaration is in
      cat secrets/*          the token is a glob anchored in that directory

    The repo root is excluded on the Bash channel unless the caller asks for
    it: refusing every `grep -r TODO .` is how a guard earns FPL_DISABLE=1,
    and the root is not a directory anyone chose to point at a credential.
    """
    if not token:
        return None, None
    t = norm_text(token).rstrip("/").lower()
    glob_head = dir_head(token) if re.search(r"[*?]", str(token)) else ""
    for e in entries:
        for h in e["heads"]:
            if t in ("", "."):
                if root_ok and not is_abs(h):
                    return e, "."
                continue
            if h.startswith(t + "/") or (glob_head and glob_head == h):
                return e, token
    return None, None


def _matching_paren(text, i):
    """Index of the `)` closing the `(` at i, or the end of the string."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(text)


def read_heredocs(text, pos, delims):
    """Lift heredoc bodies out of the command text, and say where they ended."""
    bodies = []
    for d in delims:
        lines = []
        while pos < len(text):
            nl = text.find("\n", pos)
            line = text[pos:] if nl < 0 else text[pos:nl]
            pos = len(text) if nl < 0 else nl + 1
            if line.strip() == d:
                break
            lines.append(line)
        bodies.append("\n".join(lines))
    return bodies, pos


def lex(cmd):
    """Quote-aware scan into (segments, substitutions).

    Splitting on `;|&` with a regex is not enough and the failure is a false
    refusal, not a miss: `python -c "print('a|b')"` would split mid-string and
    leave a fragment whose program cannot be named, which this guard is
    obliged to refuse. So quotes are tracked, `$(...)` and backticks are
    lifted out to be analysed as commands in their own right, and a backslash
    escapes only shell-meaningful characters — `D:\\wallet\\x` must survive as
    a path, not decay into `D:walletx`.

    Two constructs cross a newline and so must be recognised here rather than
    left to the segment splitter, because a bare `\\n` ends a segment and every
    following line would be analysed as if its first word were a program:

      * a `\\`-continued command is ONE command
      * a heredoc body is DATA for the segment that opened it, not a run of
        commands — the derive from this file's own docstring, written the way
        the house style asks for multi-line strings, was refused as "the
        credential itself is in program position"
    """
    segments, subs = [], []
    tokens, docs, cur, has = [], [], [], False
    pending = []
    i, n = 0, len(cmd)
    in_s = in_d = False

    def end_token():
        nonlocal cur, has
        if has:
            tokens.append("".join(cur))
        cur, has = [], False

    def end_segment(sep):
        nonlocal tokens, docs
        end_token()
        segments.append(SegT(tokens, sep, docs))
        tokens, docs = [], []

    while i < n:
        c = cmd[i]
        if in_s:
            if c == "'":
                in_s = False
            else:
                cur.append(c)
                has = True
            i += 1
            continue
        if c == "\\" and i + 1 < n and cmd[i + 1] == "\n":
            i += 2
            continue
        if c == "\\" and i + 1 < n and cmd[i + 1] in "\"'`$\\":
            cur.append(cmd[i + 1])
            has = True
            i += 2
            continue
        if c == '"':
            in_d = not in_d
            has = True
            i += 1
            continue
        if c == "'" and not in_d:
            in_s = True
            has = True
            i += 1
            continue
        if cmd.startswith("$'", i) or cmd.startswith('$"', i):
            # ANSI-C and locale quoting. The `$` is a quoting sigil, not part
            # of the word, so leaving it glued made `cat $'D:/wallet/x'` a
            # token that no longer equalled the path it opens.
            i += 1
            continue
        if cmd.startswith("$(", i):
            j = _matching_paren(cmd, i + 1)
            subs.append(cmd[i + 2:j])
            if not in_d:
                end_token()
            i = j + 1
            continue
        if c == "`":
            j = cmd.find("`", i + 1)
            j = n if j < 0 else j
            subs.append(cmd[i + 1:j])
            if not in_d:
                end_token()
            i = j + 1
            continue
        if in_d:
            cur.append(c)
            has = True
            i += 1
            continue
        if c in " \t\r":
            end_token()
            i += 1
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            end_token()
            j = i + 2 + (1 if cmd[i + 2:i + 3] == "-" else 0)
            while j < n and cmd[j] in " \t":
                j += 1
            k = j
            while k < n and cmd[k] not in " \t\r\n;|&<>()":
                k += 1
            delim = cmd[j:k].strip("\"'")
            if delim:
                pending.append(delim)
            i = k
            continue
        if c in "<>":
            end_token()
            # `2>file` — the leading fd is part of the operator, not a word.
            if c == ">" and tokens and tokens[-1].isdigit():
                tokens.pop()
            while i < n and cmd[i] == c:
                i += 1
            tokens.append(c)
            continue
        if c == "\n":
            if pending:
                bodies, i = read_heredocs(cmd, i + 1, pending)
                docs.extend(bodies)
                pending = []
            else:
                i += 1
            end_segment(";")
            continue
        if c == ";":
            end_segment(";")
            i += 1
            continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            end_segment(cmd[i:i + 2])
            i += 2
            continue
        if c in "|&":
            end_segment(c)
            i += 1
            continue
        cur.append(c)
        has = True
        i += 1
    end_segment("")
    return segments, subs


def split_redirects(tokens):
    """(words, redirected-in operands, redirected-out operands).

    A secret on the `<` side is a read and counts. A secret on the `>` side is
    a write INTO the declaration — restoring a backup, minting a fresh seed —
    and refusing it would be the deny rule reappearing on the write path.
    """
    words, rin, rout = [], [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("<", ">"):
            target = rin if t == "<" else rout
            if i + 1 < len(tokens):
                target.append(tokens[i + 1])
            i += 2
            continue
        words.append(t)
        i += 1
    return words, rin, rout


def base_name(prog):
    b = os.path.basename(norm_text(prog)).lower()
    return b[:-4] if b.endswith(".exe") else b


def strip_lead(words):
    """Drop everything standing in front of the program without being it.

    Three kinds, and the second is the one that let `( cat SECRET )` through:
    assignments, shell grammar, and transparent prefixes. Grammar glued to the
    program (`(cat`) is peeled here too, because the lexer does not split it.
    """
    w, assigns = list(words), []
    while w:
        t = w[0]
        if ASSIGN.match(t):
            assigns.append(w.pop(0))
            continue
        head = t.lstrip("(){}")
        if not head:
            w.pop(0)
            continue
        if head != t:
            w[0] = head
            continue
        if t.lower() in GRAMMAR:
            w.pop(0)
            continue
        if base_name(t) in PREFIX:
            w.pop(0)
            while w and (ASSIGN.match(w[0]) or w[0].startswith("-")
                         or w[0].isdigit()):
                if ASSIGN.match(w[0]):
                    assigns.append(w[0])
                w.pop(0)
            continue
        break
    return w, assigns


def strip_xargs(w):
    """Drop `xargs` and its own flags so the program it runs can be named.

    `echo D:/wallet/x | xargs cat` put the path and the dumper in different
    segments, and `xargs` sat in PREFIX where it was treated as if it changed
    nothing — so the segment holding the path ran `echo` and the segment
    holding `cat` mentioned no path.
    """
    out = w[1:]
    while out and out[0].startswith("-"):
        flag = out.pop(0)
        if flag.lower() in XARGS_OPERANDS and out:
            out.pop(0)
    return out


def find_exec(args):
    """The command `find -exec` runs, with `{}` resolved to find's own paths."""
    paths = []
    for a in args:
        if a.startswith("-"):
            break
        paths.append(a)
    for i, a in enumerate(args):
        if a.lower() not in ("-exec", "-execdir", "-ok", "-okdir"):
            continue
        run = []
        for t in args[i + 1:]:
            if t in (";", "+", "\\", "\\;"):
                break
            run.append(" ".join(paths) if t == "{}" else t)
        return " ".join(run) if run else None
    return None


def nest_flag(name, arg):
    if name in ("powershell", "pwsh"):
        return bool(PS_C.match(arg))
    if name == "cmd":
        return arg.lower() in ("/c", "/k", "-c")
    return bool(POSIX_C.match(arg))


def nest_payload(name, args):
    """The command a wrapper carries, or None."""
    if name == "eval":
        return " ".join(args) or None
    for i, a in enumerate(args):
        if not nest_flag(name, a):
            continue
        for nxt in args[i + 1:]:
            if not nxt.startswith("-"):
                return nxt
        return None
    return None


def is_extract(name, words):
    """Whether an archiver invocation is unpacking rather than packing.

    `tar -C dir` means "go there and read" when packing and "put it there"
    when extracting, so the direction has to be settled before the flag can
    be read as a destination.
    """
    if name == "unzip":
        return not any(w in ("-l", "-Z", "-v") for w in words[1:])
    if name in ("7z", "7za"):
        return any(w.lower() in ("x", "e") for w in words[1:])
    if name == "zip":
        return False
    for w in words[1:]:
        if w.startswith("--"):
            if w in ("--extract", "--get"):
                return True
            continue
        core = w[1:] if w.startswith("-") else w
        # A short cluster only: `--exclude` carries an x and packs nothing.
        if "x" in core and re.fullmatch(r"[A-Za-z]*", core):
            return True
    return False


def token_roles(name, words):
    """Role per index in `words`: dest, consumed, src, arg — and the flags.

    The trailing positional counts as a destination only for a CARRY program,
    only when no source flag already claimed one. Both halves are load-bearing:
    `cp -t /tmp SECRET` names the destination first and leaves the credential
    trailing, and `tail -n 1 SECRET` has two positionals of which neither is
    a destination at all.
    """
    spec = FLOW.get(name, {})
    dest_flags = set(spec.get("dest", ()))
    if "extract" in spec:
        dest_flags = set(spec["extract"]) if is_extract(name, words) else set()
    src_flags = set(spec.get("src", ()))
    auth_flags = set(spec.get("auth", ()))

    roles = ["flag"] * len(words)
    if roles:
        roles[0] = "prog"
    positional, sourced, i = [], False, 1
    while i < len(words):
        t = words[i]
        if i + 1 < len(words) and t in dest_flags:
            roles[i + 1] = "dest"
            i += 2
            continue
        if i + 1 < len(words) and t in auth_flags:
            roles[i + 1] = "consumed"
            i += 2
            continue
        if i + 1 < len(words) and t in src_flags:
            roles[i + 1] = "src"
            sourced = True
            i += 2
            continue
        if not t.startswith("-"):
            roles[i] = "dest" if spec.get("posdest") else "arg"
            positional.append(i)
        i += 1
    if name in CARRY and not sourced and len(positional) >= 2:
        roles[positional[-1]] = "dest"
    return roles


def gather(entries, name, words, rin, extra, assigns, docs):
    """(broad entry, broad match, [(entry, path, role)] for every real read).

    Two tiers, and the split is the whole not-eager half of the design. The
    broad tier decides whether this command is analysed at all — one hit
    anywhere, however embedded. The read tier decides the verdict, and only an
    OPERAND or a covering directory qualifies.
    """
    reads, broad, matched = [], None, None
    scan = ([(t, r) for t, r in zip(words, token_roles(name, words))]
            + [(t, "arg") for t in rin + extra]
            + [(t.split("=", 1)[1], "arg") for t in assigns if "=" in t])
    for text, _ in scan + [(d, "doc") for d in docs]:
        if broad is None:
            broad, matched = secret_hit(entries, text)
    for text, role in scan:
        if role == "prog":
            continue
        e, p = secret_operand(entries, text)
        kind = "operand"
        if not e:
            e, p = cover_hit(entries, text)
            kind = "cover"
        if e:
            reads.append((e, p, role, kind))
    if broad is None and reads:
        # `secrets` holds `secrets/*.txt` without matching it as text, so a
        # covering directory is its own evidence that this command is worth
        # analysing — otherwise `grep -r . secrets` never gets asked about.
        broad, matched = reads[0][0], reads[0][1]
    return broad, matched, reads


def classify(segments, idx, entries, depth):
    words, rin, rout = split_redirects(segments[idx].tokens)
    docs = segments[idx].docs

    # `env -S "cat SECRET"` splits its operand into a whole command line, so
    # the payload never reaches the program position the prefix strip resolves.
    for i in range(len(words) - 2):
        if base_name(words[i]) == "env" and words[i + 1] in ("-S", "--split-string"):
            found = analyze(words[i + 2], entries, depth + 1)
            if found:
                return found

    w, assigns = strip_lead(words)
    extra = []
    if w and base_name(w[0]) == "xargs":
        if idx > 0 and segments[idx - 1].sep == "|":
            extra, _, _ = split_redirects(segments[idx - 1].tokens)
        w = strip_xargs(w)
        w, more = strip_lead(w)
        assigns += more
        # Bare `xargs` runs echo, which prints the path and not the bytes.
        if not w:
            return None

    line = collapse(" ".join(w))

    # A declared emitter: the credential is in the command's OUTPUT, not in
    # its arguments, so there is no path to match. Permitted when something
    # stands between it and the transcript — the filter-at-the-source
    # discipline that replaced `Bash(railway:*)`. Matched against the STRIPPED
    # words, because the field this retires was defeated by the word `env`.
    helpish = any(t.lower() in ("--help", "-h", "help", "/?") for t in w)
    real_out = [t for t in rout if norm_text(t).lower() not in TRANSCRIPT_SINKS]
    for e in entries:
        if helpish or not any(rx.fullmatch(line) for rx in e["emits"]):
            continue
        downstream = None
        if segments[idx].sep == "|" and idx + 1 < len(segments):
            nxt, _, _ = split_redirects(segments[idx + 1].tokens)
            nxt, _ = strip_lead(nxt)
            downstream = base_name(nxt[0]) if nxt else None
        if real_out or (downstream and downstream not in EMIT):
            continue
        return Ref("emits", e, line, None, line)

    if not w:
        if any(secret_hit(entries, t)[0] for t in rin):
            return Ref("opaque", *secret_hit(entries, rin[0]),
                       "a redirection with no program to name", line)
        # `SEED=D:/wallet/x` names a path. A path is not the bytes.
        return None

    prog, args = w[0], w[1:]
    name = base_name(prog)

    if name in NEST:
        for payload in [nest_payload(name, args)] + docs + rin:
            if payload is None:
                continue
            found = analyze(payload, entries, depth + 1)
            if found:
                return found
    if name == "find":
        payload = find_exec(args)
        if payload:
            found = analyze(payload, entries, depth + 1)
            if found:
                return found

    entry, matched, reads = gather(
        entries, name, w, rin, extra, assigns, [] if name in NEST else docs)
    if not entry:
        return None

    def opaque(detail):
        return Ref("opaque", entry, matched, detail, line)

    if secret_operand(entries, prog)[0]:
        return opaque("the credential itself is in program position")
    if any(ch in prog for ch in "$`*"):
        return opaque("the program is an expansion this guard cannot resolve")

    if name == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub.lower() not in GIT_EMIT:
            return None
    elif name not in EMIT and name not in CARRY:
        return None

    taken = [r for r in reads if r[2] not in ("dest", "consumed")]
    if not taken:
        return None
    e0, p0, _, kind = taken[0]

    if name in CARRY:
        dests = [w[i] for i, r in enumerate(token_roles(name, w)) if r == "dest"]
        if dests and all(secret_operand(entries, d)[0] for d in dests):
            return None
        return Ref("carry", e0, p0,
                   f"`{name}` makes the bytes leave the declaration", line)
    if kind == "cover":
        verb = "packs" if name in ARCHIVE else "reads"
        return Ref("cover", e0, p0,
                   f"`{name}` {verb} every file under `{p0}`, and the "
                   f"declaration lives there", line)
    if name in ARCHIVE:
        return Ref("emit", e0, p0,
                   f"`{name}` packs the file into an archive whose bytes this "
                   f"guard cannot follow", line)
    return Ref("emit", e0, p0, f"`{name}` prints the file it is handed", line)


def analyze(cmd, entries, depth=0):
    """The verdict on one command line: a Ref to refuse, or None to permit."""
    if depth > 4:
        e, m = secret_hit(entries, cmd)
        if e:
            return Ref("opaque", e, m,
                       "nesting deeper than this guard follows", collapse(cmd))
        return None
    segments, subs = lex(cmd or "")
    for inner in subs:
        found = analyze(inner, entries, depth + 1)
        if found:
            return found
    for i in range(len(segments)):
        found = classify(segments, i, entries, depth)
        if found:
            return found
    return None


def tool_read(entries, tool, path):
    """Read has no safe shape: its whole output is the file."""
    entry, matched = secret_hit(entries, path or "")
    if not entry:
        return None
    return Ref("tool", entry, matched,
               f"the {tool} tool's entire output IS the file", f"{tool}({path})")


def tool_grep(entries, args):
    """Grep denies on the path, and on the directory ABOVE it in content mode.

    `Grep{pattern:'.', path:'secrets'}` never names the declaration and prints
    it anyway. But the default output mode prints file NAMES, and a name is not
    the bytes — denying every Grep in a repo that declares one relative path is
    the over-block this guard exists to retire. So containment is asked only
    when `output_mode` is explicitly `content`, which is the mode that puts
    file bytes in the transcript.
    """
    path = args.get("path")
    ref = tool_read(entries, "Grep", path)
    if ref or (args.get("output_mode") or "") != "content":
        return ref
    for cand in (path or ".", args.get("glob") or ""):
        entry, where = cover_hit(entries, cand, root_ok=True)
        if entry:
            return Ref("cover", entry, where,
                       f"a content Grep under `{where}` reads every file below "
                       f"it, and the declaration lives there", f"Grep({cand})")
    return None


def dispatch(payload, entries):
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return None
    if tool == "Bash":
        return analyze(args.get("command") or "", entries)
    if tool in ("Read", "NotebookRead"):
        return tool_read(entries, tool, args.get("file_path"))
    if tool == "Grep":
        return tool_grep(entries, args)
    return None


def render(ref):
    out = ["secret-guard: REFUSED — this would put a declared credential where "
           "the transcript can see it", ""]
    # For an emitter the match IS the command line, so repeating it on the id
    # row would print it three times in a ten-line message.
    out.append(f"  {ref.entry['id']}"
               + (f": {ref.matched}" if ref.matched and ref.kind != "emits" else ""))
    if ref.entry["why"]:
        out.append(f"      {ref.entry['why']}")
    out.append("")
    out.append(f"  {ref.line}")

    if ref.kind == "emits":
        out += [
            "  This command prints credential material straight to stdout.",
            "",
            "  Filter at the source. Pipe it through something that prints key",
            "  NAMES for everything and VALUES only for a non-secret allowlist:",
            f"      {ref.line} | python scripts/filter_env.py",
            "  The command still runs. Only the non-secret half is quoted back.",
        ]
    elif ref.kind == "tool":
        out += [
            f"  {ref.detail}, so there is no safe shape for it here — unlike",
            "  Bash, where the path can be handed to code that emits a derivation.",
            "",
            "  Read it through a program instead:",
            f"      python -c \"from your_module import load_wallet; "
            f"print(load_wallet('{ref.matched}').address)\"",
        ]
    elif ref.kind == "cover":
        out += [
            f"  {ref.detail}.",
            "",
            "  Name the files you actually need, or exclude the declaration —",
            "  a directory that holds a credential is not a safe thing to sweep.",
        ]
    elif ref.kind == "opaque":
        out += [
            f"  Refused because {ref.detail}.",
            "",
            "  Ambiguity about where the bytes go is refused; ambiguity about what",
            "  a NAMED program does is not. Name the program and pass it the path:",
            f"      python -m your_module.derive {ref.matched}",
        ]
    else:
        out += [
            f"  {ref.detail}, and the secret never has to.",
            "",
            "  Pass the PATH to code that emits only a public derivation:",
            f"      python -c \"from your_module import load_wallet; "
            f"print(load_wallet('{ref.matched}').address)\"",
            "  The same file is read. Only the derivation reaches this context.",
        ]

    out += [
        "",
        f"  If the declaration is wrong, fix {MANIFEST} in a reviewed diff — that",
        "  is the one place a change to what counts as a credential should land.",
    ]
    return "\n".join(out)


def deny(reason):
    """PreToolUse's documented refusal channel.

    Permit is SILENCE and never `"allow"`: answering allow does not merely
    stay out of the way, it overrides the permission system that would
    otherwise have asked — a bigger hole than the one this closes.
    """
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, separators=(",", ":")))


def listing(entries):
    print(f"secret-guard: {len(entries)} declaration(s) in {MANIFEST}")
    for e in entries:
        print(f"  {e['id']}")
        for p in e["rawPaths"]:
            print(f"      paths: {p}")
        for c in e["rawEmits"]:
            print(f"      emits: {c}")
            if re.match(r"^[^-*]*\*$", c):
                # `railway variables*` refuses `railway variables --help` and
                # `--set`, which is `Bash(railway:*)` narrowed by one word. The
                # declaration should name the invocation that DUMPS.
                print("             this covers every invocation of that "
                      "subcommand, including --help —")
                print("             name the dumping one instead, e.g. "
                      f"'{c[:-1]} --json*'")
        if not e["why"]:
            # Named, because the refusal is the only moment anyone reads this
            # file, and a refusal that cannot say what is at stake gets argued
            # with instead of obeyed.
            print("      no 'why' — a refusal here cannot say what is at stake")
        else:
            print(f"      why:   {e['why']}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hook", action="store_true",
                   help="read a PreToolUse payload on stdin")
    g.add_argument("--command", help="decide one command line")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()

    # FPL_DISABLE disarms HOOKS, which is what the five hook scripts read it
    # for. It must not disarm the CLI: a corrupt manifest denies every call
    # for a whole session, and `--list` is the one command that catches the
    # typo — run, of course, by an operator who has already set the flag.
    if a.hook and os.environ.get("FPL_DISABLE") == "1":
        return 0

    payload = {}
    if a.hook:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}
    # The tool call's own cwd outranks an ambient env var, and the CLI default
    # is `.` like every sibling guard. CLAUDE_PROJECT_DIR is always set inside
    # a session, so reading it first made an unrooted call render a verdict
    # about a tree it was not checking.
    root = a.root
    if not root and a.hook:
        root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    root = root or "."

    # Dormant before anything is parsed, so a repo that declares nothing costs
    # one stat — the same bargain exec-attest.sh makes on .fluxpoint-gates.json.
    if not os.path.exists(os.path.join(root, MANIFEST)):
        if not a.hook:
            print(f"secret-guard: no {MANIFEST} in this repo — nothing is "
                  f"declared, so nothing is guarded")
        return 0

    try:
        entries = compile_entries(root, load_manifest(root))
    except ManifestError as e:
        note = (f"{MANIFEST} is unusable, so nothing is declared and nothing is "
                f"guarded: {e}. A secrets manifest fails closed — every command "
                f"is refused until the file parses.")
        if a.hook:
            deny(note)
            return 0
        print(f"secret-guard: {note}", file=sys.stderr)
        return 1

    if a.list:
        return listing(entries)

    if a.hook:
        ref = dispatch(payload, entries)
        if ref:
            deny(render(ref))
        return 0

    ref = analyze(a.command, entries)
    if ref:
        print(render(ref), file=sys.stderr)
        return 1
    print("secret-guard: permitted — nothing here would put a declared "
          "credential in the transcript")
    return 0


if __name__ == "__main__":
    sys.exit(main())
