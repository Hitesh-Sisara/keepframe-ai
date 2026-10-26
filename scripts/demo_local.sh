#!/usr/bin/env bash
# make demo-local: the whole pipeline without AWS (LOCAL_MODE: WORK_BUCKET unset, files under CULL_LOCAL_ROOT).
#   1. keepframe.worker.synth      12 synthetic photos (no customer data) as event ev_synth
#   2. keepframe.worker.run_event  OpenCV 5 worker over ev_synth
#   3. keepframe.agent.run --demo  agent loop on the stub scenario (event ev_demo): fake model, stub tools,
#                                    every approval pause answered by --approve-all (decided_by=scripted, never human)
# No network: the fake model needs no key and the stub tools need no model files.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="${1:-data/local/demo}"
MARK="$ROOT/.demo-local"
# Only wipe a directory this script created (marker file) or an empty/missing one.
if [[ -d "$ROOT" && ! -f "$MARK" && -n "$(ls -A "$ROOT")" ]]; then
  echo "refusing to reuse $ROOT: not empty and not created by make demo-local (pass DEMO_ROOT=<new dir>)" >&2; exit 2
fi
rm -rf "$ROOT"; mkdir -p "$ROOT"; touch "$MARK"
ROOT="$(cd "$ROOT" && pwd)"

unset WORK_BUCKET CULL_DDB
export CULL_LOCAL_ROOT="$ROOT" OPENCV_FORCE_DNN_ENGINE=3 CULL_MODEL_PROVIDER=fake CULL_TOOLS=stub
LOG="$ROOT/demo.log"
run() { echo "+ $*" >>"$LOG"; "$@" 2>>"$LOG" | tee -a "$LOG" | tail -1; }

echo "== 1/3 synthetic event (12 photos) -> $ROOT"
run uv run --frozen python -m keepframe.worker.synth --root "$ROOT" --event-id ev_synth
echo "== 2/3 worker (OpenCV 5) on ev_synth"
run env -u CULL_TOOLS uv run --frozen python -m keepframe.worker.run_event --event-id ev_synth --local-root "$ROOT"
echo "== 3/3 agent --demo (fake model, stub tools, scripted approvals) on ev_demo"
run uv run --frozen python -m keepframe.agent.run --demo --run-id run_demo --approve-all

python3 - "$ROOT" <<'PY'
import collections, json, sys
from pathlib import Path
root = Path(sys.argv[1])
by = collections.Counter()
for line in (root / "dynamodb" / "keepframe-decisions.jsonl").read_text().splitlines():
    d = json.loads(line)
    if not d.get("decided_by"):  # RUN# / approval rows, not photo decisions
        continue
    by[(d.get("event_id"), d.get("decided_by") or "-")] += 1
print("\nphoto decisions by (event, decided_by):")
for (ev, who), n in sorted(by.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
    print(f"  {ev:10} {who:9} {n}")
PY
cat <<MSG

Results:
  worker  $ROOT/results/ev_synth/   (decisions.jsonl, groups.json, summary.json)
  agent   $ROOT/results/ev_demo/    (agent_summary.json, decisions.jsonl, trace.jsonl)
  tables  $ROOT/dynamodb/           (keepframe-decisions.jsonl, keepframe-traces.jsonl)
  log     $LOG
Approvals in this run were SCRIPTED by --approve-all (decided_by=scripted); no person reviewed them.
The agent step runs the stub scenario (ev_demo), not ev_synth: on the synthetic event the worker settles
every photo, so the agent's work queue would be empty.
MSG
