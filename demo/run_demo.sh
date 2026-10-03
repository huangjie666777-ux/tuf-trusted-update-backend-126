#!/usr/bin/env bash
# End-to-end demo: signed repo + backend + curl refresh/download.
set -euo pipefail
cd "$(dirname "$0")/.."

.venv/bin/python demo/make_repo.py demo/repo
(cd demo/repo && ../../.venv/bin/python -m http.server 8001 &)
.venv/bin/python -m uvicorn app.main:app --port 8000 &
APP_PID=$!
trap 'kill $APP_PID 2>/dev/null || true' EXIT
sleep 2

ROOT=$(python3 -c "import json;print(json.dumps(open('demo/repo/root.json').read()))")
SID=$(curl -s -X POST http://127.0.0.1:8000/sources -H 'Content-Type: application/json' \
  -d "{\"upstream_url\":\"http://127.0.0.1:8001\",\"root_json\":$ROOT}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['source_id'])")
echo "source: $SID"
echo "--- refresh ---";  curl -s -X POST "http://127.0.0.1:8000/sources/$SID/refresh"; echo
echo "--- targets ---";  curl -s "http://127.0.0.1:8000/sources/$SID/targets"; echo
echo "--- download ---"; curl -s "http://127.0.0.1:8000/sources/$SID/download/hello.txt"
