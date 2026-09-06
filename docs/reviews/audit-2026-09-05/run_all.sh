#!/usr/bin/env bash
# Re-derive every number in section 2 of docs/reviews/audit-2026-09-05.md.
#
# Run from the repository root:
#     bash docs/reviews/audit-2026-09-05/run_all.sh
#
# Each script is a regression check on the audit record, not a one-off print: it
# exits 0 while the finding still behaves the way the table records, and non-zero
# the moment the code stops matching. So a red run here means the table is out of
# date, which is the only way a verification record stays worth anything.
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

if [ ! -x "$PY" ]; then
  echo "no python at $PY; set PY=/path/to/python (needs backend/requirements-dev.txt)" >&2
  exit 2
fi

pass=0
fail=0
failed_names=()

run() {
  local runner="$1" script="$2" label="$3"
  printf '%-6s %-44s ' "$label" "$script"
  local log
  log="$(mktemp)"
  if "$runner" "$DIR/$script" >"$log" 2>&1; then
    printf 'PASS  %s\n' "$(grep -m1 '^DISPOSITION:' "$log" | cut -c1-96)"
    pass=$((pass + 1))
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

echo
echo "$pass passed, $fail failed"
if [ "$fail" -ne 0 ]; then
  echo "still-matching findings: $pass; findings whose record no longer matches the code: ${failed_names[*]}"
  exit 1
fi
