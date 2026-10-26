#!/usr/bin/env bash
# Local dev API over synthetic fixtures. Tokens are throwaway dev values generated per shell session,
# never real ones. Usage: services/api/dev.sh   (then point the UI at http://127.0.0.1:8000)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
py="${PYTHON:-../../.venv/bin/python}"
"$py" -m tests.fixtures.make_fixtures .local/results >/dev/null
export LOCAL_MODE=1
export CULL_LOCAL_ROOT="$here/.local/results"
export CULL_JUDGE_TOKEN="${CULL_JUDGE_TOKEN:-dev-judge-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
export CULL_TEAM_TOKEN="${CULL_TEAM_TOKEN:-dev-team-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
export CULL_CORS_ORIGINS="${CULL_CORS_ORIGINS:-http://127.0.0.1:3000,http://localhost:3000}"
export CULL_LOCAL_PUBLIC_BASE="${CULL_LOCAL_PUBLIC_BASE:-http://127.0.0.1:8000}"
echo "judge token: $CULL_JUDGE_TOKEN" >&2
echo "team token:  $CULL_TEAM_TOKEN" >&2
exec "$py" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
