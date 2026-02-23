"""
MBTA Route Planner Agent - Real API Integration with LLM Location Extraction
Plans routes between stops using real MBTA data, including transfers
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List, Tuple
import logging
import os
import requests
from datetime import datetime
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("planner-agent")

try:
    from src.observability.otel_config import setup_otel
    setup_otel("planner-agent")
except Exception as e:
    log.warning(f"Could not setup telemetry: {e}")

# Initialize FastAPI
app = FastAPI(title="mbta-planner-agent", version="1.0.0")
try:
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:
    log.warning(f"Could not instrument FastAPI: {e}")

# MBTA API Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY', '')
MBTA_BASE_URL = "https://api-v3.mbta.com"

if not MBTA_API_KEY:
    log.warning("MBTA_API_KEY not found in environment variables!")

# LLM Client
try:
    from src.exchange_agent.llm_client import get_llm_client
    llm = get_llm_client()
    log.info(f"✓ LLM provider: {llm.provider}")
except RuntimeError as e:
    llm = None
    log.warning(f"LLM extraction disabled: {e}")


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = {}


# ============================================================================
# LLM LOCATION EXTRACTION
# ============================================================================

async def extract_locations_with_llm(query: str) -> Tuple[Optional[str], Optional[str]]:
    if not llm:
        return extract_locations_basic(query)

    prompt = f"""Extract the origin and destination locations from this transit query.

Query: "{query}"

Instructions:
- Return ONLY the two location names separated by a pipe |
- Use the exact location names mentioned
- If only destination is mentioned, use "none" for origin
- If locations are unclear, use "none"
- Do not include words like "station" or "stop" unless part of the name

Format: origin|destination

Examples:
- "how do I get from park street to harvard" → park street|harvard
- "i wanna go to park street from northeastern university" → northeastern university|park street
- "take me to harvard" → none|harvard
- "northeastern to park street" → northeastern|park street

