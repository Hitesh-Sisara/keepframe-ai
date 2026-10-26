#!/usr/bin/env bash
# Privacy sweep: tracked files must not carry customer identifiers or imagery.
# Real event ids come from a gitignored list (data/selected_events.json or $REAL_IDS_FILE); synthetic ev_synth_* are excluded.
set -uo pipefail   # no -e: grep returns 1 on no match, which is the good case
cd "$(git rev-parse --show-toplevel)"
fail=0
echo "== tracked images outside the allowlist"
git ls-files | grep -iE '\.(png|jpg|jpeg|webp|heic)$' \
  | grep -vE '^(docs/screenshots/ui-(approvals|bench|events|group-trace)\.png|docs/screenshots/ui-bench-graviton-real\.png|docs/(architecture|agent_workflow|agent_workflow_compact)\.png|tests/fixtures/faces/)' \
  | sed 's/^/  UNEXPECTED: /' > /tmp/ps_img.txt || true; cat /tmp/ps_img.txt; [ -s /tmp/ps_img.txt ] && fail=1
echo "== e-mails / whitelabel ids / source user ids"
git grep -nE '[A-Za-z0-9._%+-]+@(gmail|yahoo|hotmail|outlook)\.[a-z]+|whitelabel-[1-9][0-9]*|"userId"\s*:' -- . ':!uv.lock' | sed 's/^/  /' > /tmp/ps_pii.txt || true; cat /tmp/ps_pii.txt; [ -s /tmp/ps_pii.txt ] && fail=1
IDS="${REAL_IDS_FILE:-data/selected_events.json}"
if [ -f "$IDS" ]; then
  echo "== real event ids (from $IDS) in tracked files"
  grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "$IDS" | sort -u > /tmp/ps_ids.txt
  for f in $(git ls-files | grep -vE '^uv\.lock$'); do
    n=$(grep -cFf /tmp/ps_ids.txt "$f" 2>/dev/null || true); [ "${n:-0}" -gt 0 ] && { echo "  $f: $n line(s)"; fail=1; }
  done
else
  echo "== (no $IDS; skipping real-id check)"
fi
echo "== gitleaks"; gitleaks detect --source . --redact --exit-code 1 >/dev/null 2>&1 && echo "  clean" || { echo "  LEAKS FOUND"; fail=1; }
[ "$fail" = 0 ] && echo "PRIVACY SWEEP: clean" || { echo "PRIVACY SWEEP: FINDINGS ABOVE"; exit 1; }
