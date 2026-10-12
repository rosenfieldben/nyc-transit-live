#!/usr/bin/env bash
# Re-derive every number in section 2 of docs/reviews/audit-2026-10-12.md.
#
# Run from the repository root:
#     bash docs/reviews/audit-2026-10-12/run_all.sh
#
# Each script is a regression check on a record, not a one-off print: it exits 0
# while the finding still behaves the way the table records, and non-zero the
# moment the code stops matching. So a red run here means the table is out of date,
# which is the only way a verification record stays worth anything.
#
# A GREEN RUN IS NOT A CLEAN BILL OF HEALTH, and the summary says so in its own
# words. A record of an open defect passes for exactly as long as the defect is
# still there, so "11 passed" on its own reads as good news when it means eleven open
# records. The summary therefore tallies the passing rows by the first word after
# DISPOSITION: (VERIFIED, PARTLY, REFUTED, FIXED, and any other word a script
# prints, counted rather than dropped), and that tally is the line to read.
#
# Requirements: a Python environment with backend/requirements-dev.txt installed
# (PY below, default .venv/bin/python) and Node 20 or newer for the .mjs scripts.
# Nothing here needs the network, a credential or a live upstream. In particular
# nothing here can spend an NJ Transit mint: every script that imports the backend
# installs the containment in docs/reviews/audit-2026-09-05/_hermetic.py first, and
# the NJ Transit scripts drive fake transports that refuse any real NJ Transit host.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
NODE="${NODE:-node}"
DIR="docs/reviews/audit-2026-10-12"

# command -v, not [ -x ], for the reason the 2026-09-05 run_all.sh gives: CI passes a
# bare interpreter NAME (PY=python) and lets PATH resolve it.
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "no python at $PY; set PY=/path/to/python (needs backend/requirements-dev.txt)" >&2
  exit 2
fi

pass=0
fail=0
failed_names=()

# The tally. Plain counters rather than an associative array, because macOS still
# ships bash 3.2, which has none; any word outside the four is kept in a list and
# counted at the end, so a new disposition word shows up instead of vanishing.
n_verified=0
n_partly=0
n_refuted=0
n_fixed=0
other_words=()

tally() {
  case "$1" in
    VERIFIED) n_verified=$((n_verified + 1)) ;;
    PARTLY) n_partly=$((n_partly + 1)) ;;
    REFUTED) n_refuted=$((n_refuted + 1)) ;;
    FIXED) n_fixed=$((n_fixed + 1)) ;;
    *) other_words+=("$1") ;;
  esac
}

# The first word after "DISPOSITION:", without trailing punctuation, so that
# "VERIFIED," and "PARTLY VERIFIED." count as VERIFIED and PARTLY. A passing script
# that prints no DISPOSITION line is counted as UNSTATED rather than guessed at.
first_word() {
  local line="$1" word
  if [ -z "$line" ]; then
    printf 'UNSTATED'
    return
  fi
  word="${line#DISPOSITION:}"
  word="${word#"${word%%[![:space:]]*}"}"
  word="${word%%[[:space:]]*}"
  word="${word%%[.,;:]*}"
  printf '%s' "${word:-UNSTATED}"
}

run() {
  local runner="$1" script="$2" label="$3"
  printf '%-6s %-44s ' "$label" "$script"
  local log line
  log="$(mktemp)"
  if "$runner" "$DIR/$script" >"$log" 2>&1; then
    line="$(grep -m1 '^DISPOSITION:' "$log")"
    printf 'PASS  %s\n' "$(printf '%s' "$line" | cut -c1-96)"
    pass=$((pass + 1))
    tally "$(first_word "$line")"
  else
    printf 'FAIL\n'
    sed 's/^/         | /' "$log" | tail -30
    fail=$((fail + 1))
    failed_names+=("$label")
  fi
  rm -f "$log"
}

run "$PY"   a01_njt_fresh_token_rejected.py      A01
run "$PY"   a02_unreadable_trips_promoted.py     A02
run "$PY"   a02b_njt_shapes_zlib_error.py        A02b
run "$PY"   a03_empty_board_hides_failed_feed.py A03
run "$NODE" a04_clock_offset_minimum.mjs         A04
run "$NODE" a06_frontend_static_once.mjs         A06
run "$PY"   a09_unbounded_upstream.py            A09
run "$PY"   a10_headerless_lirr_clock.py         A10
run "$PY"   a11_estimated_lirr_train_numbers.py  A11
run "$NODE" a12_path_fallback_color.mjs          A12
run "$PY"   a13_env_seams_accepts_nonpositive.py A13

summary="$n_verified VERIFIED, $n_partly PARTLY, $n_refuted REFUTED, $n_fixed FIXED"
if [ "${#other_words[@]}" -gt 0 ]; then
  while read -r count word; do
    summary="$summary, $count $word"
  done < <(printf '%s\n' "${other_words[@]}" | sort | uniq -c)
fi

echo
echo "$pass passed: $summary"
if [ "$fail" -ne 0 ]; then
  echo "$fail failed: ${failed_names[*]}"
  echo "still-matching records: $pass; records that no longer match: ${failed_names[*]}"
  exit 1
fi
