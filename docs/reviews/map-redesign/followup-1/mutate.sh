#!/usr/bin/env bash
# One mutation, run in a worktree detached at a named commit. MR5's runner (map-redesign/mr5/
# mutate.sh), carried forward with four changes this follow-up needed, each for a measured reason.
#
#   1. THE PORT IS CHECKED, NEVER FREED. MR5's runner killed whatever held the suite's port with
#      `fuser -n tcp`, which does not exist on macOS, and on a machine shared with other sessions
#      "whatever holds the port" can be someone else's run: this runner's first version killed
#      without asking, and its review said so (the ledger's V5). So a held port before a gate is
#      a RUN FAILED naming the holder, every gate runs with CI=1 so Playwright never reuses a
#      server, and Playwright stops the server it started itself.
#   2. THE NODE TIER GETS A REAL-PATH TMPDIR. tests/nodetier.test.js compares a module path it
#      resolved against one it built from os.tmpdir(), and on macOS the default TMPDIR is a
#      symlink (/var -> /private/var), so that test fails on an unmutated tree here and would
#      read as a kill of any row gated on the node tier.
#   3. A GATE THAT FAILED ONLY ON THE SUITE'S CLOCK RACE IS RUN AGAIN, NOT COUNTED. The frozen-clock
#      boots install the clock at the frozen time and then pause at it, and under load pauseAt
#      throws "Cannot fast-forward to the past" before the page loads (the ledger's flake list has
#      it). A failure with only that signature says nothing about the mutation, so the gate is
#      re-run, up to three attempts, and a gate still racing after three is a RUN FAILED.
#   4. A DEATH IS A FAILING TEST, NOT A FAILING COMMAND. A gate that exits non-zero with no failing
#      test in its log (a webServer that could not bind, a config that did not load, an npx that
#      could not find Playwright) said nothing about the mutation, so it is a RUN FAILED. Only
#      Playwright's "N failed" summary, or node's "fail N" with N above zero, is a kill, and its
#      first error lines are printed so the table can say which assertion did it. The runner also
#      refuses to start without the invoking checkout's node_modules, because a dangling link let
#      npx reach for the network instead, and npm is told to stay offline besides (V6, which the
#      verifier refuted for this checkout and which is kept as hardening).
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
PORT=5173

# Who holds the port, as "pid command", or nothing.
holder() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 { print $2, $1; exit }'; }

[ -x "$ROOT/node_modules/.bin/playwright" ] || { echo "RUN FAILED $LABEL: no node_modules/.bin/playwright in $ROOT"; exit 2; }

TREE="$(mktemp -d "$BASE_TMP/f1mut.XXXXXX")"
NODE_TMP="$(mktemp -d "$BASE_TMP/f1node.XXXXXX")"
cleanup() { cd "$ROOT" || true; git worktree remove --force "$TREE" >/dev/null 2>&1 || true; rm -rf "$TREE" "$NODE_TMP"; }
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
    held="$(holder)"
    if [ -n "$held" ]; then
      echo "RUN FAILED $LABEL: port $PORT is held by $held, and this runner does not kill what it did not start"
      exit 2
    fi
    if CI=1 npm_config_offline=true TMPDIR="$NODE_TMP/" bash -c "$gate" > "$TREE/gate.log" 2>&1; then
      break
    fi
    if grep -q "Cannot fast-forward to the past" "$TREE/gate.log" \
      && ! grep -E "^\s*Error: " "$TREE/gate.log" | grep -vq "Cannot fast-forward to the past"; then
      if [ "$attempt" -ge 3 ]; then
        echo "RUN FAILED $LABEL: the gate raced its clock on all three attempts"
        exit 2
      fi
      echo "    the clock race, not the mutation (attempt $attempt); running the gate again"
      attempt=$((attempt + 1))
      continue
    fi
    if ! grep -Eq "^\s+[0-9]+ failed|^ℹ fail [1-9]" "$TREE/gate.log"; then
      echo "RUN FAILED $LABEL: the gate failed with no failing test, so it said nothing about the mutation"
      grep -E "^\s*Error" "$TREE/gate.log" | head -4 | sed 's/^/    | /'
      exit 2
    fi
    echo "DIED $LABEL on: $gate"
    grep -E "^\s*Error|^\s+Expected|^\s+Received|^\s+[0-9]+ (passed|failed)|^not ok|^✖ " "$TREE/gate.log" \
      | grep -v "^✖ failing tests" | head -8 | sed 's/^/    | /'
    exit 0
  done
done
echo "SURVIVED $LABEL: every gate passed with the mutation in place"
exit 1
