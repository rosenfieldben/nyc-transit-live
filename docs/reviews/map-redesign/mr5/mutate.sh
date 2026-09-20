#!/usr/bin/env bash
# One mutation, run in a worktree detached at a named commit.
#
# WHY THIS IS A SCRIPT AND NOT A PROCEDURE IN A COMMENT. MR4's finding F19: mutation M35 had
# reported `ANCHOR MISS` and exited without testing anything for a whole round, because round 1
# turned the line it anchored on into a block and nobody re-ran the table. Standing rule 6 came
# out of that: a mutation whose anchor misses is a mutation that did not run, so the whole table
# is re-run before every push and an ANCHOR MISS is a failure of the run rather than a survivor.
# A table that lives only in a markdown file cannot be re-run; this can.
#
# Usage:
#   mutate.sh <sha> <label> <file> <anchor-file> <replacement-file> <gate...>
#
# <anchor-file> and <replacement-file> hold the exact text to find and to put in its place, so
# neither has to survive shell quoting. The anchor must match EXACTLY ONCE: zero is an ANCHOR
# MISS and more than one is an ambiguous mutation, and both exit non-zero without running a gate.
#
# Exit codes: 0 the mutation DIED (a gate rejected it, which is what a guard is for), 1 it
# SURVIVED (every gate passed with the defect in place), 2 the run itself failed (anchor miss,
# wrong sha, gate could not start). 2 is never a result about the code.
set -uo pipefail

SHA="${1:?sha}"; LABEL="${2:?label}"; TARGET="${3:?file}"; ANCHOR="${4:?anchor file}"; REPL="${5:?replacement file}"
shift 5
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TREE="$(mktemp -d /tmp/mr5mut.XXXXXX)"
PORT="${E2E_PORT:-5173}"

# Kill whatever holds the suite's port, BY PORT. Never `pkill -f playwright`: that pattern
# matches the shell running this script and has killed a session here before (exit 144). `ss`
# reports no sockets at all in this container, so fuser is the reader that works.
killserve() {
  local pid
  pid="$(fuser -n tcp "$PORT" 2>/dev/null | tr -d ' ' | awk '{print $1}')"
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null && sleep 2
  return 0
}

cleanup() { cd "$ROOT" || true; killserve; git worktree remove --force "$TREE" >/dev/null 2>&1 || true; rm -rf "$TREE"; }
trap cleanup EXIT

cd "$ROOT"
git worktree add --detach "$TREE" "$SHA" >/dev/null 2>&1 || { echo "RUN FAILED $LABEL: no worktree at $SHA"; exit 2; }
ln -s "$ROOT/node_modules" "$TREE/node_modules"

# THE SHA IS ECHOED AND COMPARED, not assumed: a worktree at the wrong commit is a mutation of
# something else, and it would report a perfectly plausible verdict about code nobody is shipping.
GOT="$(git -C "$TREE" rev-parse HEAD)"
WANT="$(git rev-parse "$SHA")"
echo "  worktree HEAD: $GOT"
[ "$GOT" = "$WANT" ] || { echo "RUN FAILED $LABEL: worktree is at $GOT, wanted $WANT"; exit 2; }

python3 - "$TREE/$TARGET" "$ANCHOR" "$REPL" "$LABEL" <<'PY' || exit 2
import sys, pathlib
target, anchor_f, repl_f, label = sys.argv[1:5]
src = pathlib.Path(target).read_text()
anchor = pathlib.Path(anchor_f).read_text()
if anchor.endswith("\n") and not src.endswith("\n"):
    anchor = anchor.rstrip("\n")
n = src.count(anchor)
if n != 1:
    print(f"ANCHOR MISS {label}: anchor in {target} matched {n} times; expected exactly 1")
    sys.exit(2)
pathlib.Path(target).write_text(src.replace(anchor, pathlib.Path(repl_f).read_text()))
print(f"  mutated {target}")
PY

killserve
cd "$TREE"
for gate in "$@"; do
  echo "  gate: $gate"
  if ! CI=1 E2E_PORT="$PORT" bash -c "$gate" > "$TREE/gate.log" 2>&1; then
    echo "DIED $LABEL on: $gate"
    grep -E "^ *[0-9]+ (passed|failed)|^not ok|✘|Error:" "$TREE/gate.log" | head -6 | sed 's/^/    | /'
    exit 0
  fi
done
echo "SURVIVED $LABEL: every gate passed with the mutation in place"
exit 1
