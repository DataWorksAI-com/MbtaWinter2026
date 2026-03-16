#!/bin/bash
# register_agents_with_descriptions.sh
# Registers MBTA agents in NANDA Registry with semantic descriptions

REGISTRY_URL="http://97.107.132.213:6900"

echo "🗄️ Registering MBTA Agents with Semantic Descriptions"
echo "======================================================"
echo "Registry: $REGISTRY_URL"
echo ""

# ============================================================================
# Agent 1: mbta-alerts
# ============================================================================
echo "[1/3] Registering mbta-alerts..."

curl -X POST $REGISTRY_URL/register \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "mbta-alerts",
    "agent_url": "http://96.126.111.107:8001"
  }' 2>/dev/null

echo ""

curl -X PUT $REGISTRY_URL/agents/mbta-alerts/status \
  -H "Content-Type: application/json" \
  -d '{
    "alive": true,
    "description": "Provides real-time service alerts, delays, and disruptions for Boston MBTA trains and buses. Monitors all subway lines (Red, Orange, Blue, Green) and commuter rail for issues, maintenance, and schedule changes. Reports both current problems and planned service modifications.",
    "capabilities": ["alerts", "service-status", "disruptions", "real-time"]
  }'

echo "✅ mbta-alerts registered"
echo ""

# ============================================================================
# Agent 2: mbta-stopfinder
# ============================================================================
echo "[2/3] Registering mbta-stopfinder..."

curl -X POST $REGISTRY_URL/register \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "mbta-stopfinder",
    "agent_url": "http://96.126.111.107:8003"
  }' 2>/dev/null

echo ""

curl -X PUT $REGISTRY_URL/agents/mbta-stopfinder/status \
  -H "Content-Type: application/json" \
  -d '{
    "alive": true,
    "description": "Finds MBTA stations and stops by name, location, or proximity. Provides detailed stop information including accessible facilities, parking availability, bike racks, and connecting routes. Can search by address, GPS coordinates, or landmark names. Covers all MBTA subway, bus, and commuter rail stops.",
    "capabilities": ["stops", "stations", "location-search", "find-stops", "nearby"]
  }'

echo "✅ mbta-stopfinder registered"
echo ""

# ============================================================================
# Agent 3: mbta-planner
# ============================================================================
echo "[3/3] Registering mbta-planner..."

curl -X POST $REGISTRY_URL/register \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "mbta-planner",
    "agent_url": "http://96.126.111.107:8002"
  }' 2>/dev/null

echo ""

curl -X PUT $REGISTRY_URL/agents/mbta-planner/status \
  -H "Content-Type: application/json" \
  -d '{
    "alive": true,
    "description": "Plans optimal routes and trips on Boston MBTA transit network. Provides step-by-step directions including train/bus lines, transfers, walking instructions, and estimated travel times. Considers multiple route options, suggests fastest routes, and accounts for real-time conditions. Handles complex multi-leg journeys across subway, bus, and commuter rail.",
    "capabilities": ["trip-planning", "routing", "directions", "navigation", "route-planning"]
  }'

echo "✅ mbta-planner registered"
echo ""

# ============================================================================
# Verification
# ============================================================================
echo "======================================================"
echo "🔍 Verifying Registrations..."
echo ""

echo "Checking mbta-alerts:"
curl -s "$REGISTRY_URL/agents/mbta-alerts" | python3 -m json.tool | grep -E "agent_id|description|alive"

echo ""
echo "Checking mbta-stopfinder:"
curl -s "$REGISTRY_URL/agents/mbta-stopfinder" | python3 -m json.tool | grep -E "agent_id|description|alive"

echo ""
echo "Checking mbta-planner:"
curl -s "$REGISTRY_URL/agents/mbta-planner" | python3 -m json.tool | grep -E "agent_id|description|alive"

echo ""
echo "======================================================"
echo "✅ All agents registered with semantic descriptions!"
echo ""
echo "🧪 Test semantic discovery:"
echo "  curl -X POST http://localhost:8100/chat -d '{\"query\": \"Red Line delays?\"}'"
echo ""