#!/usr/bin/env bash
# One mutation, run in a worktree detached at a named commit. MR5's runner (map-redesign/mr5/
# mutate.sh), carried forward with three changes this follow-up needed, each for a measured reason.
#
#   1. THE PORT IS FREED WITH lsof, falling back to fuser. MR5's runner ran in a Linux container
#      where `fuser -n tcp` was the reader that worked; on macOS that flag does not exist, and a
#      server left on the port would be REUSED by a gate outside CI mode, which tests the
#      unmutated tree. The gate also runs with CI=1 so Playwright never reuses one at all.
#   2. THE NODE TIER GETS A REAL-PATH TMPDIR. tests/nodetier.test.js compares a module path it
#      resolved against one it built from os.tmpdir(), and on macOS the default TMPDIR is a
#      symlink (/var -> /private/var), so that test fails on an unmutated tree here and would
#      read as a kill of any row gated on the node tier.
#   3. A GATE THAT FAILED ONLY ON THE SUITE'S CLOCK RACE IS RUN AGAIN, NOT COUNTED. The frozen-clock
#      boots install the clock at the frozen time and then pause at it, and under load pauseAt
#      throws "Cannot fast-forward to the past" before the page loads (the ledger's flake list has
#      it). A failure with that signature says nothing about the mutation, so the gate is re-run,
#      up to three attempts, and a gate still racing after three is a RUN FAILED rather than a
#      verdict. Any other failure is a death, and its first lines are printed so the table can
#      say which assertion killed it.
#
# Usage:
#   mutate.sh <sha> <label> <file> <anchor-file> <replacement-file> <gate...>
#
# The anchor must match EXACTLY ONCE (standing rule 6): zero is an ANCHOR MISS and more than one is
# ambiguous, and both exit 2 without running a gate.
#
# Exit codes: 0 DIED, 1 SURVIVED, 2 the run itself failed. 2 is never a result about the code.
set -uo pipefail

SHA="${1:?sha}"; LABEL="${2:?label}"; TARGET="${3:?file}"; ANCHOR="${4:?anchor file}"; REPL="${5:?replacement file}"
shift 5
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
BASE_TMP="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
TREE="$(mktemp -d "$BASE_TMP/f1mut.XXXXXX")"
NODE_TMP="$(mktemp -d "$BASE_TMP/f1node.XXXXXX")"
PORT=5173

killserve() {
  local pids
  pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || fuser -n tcp "$PORT" 2>/dev/null || true)"
  if [ -n "${pids// /}" ]; then kill $pids 2>/dev/null; sleep 2; fi
  return 0
}

cleanup() { cd "$ROOT" || true; killserve; git worktree remove --force "$TREE" >/dev/null 2>&1 || true; rm -rf "$TREE" "$NODE_TMP"; }
trap cleanup EXIT

cd "$ROOT"
git worktree add --detach "$TREE" "$SHA" >/dev/null 2>&1 || { echo "RUN FAILED $LABEL: no worktree at $SHA"; exit 2; }
ln -s "$ROOT/node_modules" "$TREE/node_modules"

# THE SHA IS ECHOED AND COMPARED, not assumed (RULE 0b's lesson, from MR3).
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

cd "$TREE"
for gate in "$@"; do
  echo "  gate: $gate"
  attempt=1
  while :; do
    killserve
    if CI=1 TMPDIR="$NODE_TMP/" bash -c "$gate" > "$TREE/gate.log" 2>&1; then
      break
    fi
    if grep -q "Cannot fast-forward to the past" "$TREE/gate.log" \
      && ! grep -E "^\s+Error: " "$TREE/gate.log" | grep -vq "Cannot fast-forward to the past"; then
      if [ "$attempt" -ge 3 ]; then
        echo "RUN FAILED $LABEL: the gate raced its clock on all three attempts"
        exit 2
      fi
      echo "    the clock race, not the mutation (attempt $attempt); running the gate again"
      attempt=$((attempt + 1))
      continue
    fi
    echo "DIED $LABEL on: $gate"
    grep -E "^\s+Error: |^\s+Expected|^\s+Received|^\s+[0-9]+ (passed|failed)|^not ok|^✖ " "$TREE/gate.log" \
      | grep -v "^✖ failing tests" | head -8 | sed 's/^/    | /'
    exit 0
  done
done
echo "SURVIVED $LABEL: every gate passed with the mutation in place"
exit 1
