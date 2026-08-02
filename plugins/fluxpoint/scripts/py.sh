#!/usr/bin/env bash
# Run one of this directory's Python scripts portably.
#
#   py.sh compile-graph.py WORK.md --check
#
# The commands and agents invoke these scripts as literal instructions rather
# than through lib.sh, so they cannot pick up a shell-resolved interpreter.
# Two things have to be settled before the interpreter starts, and this is the
# only shared point where both can be:
#
#   * the name — `python3` does not exist on a standard Windows install
#   * stdio encoding — Windows writes cp1252, so a non-ASCII character in any
#     output a caller parses comes back as a byte that no UTF-8 match sees
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: py.sh <script.py> [args...]" >&2
  exit 64
fi

if [ -z "${FPL_PY:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then FPL_PY=python3
  elif command -v python >/dev/null 2>&1; then FPL_PY=python
  else echo "fluxpoint: no python interpreter on PATH" >&2; exit 127
  fi
fi
export PYTHONIOENCODING=utf-8

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="$here/$1"
if [ ! -f "$target" ]; then
  echo "fluxpoint: no such script: $1" >&2
  exit 66
fi
shift
exec "$FPL_PY" "$target" "$@"
