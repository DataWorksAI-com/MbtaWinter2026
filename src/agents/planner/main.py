"""
MBTA Route Planner Agent - Real API Integration with LLM Location Extraction
Plans routes between stops using real MBTA data
Uses LLM to extract locations from natural language queries
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List, Tuple
import logging
import os
import requests
from datetime import datetime
from openai import OpenAI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import sys
import os
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
# Auto-instrument FastAPI for distributed tracing
try:
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:
    log.warning(f"Could not instrument FastAPI: {e}")

# MBTA API Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY', 'c845eff5ae504179bc9cfa69914059de')
MBTA_BASE_URL = "https://api-v3.mbta.com"

# OpenAI Configuration for LLM extraction
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

if not MBTA_API_KEY:
    log.warning("MBTA_API_KEY not found in environment variables!")

if not OPENAI_API_KEY:
    log.warning("OPENAI_API_KEY not found - LLM extraction disabled!")

# Pydantic models
class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = {}


def extract_locations_with_llm(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Use LLM to extract origin and destination from natural language query.
    Handles all phrasings robustly.
    
    Args:
        query: Natural language query
        
    Returns:
        Tuple of (origin, destination)
        Either can be None if not mentioned
    
    Examples:
        "how do I get from park street to harvard" → ("park street", "harvard")
        "i wanna go to park street from northeastern" → ("northeastern", "park street")
        "take me to harvard" → (None, "harvard")
        "northeastern to park street" → ("northeastern", "park street")
    """
    if not openai_client:
        log.warning("OpenAI client not available, falling back to basic parsing")
        return extract_locations_basic(query)
    
    prompt = f"""Extract the origin and destination locations from this transit query.

Query: "{query}"

Instructions:
- Return ONLY the two location names separated by a pipe |
- Use the exact location names mentioned (preserve "northeastern university", "park street", etc.)
- If only destination is mentioned, use "none" for origin
- If locations are unclear, use "none"
- Do not include words like "station", "stop" unless part of the name

Format: origin|destination

Examples:
- "how do I get from park street to harvard" → park street|harvard
- "i wanna go to park street from northeastern university" → northeastern university|park street
- "take me to harvard" → none|harvard
- "northeastern to park street" → northeastern|park street
- "go to kenmore from airport" → airport|kenmore

Response:"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",  # Fast and cheap
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=50
        )
        
        # Parse response
        result = response.choices[0].message.content.strip()
        
        if "|" in result:
            parts = result.split("|")
            origin = parts[0].strip() if parts[0].strip().lower() != "none" else None
            destination = parts[1].strip() if len(parts) > 1 and parts[1].strip().lower() != "none" else None
            
            log.info(f"LLM extracted: origin='{origin}', destination='{destination}'")
            
            return origin, destination
        
        log.warning(f"LLM returned unexpected format: {result}")
        return extract_locations_basic(query)
        
    except Exception as e:
        log.error(f"LLM extraction failed: {e}")
        return extract_locations_basic(query)


def extract_locations_basic(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Fallback: Basic string parsing for location extraction.
    Used if LLM is not available.
    
    Args:
        query: Natural language query
        
    Returns:
        Tuple of (origin, destination)
    """
    query_lower = query.lower()
    
    origin = None
    destination = None
    
    # Try to extract locations
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
            
            # Clean origin
            for word in ["how", "do", "i", "get", "go", "wanna", "want", "travel", "the"]:
                origin_part = origin_part.replace(f" {word} ", " ").strip()
            
            origin = origin_part
    
    # Clean up
    if origin:
        origin = origin.strip("?.,!")
    if destination:
        destination = destination.strip("?.,!")
    
    return origin, destination


