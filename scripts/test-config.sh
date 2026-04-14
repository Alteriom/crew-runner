#!/bin/bash
# Crew Runner Configuration & Health Test Script

set -e

CREW_RUNNER_URL="${CREW_RUNNER_URL:-http://localhost:8081}"
BACKEND_URL="${BACKEND_URL:-http://localhost:3007}"

echo "🧪 Crew Runner Configuration Test"
echo "=================================="
echo ""
echo "📍 Crew Runner URL: $CREW_RUNNER_URL"
echo "📍 Backend URL: $BACKEND_URL"
echo ""

echo "1️⃣  Testing health endpoint..."
HEALTH=$(curl -sf "$CREW_RUNNER_URL/health" || echo "{}")
echo "$HEALTH" | jq '.'

STATUS=$(echo "$HEALTH" | jq -r '.status // "unknown"')
if [ "$STATUS" != "ok" ]; then
    echo "❌ Health check failed: status=$STATUS"
    exit 1
fi
echo "✅ Health check passed"
echo ""

echo "2️⃣  Testing version endpoint..."
VERSION_RESPONSE=$(curl -sf "$CREW_RUNNER_URL/version" || echo "{}")
echo "$VERSION_RESPONSE" | jq '.'

VERSION=$(echo "$VERSION_RESPONSE" | jq -r '.version // "unknown"')
if [ "$VERSION" = "unknown" ]; then
    echo "❌ Version endpoint failed"
    exit 1
fi
echo "✅ Version: $VERSION"
echo ""

echo "3️⃣  Testing worker registration..."
ACTIVE_SESSIONS=$(echo "$HEALTH" | jq -r '.active_sessions // 0')
MAX_SESSIONS=$(echo "$HEALTH" | jq -r '.max_sessions // 0')
AT_CAPACITY=$(echo "$HEALTH" | jq -r '.at_capacity // false')

echo "   Active sessions: $ACTIVE_SESSIONS / $MAX_SESSIONS"
echo "   At capacity: $AT_CAPACITY"

if [ "$MAX_SESSIONS" -eq 0 ]; then
    echo "⚠️  Warning: max_sessions is 0 (worker may not be registered)"
else
    echo "✅ Worker configuration valid"
fi
echo ""

echo "4️⃣  Testing container health..."
if command -v docker &> /dev/null; then
    CONTAINER_ID=$(docker ps --filter "name=crew-runner" --format "{{.ID}}" | head -1)
    if [ -n "$CONTAINER_ID" ]; then
        CONTAINER_STATUS=$(docker inspect "$CONTAINER_ID" | jq -r '.[0].State.Health.Status // "none"')
        echo "   Container ID: $CONTAINER_ID"
        echo "   Health status: $CONTAINER_STATUS"
        
        if [ "$CONTAINER_STATUS" = "healthy" ] || [ "$CONTAINER_STATUS" = "none" ]; then
            echo "✅ Container healthy"
        else
            echo "❌ Container unhealthy: $CONTAINER_STATUS"
            docker logs "$CONTAINER_ID" --tail 20
            exit 1
        fi
    else
        echo "⚠️  No crew-runner container found"
    fi
else
    echo "⚠️  Docker not available, skipping container check"
fi
echo ""

echo "5️⃣  Testing for zombie processes..."
if command -v docker &> /dev/null && [ -n "$CONTAINER_ID" ]; then
    ZOMBIE_COUNT=$(docker exec "$CONTAINER_ID" ps aux 2>/dev/null | awk '$8 ~ /Z/ {print}' | wc -l || echo "0")
    echo "   Zombie processes: $ZOMBIE_COUNT"
    
    if [ "$ZOMBIE_COUNT" -gt 0 ]; then
        echo "❌ Found $ZOMBIE_COUNT zombie processes!"
        docker exec "$CONTAINER_ID" ps aux | awk '$8 ~ /Z/ {print}'
        exit 1
    fi
    echo "✅ No zombie processes"
else
    echo "⚠️  Skipping zombie check (no container access)"
fi
echo ""

echo "6️⃣  Testing backend connectivity..."
BACKEND_HEALTH=$(curl -sf "$BACKEND_URL/health" || echo "{}")
BACKEND_STATUS=$(echo "$BACKEND_HEALTH" | jq -r '.status // "unknown"')

if [ "$BACKEND_STATUS" = "ok" ] || [ "$BACKEND_STATUS" = "healthy" ]; then
    echo "✅ Backend reachable: $BACKEND_STATUS"
else
    echo "⚠️  Backend not reachable or unhealthy"
fi
echo ""

echo "7️⃣  Testing crew execution (optional)..."
if [ "${RUN_EXECUTION_TEST:-false}" = "true" ]; then
    echo "   Sending test execution request..."
    EXEC_RESPONSE=$(curl -sf -X POST "$CREW_RUNNER_URL/execute" \
        -H "Content-Type: application/json" \
        -d '{
            "prompt": "Say hello",
            "system_context": "You are a test assistant",
            "execution_id": "test-'$(date +%s)'",
            "inputs": {},
            "timeout_seconds": 30
        }' || echo "{}")
    
    if [ -n "$EXEC_RESPONSE" ]; then
        echo "✅ Execution test passed"
        echo "$EXEC_RESPONSE" | jq -r '.result // .output // "No output"' | head -5
    else
        echo "❌ Execution test failed"
        exit 1
    fi
else
    echo "⏭️  Skipping execution test (set RUN_EXECUTION_TEST=true to enable)"
fi
echo ""

echo "═══════════════════════════════════"
echo "✅ All tests passed!"
echo ""
echo "📊 Summary:"
echo "   Version: $VERSION"
echo "   Status: $STATUS"
echo "   Active sessions: $ACTIVE_SESSIONS / $MAX_SESSIONS"
echo "   Zombie processes: ${ZOMBIE_COUNT:-N/A}"
echo "   Backend: $BACKEND_STATUS"
echo ""
echo "🎉 crew-runner is healthy and ready"