Response:"""

    try:
        result = await llm.complete(system="", user=prompt, max_tokens=50, temperature=0)
        if "|" in result:
            parts = result.split("|")
            origin = parts[0].strip() if parts[0].strip().lower() != "none" else None
            destination = parts[1].strip() if len(parts) > 1 and parts[1].strip().lower() != "none" else None
            log.info(f"LLM extracted: origin='{origin}', destination='{destination}'")
            return origin, destination
        return extract_locations_basic(query)
    except Exception as e:
        log.error(f"LLM extraction failed: {e}")
        return extract_locations_basic(query)


def extract_locations_basic(query: str) -> Tuple[Optional[str], Optional[str]]:
    query_lower = query.lower()
    origin = None
    destination = None

    if " from " in query_lower and " to " in query_lower:
        parts = query_lower.split(" from ")
        if len(parts) > 1:
            from_part = parts[1]
            to_parts = from_part.split(" to ")
            if len(to_parts) >= 2:
                origin = to_parts[0].strip()
                destination = to_parts[1].strip()
    elif " to " in query_lower:
        parts = query_lower.split(" to ")
        if len(parts) >= 2:
            origin_part = parts[0].strip()
            destination = parts[1].strip()
            for word in ["how", "do", "i", "get", "go", "wanna", "want", "travel", "the"]:
                origin_part = origin_part.replace(f" {word} ", " ").strip()
            origin = origin_part

    if origin:
        origin = origin.strip("?.,!")
    if destination:
        destination = destination.strip("?.,!")

    return origin, destination


# ============================================================================
# MBTA API HELPERS
# ============================================================================

def find_stop_by_name(name: str) -> Optional[Dict[str, Any]]:
    try:
        params = {
            "api_key": MBTA_API_KEY,
            "page[limit]": 500,
            "filter[location_type]": "1"
        }
        log.info(f"Searching for stop: '{name}'")
        response = requests.get(f"{MBTA_BASE_URL}/stops", params=params, timeout=10)
        response.raise_for_status()

        stops = response.json().get("data", [])
        name_lower = name.lower().strip()
        matching_stops = []

        for stop in stops:
            stop_name = stop.get("attributes", {}).get("name", "").lower()
            if name_lower in stop_name:
                matching_stops.append(stop)

        if matching_stops:
            stop = matching_stops[0]
            attributes = stop.get("attributes", {})
            log.info(f"Found stop: {attributes.get('name')}")
            return {
                "id": stop.get("id"),
                "name": attributes.get("name"),
                "latitude": attributes.get("latitude"),
                "longitude": attributes.get("longitude")
            }

        log.warning(f"No stop found matching '{name}'")
        return None
    except Exception as e:
        log.error(f"Error finding stop '{name}': {e}")
        return None


def get_routes_for_stop(stop_id: str) -> List[Dict[str, Any]]:
    """Get all routes serving a given stop."""
    try:
        params = {
            "api_key": MBTA_API_KEY,
            "filter[stop]": stop_id
        }
        response = requests.get(f"{MBTA_BASE_URL}/routes", params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        log.error(f"Error getting routes for stop {stop_id}: {e}")
        return []


def get_stops_for_route(route_id: str) -> List[Dict[str, Any]]:
    """Get all stops on a given route."""
    try:
        params = {
            "api_key": MBTA_API_KEY,
            "filter[route]": route_id,
            "filter[location_type]": "1"
        }
        response = requests.get(f"{MBTA_BASE_URL}/stops", params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        log.error(f"Error getting stops for route {route_id}: {e}")
        return []


def get_routes_between_stops(origin_id: str, destination_id: str) -> List[Dict[str, Any]]:
    """Find direct routes serving both stops."""
    try:
        origin_routes = get_routes_for_stop(origin_id)
        origin_route_ids = {r.get("id") for r in origin_routes}

        dest_routes = get_routes_for_stop(destination_id)
        dest_route_ids = {r.get("id") for r in dest_routes}

        common_route_ids = origin_route_ids.intersection(dest_route_ids)

        common_routes = []
        for route in origin_routes:
            if route.get("id") in common_route_ids:
                attributes = route.get("attributes", {})
                common_routes.append({
                    "id": route.get("id"),
                    "name": attributes.get("long_name", attributes.get("short_name", "Unknown")),
                    "type": attributes.get("type"),
                    "color": attributes.get("color"),
                    "description": attributes.get("description")
                })

        return common_routes
    except Exception as e:
        log.error(f"Error finding routes: {e}")
        return []


def find_transfer_routes(origin_id: str, destination_id: str) -> Optional[Dict[str, Any]]:
    """
    Find a one-transfer route between origin and destination.
    
    Strategy:
    1. Get all routes from origin
    2. Get all routes from destination  
    3. For each origin route, get all its stops
    4. For each of those stops, check if any destination route also serves it
    5. That stop is the transfer point
    """
    try:
        origin_routes = get_routes_for_stop(origin_id)
        dest_routes = get_routes_for_stop(destination_id)

        dest_route_ids = {r.get("id") for r in dest_routes}
        dest_route_map = {r.get("id"): r for r in dest_routes}

        log.info(f"Looking for transfers: {len(origin_routes)} origin routes, {len(dest_routes)} dest routes")

        for origin_route in origin_routes:
            origin_route_id = origin_route.get("id")
            origin_route_name = origin_route.get("attributes", {}).get("long_name", origin_route_id)

            # Get all stops on this origin route
            stops_on_origin_route = get_stops_for_route(origin_route_id)

            for transfer_stop in stops_on_origin_route:
                transfer_stop_id = transfer_stop.get("id")
                transfer_stop_name = transfer_stop.get("attributes", {}).get("name", "Unknown")

                # Check if any destination route also serves this stop
                transfer_routes = get_routes_for_stop(transfer_stop_id)
                transfer_route_ids = {r.get("id") for r in transfer_routes}

                connecting_route_ids = transfer_route_ids.intersection(dest_route_ids)

                if connecting_route_ids:
                    connecting_route_id = list(connecting_route_ids)[0]
                    connecting_route = dest_route_map.get(connecting_route_id, {})
                    connecting_route_name = connecting_route.get("attributes", {}).get("long_name", connecting_route_id)

                    log.info(f"Found transfer at {transfer_stop_name}: {origin_route_name} → {connecting_route_name}")

                    return {
                        "origin_route": {
                            "id": origin_route_id,
                            "name": origin_route_name
                        },
                        "transfer_stop": {
                            "id": transfer_stop_id,
                            "name": transfer_stop_name
                        },
                        "destination_route": {
                            "id": connecting_route_id,
                            "name": connecting_route_name
                        }
                    }

        return None
    except Exception as e:
        log.error(f"Error finding transfer routes: {e}")
        return None


# ============================================================================
# ROUTE PLANNING
# ============================================================================

def plan_route(origin: str, destination: str) -> Dict[str, Any]:
    try:
        log.info(f"Planning route from '{origin}' to '{destination}'")

        origin_stop = find_stop_by_name(origin)
        if not origin_stop:
            return {
                "ok": False,
                "error": f"Could not find origin stop: {origin}",
                "text": f"Sorry, I couldn't find a stop matching '{origin}'. Please check the name and try again."
            }

        dest_stop = find_stop_by_name(destination)
        if not dest_stop:
            return {
                "ok": False,
                "error": f"Could not find destination stop: {destination}",
                "text": f"Sorry, I couldn't find a stop matching '{destination}'. Please check the name and try again."
            }

        log.info(f"Found stops — Origin: {origin_stop['name']}, Destination: {dest_stop['name']}")

        # Try direct routes first
        direct_routes = get_routes_between_stops(origin_stop["id"], dest_stop["id"])

        if direct_routes:
            if len(direct_routes) == 1:
                route = direct_routes[0]
                text = f"Take the {route['name']} from {origin_stop['name']} to {dest_stop['name']}."
            else:
                text = f"Multiple direct options from {origin_stop['name']} to {dest_stop['name']}:\n"
                for i, route in enumerate(direct_routes, 1):
                    text += f"\n{i}. {route['name']}"

            return {
                "ok": True,
                "origin": origin_stop,
                "destination": dest_stop,
                "routes": direct_routes,
                "transfers": 0,
                "text": text
            }

        # No direct route — look for one-transfer option
        log.info("No direct route found, searching for transfer options...")
        transfer = find_transfer_routes(origin_stop["id"], dest_stop["id"])

        if transfer:
            text = (
                f"Take the {transfer['origin_route']['name']} from {origin_stop['name']} "
                f"to {transfer['transfer_stop']['name']}, "
                f"then transfer to the {transfer['destination_route']['name']} "
                f"to {dest_stop['name']}."
            )
            return {
                "ok": True,
                "origin": origin_stop,
                "destination": dest_stop,
                "transfer": transfer,
                "transfers": 1,
                "text": text
            }

        # No route found even with transfer
        return {
            "ok": True,
            "origin": origin_stop,
            "destination": dest_stop,
            "routes": [],
            "transfers": None,
            "text": f"No route found between {origin_stop['name']} and {dest_stop['name']}. You may need multiple transfers — consider checking the MBTA Trip Planner at mbta.com."
        }

    except requests.exceptions.RequestException as e:
        log.error(f"MBTA API request failed: {e}")
        return {
            "ok": False,
            "error": str(e),
            "text": "Sorry, I couldn't plan your route at this time. Please try again later."
        }
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return {
            "ok": False,
            "error": str(e),
            "text": "An unexpected error occurred while planning your route."
        }


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "mbta-planner-agent",
        "version": "1.0.0",
        "mbta_api_configured": bool(MBTA_API_KEY),
        "llm_extraction_available": llm is not None,
        "llm_provider": llm.provider if llm else None
    }


@app.get("/plan")
def plan_route_endpoint(
    origin: str = Query(..., description="Origin stop name"),
    destination: str = Query(..., description="Destination stop name")
):
    try:
        return plan_route(origin=origin, destination=destination)
    except Exception as e:
        log.error(f"Error in /plan endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    log.info(f"Received A2A message: type={message.type}")

    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")

            log.info(f"Processing trip planning query: '{query}'")

            origin, destination = await extract_locations_with_llm(query)
            log.info(f"Extracted — Origin: '{origin}', Destination: '{destination}'")

            if not destination:
                return {
                    "type": "response",
                    "payload": {
                        "ok": False,
                        "text": "I couldn't understand where you want to go. Please specify your destination. For example: 'How do I get to Harvard?' or 'Take me from Park Street to Kenmore.'"
                    },
                    "metadata": {"status": "error", "agent": "mbta-planner-agent"}
                }

            if not origin:
                return {
                    "type": "response",
                    "payload": {
                        "ok": False,
                        "text": f"I can help you get to {destination}! Where are you starting from? For example: 'From Park Street to {destination}'"
                    },
                    "metadata": {"status": "partial", "agent": "mbta-planner-agent"}
                }

            result = plan_route(origin=origin, destination=destination)

            return {
                "type": "response",
                "payload": result,
                "metadata": {
                    "status": "success",
                    "agent": "mbta-planner-agent",
                    "origin_parsed": origin,
                    "destination_parsed": destination,
                    "llm_provider": llm.provider if llm else "none",
                    "timestamp": datetime.now().isoformat()
                }
            }

        return {
            "type": "error",
            "payload": {"text": f"Unsupported message type: {message.type}"},
            "metadata": {"status": "error"}
        }

    except Exception as e:
        log.error(f"A2A error: {e}", exc_info=True)
        return {
            "type": "error",
            "payload": {"error": str(e), "text": "An error occurred while processing your request."},
            "metadata": {"status": "error"}
        }


@app.post("/mcp/tools/list")
def mcp_tools_list():
    return {
        "tools": [
            {
                "name": "plan_mbta_trip",
                "description": "Plan a trip between two MBTA stops, including transfers if needed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string", "description": "Origin stop name"},
                        "destination": {"type": "string", "description": "Destination stop name"}
                    },
                    "required": ["origin", "destination"]
                }
            }
        ]
    }


@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    tool_name = request.get("name")
    arguments = request.get("arguments", {})

    if tool_name == "plan_mbta_trip":
        result = plan_route(
            origin=arguments.get("origin"),
            destination=arguments.get("destination")
        )
        return {"content": [{"type": "text", "text": result.get("text", "Could not plan route")}]}

    return {"error": f"Unknown tool: {tool_name}"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8002"))
    log.info(f"Starting MBTA Planner Agent on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