def find_stop_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Find a stop by name using MBTA API with client-side filtering.
    
    Args:
        name: Stop name to search for
    
    Returns:
        Stop information or None if not found
    """
    try:
        params = {
            "api_key": MBTA_API_KEY,
            "page[limit]": 500,
            "filter[location_type]": "1"  # Only stations
        }
        
        log.info(f"Searching for stop: '{name}'")
        
        response = requests.get(
            f"{MBTA_BASE_URL}/stops",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        stops = data.get("data", [])
        
        # Filter by name client-side
        name_lower = name.lower().strip()
        matching_stops = []
        
        for stop in stops:
            stop_name = stop.get("attributes", {}).get("name", "").lower()
            # Check if query is in the stop name
            if name_lower in stop_name:
                matching_stops.append(stop)
        
        if matching_stops:
            # Return the best match (first one)
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


def get_routes_between_stops(origin_id: str, destination_id: str) -> List[Dict[str, Any]]:
    """
    Find routes that serve both origin and destination stops.
    
    Args:
        origin_id: Origin stop ID
        destination_id: Destination stop ID
    
    Returns:
        List of routes that connect the stops
    """
    try:
        # Get routes serving origin stop
        params = {
            "api_key": MBTA_API_KEY,
            "filter[stop]": origin_id
        }
        
        response = requests.get(
            f"{MBTA_BASE_URL}/routes",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        origin_routes = response.json().get("data", [])
        origin_route_ids = {route.get("id") for route in origin_routes}
        
        # Get routes serving destination stop
        params["filter[stop]"] = destination_id
        
        response = requests.get(
            f"{MBTA_BASE_URL}/routes",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        dest_routes = response.json().get("data", [])
        dest_route_ids = {route.get("id") for route in dest_routes}
        
        # Find common routes
        common_route_ids = origin_route_ids.intersection(dest_route_ids)
        
        # Get details of common routes
        common_routes = []
        for route in origin_routes:
            if route.get("id") in common_route_ids:
                attributes = route.get("attributes", {})
                common_routes.append({
                    "id": route.get("id"),
                    "name": attributes.get("long_name", attributes.get("short_name", "Unknown")),
                    "type": attributes.get("type"),
                    "color": attributes.get("color"),
                    "text_color": attributes.get("text_color"),
                    "description": attributes.get("description")
                })
        
        return common_routes
    
    except Exception as e:
        log.error(f"Error finding routes: {e}")
        return []


def plan_route(origin: str, destination: str) -> Dict[str, Any]:
    """
    Plan a route between two locations using real MBTA data.
    
    Note: MBTA API doesn't provide direct trip planning with transfers.
    This is a simplified version that finds direct routes.
    
    Args:
        origin: Origin stop name
        destination: Destination stop name
    
    Returns:
        Dictionary with route information
    """
    try:
        log.info(f"Planning route from '{origin}' to '{destination}'")
        
        # Step 1: Find origin stop
        origin_stop = find_stop_by_name(origin)
        if not origin_stop:
            return {
                "ok": False,
                "error": f"Could not find origin stop: {origin}",
                "text": f"Sorry, I couldn't find a stop matching '{origin}'. Please check the name and try again."
            }
        
        # Step 2: Find destination stop
        dest_stop = find_stop_by_name(destination)
        if not dest_stop:
            return {
                "ok": False,
                "error": f"Could not find destination stop: {destination}",
                "text": f"Sorry, I couldn't find a stop matching '{destination}'. Please check the name and try again."
            }
        
        log.info(f"Found stops - Origin: {origin_stop['name']}, Destination: {dest_stop['name']}")
        
        # Step 3: Find routes connecting the stops
        routes = get_routes_between_stops(origin_stop["id"], dest_stop["id"])
        
        if not routes:
            return {
                "ok": True,
                "origin": origin_stop,
                "destination": dest_stop,
                "routes": [],
                "text": f"No direct routes found between {origin_stop['name']} and {dest_stop['name']}. You may need to transfer between lines."
            }
        
        # Step 4: Format response
        if len(routes) == 1:
            route = routes[0]
            text = f"Take the {route['name']} from {origin_stop['name']} to {dest_stop['name']}."
        else:
            text = f"Multiple options available from {origin_stop['name']} to {dest_stop['name']}:\n"
            for i, route in enumerate(routes, 1):
                text += f"\n{i}. {route['name']}"
        
        return {
            "ok": True,
            "origin": origin_stop,
            "destination": dest_stop,
            "routes": routes,
            "text": text,
            "summary": f"{len(routes)} route(s) available"
        }
        
    except requests.exceptions.RequestException as e:
        log.error(f"MBTA API request failed: {e}")
        return {
            "ok": False,
            "error": f"Failed to plan route: {str(e)}",
            "text": "Sorry, I couldn't plan your route at this time. Please try again later."
        }
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return {
            "ok": False,
            "error": str(e),
            "text": "An unexpected error occurred while planning your route."
        }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {
        "ok": True,
        "service": "mbta-planner-agent",
        "version": "1.0.0",
        "mbta_api_configured": MBTA_API_KEY is not None,
        "llm_extraction_available": openai_client is not None
    }


@app.get("/plan")
def plan_route_endpoint(
    origin: str = Query(..., description="Origin stop name"),
    destination: str = Query(..., description="Destination stop name")
):
    """
    REST endpoint to plan a route.
    
    Examples:
    - GET /plan?origin=Harvard&destination=MIT
    - GET /plan?origin=Park%20Street&destination=Downtown%20Crossing
    """
    try:
        result = plan_route(origin=origin, destination=destination)
        return result
    except Exception as e:
        log.error(f"Error in /plan endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """
    A2A protocol endpoint for agent-to-agent communication.
    Now uses LLM to extract locations from natural language.
    """
    log.info(f"Received A2A message: type={message.type}")
    
    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")
            context = payload.get("context", {})
            
            log.info(f"Processing trip planning query: '{query}'")
            
            # Use LLM to extract origin and destination (production-grade!)
            origin, destination = extract_locations_with_llm(query)
            
            log.info(f"Extracted locations - Origin: '{origin}', Destination: '{destination}'")
            
            # Validation
            if not destination:
                return {
                    "type": "response",
                    "payload": {
                        "ok": False,
                        "error": "Could not parse destination",
                        "text": "I couldn't understand where you want to go. Please specify your destination. For example: 'How do I get to Harvard?' or 'Take me from Park Street to Kenmore.'"
                    },
                    "metadata": {
                        "status": "error",
                        "agent": "mbta-planner-agent"
                    }
                }
            
            if not origin:
                # Destination only - provide helpful message
                return {
                    "type": "response",
                    "payload": {
                        "ok": False,
                        "error": "Origin not specified",
                        "text": f"I can help you get to {destination}! Where are you starting from? For example: 'From Park Street to {destination}'"
                    },
                    "metadata": {
                        "status": "partial",
                        "agent": "mbta-planner-agent",
                        "destination_parsed": destination
                    }
                }
            
            # Plan route using MBTA API
            result = plan_route(origin=origin, destination=destination)
            
            # Return A2A response
            return {
                "type": "response",
                "payload": result,
                "metadata": {
                    "status": "success",
                    "agent": "mbta-planner-agent",
                    "origin_parsed": origin,
                    "destination_parsed": destination,
                    "extraction_method": "llm",
                    "timestamp": datetime.now().isoformat()
                }
            }
        
        else:
            log.warning(f"Unsupported message type: {message.type}")
            return {
                "type": "error",
                "payload": {
                    "error": f"Unsupported message type: {message.type}",
                    "text": "This agent only supports 'request' messages."
                },
                "metadata": {"status": "error"}
            }
    
    except Exception as e:
        log.error(f"A2A error: {e}", exc_info=True)
        return {
            "type": "error",
            "payload": {
                "error": str(e),
                "text": "An error occurred while processing your request."
            },
            "metadata": {"status": "error"}
        }


@app.post("/mcp/tools/list")
def mcp_tools_list():
    """MCP protocol: List available tools."""
    return {
        "tools": [
            {
                "name": "plan_mbta_trip",
                "description": "Plan a trip between two MBTA stops. Returns available routes and directions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "origin": {
                            "type": "string",
                            "description": "Origin stop name (e.g., 'Harvard', 'Park Street')"
                        },
                        "destination": {
                            "type": "string",
                            "description": "Destination stop name (e.g., 'MIT', 'Downtown Crossing')"
                        }
                    },
                    "required": ["origin", "destination"]
                }
            }
        ]
    }


@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    """MCP protocol: Call a tool."""
    tool_name = request.get("name")
    arguments = request.get("arguments", {})
    
    if tool_name == "plan_mbta_trip":
        origin = arguments.get("origin")
        destination = arguments.get("destination")
        result = plan_route(origin=origin, destination=destination)
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": result.get("text", "Could not plan route")
                }
            ]
        }
    
    return {
        "error": f"Unknown tool: {tool_name}"
    }


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8002"))
    log.info(f"Starting MBTA Planner Agent on port {port}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)