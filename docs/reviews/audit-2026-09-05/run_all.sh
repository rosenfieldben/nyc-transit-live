#!/usr/bin/env bash
# Re-derive every number in section 2 of docs/reviews/audit-2026-09-05.md, and the
# buses row of the age policy in section 3.3 of docs/design/freshness-contract.md.
#
# Run from the repository root:
#     bash docs/reviews/audit-2026-09-05/run_all.sh
#
# Each script is a regression check on a record, not a one-off print: it exits 0
# while the finding still behaves the way the table records, and non-zero the
# moment the code stops matching. The one PROBE here (6.0) has no before and no
# after and is not expected to start failing when a fix lands; it fails only if
# its committed capture stops supporting the row it was taken for. So a red run here means the table is out of
# date, which is the only way a verification record stays worth anything.
#
# A GREEN RUN IS NOT A CLEAN BILL OF HEALTH. A record of an open defect passes for
# exactly as long as the defect is still there, so the summary tallies the passing
# rows by the first word after DISPOSITION: (VERIFIED, PARTLY, REFUTED, FIXED, and
# any other word a script prints, such as the probe's MEASURED, counted rather than
# dropped). The 2026-10-12 audit made the point about this job, and it was right.
#
# Requirements: a Python environment with backend/requirements-dev.txt installed
# (PY below, default .venv/bin/python) and Node 20 or newer for the two .mjs
# scripts. Nothing here needs the network, a credential or a live upstream. In
# particular nothing here can spend an NJ Transit mint: the NJT scripts drive
# fake transports and fake mint callbacks, and they refuse to run if a real
# credential is present.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
NODE="${NODE:-node}"
DIR="docs/reviews/audit-2026-09-05"

# command -v, not [ -x ], because PY may be a bare interpreter NAME rather than a path:
# CI sets PY=python and lets the runner's PATH resolve it, exactly as NODE above is a bare
# "node". A -x test on "python" asks whether ./python exists in the repository root, which it
# does not, so the guard rejected a perfectly good interpreter and the audit-records job went
# red on its first run. command -v answers for both forms: it resolves a name on PATH, and for
# a value containing a slash it still requires that the file be executable.
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

run "$PY"   f01_lirr_gps_observation_age.py      F01
run "$PY"   f02_canceled_railroad_gps.py         F02
run "$PY"   f03_arrivals_content_freshness.py    F03
run "$PY"   f04_airtrain_historical_reference.py F04
run "$PY"   f05_njt_failed_mint_storm.py         F05
run "$PY"   f06_partial_startup_no_recovery.py   F06
run "$PY"   f07_static_never_refreshes.py        F07
run "$PY"   f08_bad_geometry_replaces_good.py    F08
run "$PY"   f09_nonfinite_coordinates.py         F09
run "$PY"   f10_deadline_health_disagreement.py  F10
run "$NODE" f11_station_panel_hides_alerts.mjs   F11
run "$NODE" f12_stale_error_body_overwrites.mjs  F12
run "$PY"   f13_healthcheck_recovery_gap.py      F13
run "$PY"   f14_accessibility_gaps.py            F14
run "$PY"   probe_bus_observation_clock.py       6.0

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
