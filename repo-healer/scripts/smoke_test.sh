#!/usr/bin/env bash
# scripts/smoke_test.sh — Manual smoke test for development
set -e
BASE="http://localhost:8000/api/v1"
REPO_URL="${1:-https://github.com/your-org/your-repo}"

echo "=== 1. Health Check ==="
curl -sf "${BASE%/api/v1}/health" | python3 -m json.tool

echo "=== 2. Analyze Repo ==="
ANALYZE_RESP=$(curl -sf "${BASE}/analyze/repo?repo_url=${REPO_URL}&branch=main")
echo "$ANALYZE_RESP" | python3 -m json.tool
RUN_ID=$(echo "$ANALYZE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "run_id: $RUN_ID"

echo "=== 3. Compute Complexity ==="
curl -sf -X POST "${BASE}/analyze/complexity" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": \"$RUN_ID\"}" | python3 -m json.tool

echo "=== 4. Predict Risk ==="
curl -sf -X POST "${BASE}/predict/risk" \
  -H "Content-Type: application/json" \
  -d "{\"run_id\": \"$RUN_ID\"}" | python3 -m json.tool

echo "=== 5. Run Full Pipeline ==="
curl -sf -X POST "${BASE}/pipeline/run" \
  -H "Content-Type: application/json" \
  -d "{\"repo_url\": \"$REPO_URL\", \"branch\": \"main\"}" | python3 -m json.tool

echo "=== Smoke tests passed ==="
