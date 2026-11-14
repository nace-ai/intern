#!/usr/bin/env bash
# ==============================================================================
# DrexCam / ChronoGround Deployment Verification & Smoke Test Script
# Verifies container/server health, API endpoints, Drex System 1, DeepInfra System 2
# ==============================================================================

set -e

HOST="${1:-http://127.0.0.1:8000}"

echo "=========================================================="
echo "⚡ DrexCam Deployment Verification: ${HOST}"
echo "=========================================================="

echo -n "1. Checking System Health (/api/health)... "
HEALTH_RES=$(curl -s "${HOST}/api/health")
if echo "${HEALTH_RES}" | grep -q '"status":"healthy"'; then
  echo "✅ OK"
else
  echo "❌ FAILED"
  echo "Response: ${HEALTH_RES}"
  exit 1
fi

echo -n "2. Checking DrexCam Policy (/api/policy)... "
POLICY_DOC=$(curl -s "${HOST}/api/policy" | grep -o "Texas Personal Auto Policy" | head -n 1)
if [ "${POLICY_DOC}" = "Texas Personal Auto Policy" ]; then
  echo "✅ OK (${POLICY_DOC})"
else
  echo "❌ FAILED"
  exit 1
fi

echo -n "3. Checking DrexCam Benchmark (/api/benchmark)... "
BENCH_RES=$(curl -s "${HOST}/api/benchmark")
if echo "${BENCH_RES}" | grep -q '"pipeline":"drex"'; then
  echo "✅ OK (Drex vs Baseline Benchmark)"
else
  echo "❌ FAILED"
  exit 1
fi

echo -n "4. Checking Video Streaming (/api/media/video)... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${HOST}/api/media/video")
if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "206" ]; then
  echo "✅ OK (HTTP ${HTTP_CODE})"
else
  echo "❌ FAILED (HTTP ${HTTP_CODE})"
  exit 1
fi

echo -n "5. Checking Drex System 1 Evaluation (/api/evaluate)... "
EVAL_RES=$(curl -s -X POST "${HOST}/api/evaluate" \
  -H "Content-Type: application/json" \
  -d '{"scenario_id":"texas_auto_nexar_incident"}')
if echo "${EVAL_RES}" | grep -q '"status":"evaluated"'; then
  DECISION=$(echo "${EVAL_RES}" | grep -o '"decision":"[^"]*"' | head -n 1)
  LATENCY=$(echo "${EVAL_RES}" | grep -o '"latency_ms":[0-9.]*' | head -n 1)
  echo "✅ OK (${DECISION}, ${LATENCY})"
else
  echo "❌ FAILED"
  echo "Response: ${EVAL_RES}"
  exit 1
fi

echo -n "6. Checking DrexCam Web UI Root (/)... "
HTML_RES=$(curl -s "${HOST}/")
if echo "${HTML_RES}" | grep -q 'DrexCam'; then
  echo "✅ OK (DrexCam UI served directly)"
else
  echo "❌ FAILED"
  exit 1
fi

echo "=========================================================="
echo "🎉 ALL DREXCAM DEPLOYMENT GATES PASSED CLEANLY!"
echo "=========================================================="
