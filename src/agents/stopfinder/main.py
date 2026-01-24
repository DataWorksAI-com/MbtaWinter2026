"""
MBTA Stop Finder Agent - Real API Integration
Searches for MBTA stops and stations using the real API
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import logging
import os
import requests
from datetime import datetime
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stopfinder-agent")

try:
    from src.observability.otel_config import setup_otel
    setup_otel("stopfinder-agent")
except Exception as e:
    log.warning(f"Could not setup telemetry: {e}")



# Initialize FastAPI
app = FastAPI(title="mbta-stopfinder-agent", version="1.0.0")
# Auto-instrument FastAPI for distributed tracing
try:
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:
    log.warning(f"Could not instrument FastAPI: {e}")

# MBTA API Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY', 'c845eff5ae504179bc9cfa69914059de')
MBTA_BASE_URL = "https://api-v3.mbta.com"

if not MBTA_API_KEY:
    log.warning("MBTA_API_KEY not found in environment variables!")

# Pydantic models
class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = {}


def extract_route_from_query(query: str) -> Optional[str]:
    """
    Extract route/line name from user query.
    
    Examples:
    - "stops on red line" → "Red"
    - "green line stations" → "Green-B"
    - "how many stops on orange line" → "Orange"
    """
    query_lower = query.lower()
    
    # Map of keywords to MBTA route IDs
    route_mapping = {
        "red line": "Red",
        "red": "Red",
        "orange line": "Orange",
        "orange": "Orange",
        "blue line": "Blue",
        "blue": "Blue",
        "green line": "Green-B",  # Default to B branch
        "green": "Green-B",
        "green-b": "Green-B",
        "green b": "Green-B",
        "green-c": "Green-C",
        "green c": "Green-C",
        "green-d": "Green-D",
        "green d": "Green-D",
        "green-e": "Green-E",
        "green e": "Green-E",
        "mattapan": "Mattapan",
        "silver line": "741",
        "silver": "741",
    }
    
    for keyword, route_id in route_mapping.items():
        if keyword in query_lower:
            return route_id
    
    return None


def find_stops(
    query: Optional[str] = None,
    route: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius: Optional[float] = None
) -> Dict[str, Any]:
    """
    Find MBTA stops using the real API.
    
    Args:
        query: Search by stop name
        route: Filter by route (e.g., "Red", "Green-B")
        latitude: Search by location latitude
        longitude: Search by location longitude
        radius: Search radius in meters (default: 1000)
    
    Returns:
        Dictionary with stop information
    """
    try:
        # Build API request
        params = {
            "api_key": MBTA_API_KEY,
        }
        
        # Filter by route (this is supported!)
        if route:
            params["filter[route]"] = route
            log.info(f"Filtering stops by route: {route}")
        
        # Search by location (lat/lon)
        if latitude is not None and longitude is not None:
            params["filter[latitude]"] = latitude
            params["filter[longitude]"] = longitude
            params["filter[radius]"] = radius or 0.01  # ~1km in degrees
            log.info(f"Searching stops near location: ({latitude}, {longitude})")
        else:
            # Fetch stops for filtering
            log.info("Fetching stops for filtering")
            params["page[limit]"] = 500
            if not route:
                # Only filter by location_type if not filtering by route
                params["filter[location_type]"] = "1"  # Only stations
        
        # Make API request
        response = requests.get(
            f"{MBTA_BASE_URL}/stops",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        all_stops = data.get("data", [])
        
        log.info(f"Fetched {len(all_stops)} stops from MBTA API")
        
        # If searching by name, filter client-side
        if query and not route:  # Only name-filter if not already route-filtered
            query_lower = query.lower().strip()
            log.info(f"Filtering by name: '{query}'")
            
            # Filter stops that contain the query in their name
            filtered_stops = []
            for stop in all_stops:
                stop_name = stop.get("attributes", {}).get("name", "").lower()
                # Check if query is in the stop name
                if query_lower in stop_name:
                    filtered_stops.append(stop)
            
            stops = filtered_stops
            log.info(f"Found {len(stops)} stops matching '{query}'")
        else:
            stops = all_stops
        
        # No stops case
        if len(stops) == 0:
            if route:
                search_text = f"on the {route} Line"
            elif query:
                search_text = f"matching '{query}'"
            else:
                search_text = "in that area"
            
            return {
                "ok": True,
                "count": 0,
                "stops": [],
                "text": f"Sorry, I couldn't find any stops {search_text}. Please check and try again.",
                "query": query,
                "route": route
            }
        
        # Process stops
        processed_stops = []
        
        for stop in stops[:50]:  # Limit to 50 results
            attributes = stop.get("attributes", {})
            
            stop_info = {
                "id": stop.get("id"),
                "name": attributes.get("name", "Unknown"),
                "description": attributes.get("description"),
                "latitude": attributes.get("latitude"),
                "longitude": attributes.get("longitude"),
                "wheelchair_accessible": attributes.get("wheelchair_boarding") == 1,
                "location_type": attributes.get("location_type"),
                "municipality": attributes.get("municipality"),
                "address": attributes.get("address")
            }
            
            processed_stops.append(stop_info)
        
        # Create response text
        if route:
            # Route-specific response
            text = f"The {route} Line has {len(stops)} stop(s):\n\n"
            for i, stop in enumerate(processed_stops[:20]):
                name = stop["name"]
                wheelchair = " ♿" if stop.get("wheelchair_accessible") else ""
                text += f"{i+1}. {name}{wheelchair}\n"
            
            if len(stops) > 20:
                text += f"\n... and {len(stops) - 20} more stops"
        
        elif query:
            if len(stops) == 1:
                # Single result - provide detailed info
                stop = processed_stops[0]
                text = f"Found {stop['name']}"
                if stop.get('municipality'):
                    text += f" in {stop['municipality']}"
                if stop.get('wheelchair_accessible'):
                    text += " ♿ (wheelchair accessible)"
                text += "."
            else:
                # Multiple results - list them
                text = f"Found {len(stops)} stop(s) matching '{query}':\n\n"
                for i, stop in enumerate(processed_stops[:10]):
                    name = stop["name"]
                    municipality = stop.get("municipality", "")
                    wheelchair = " ♿" if stop.get("wheelchair_accessible") else ""
                    
                    stop_line = f"{i+1}. {name}"
                    if municipality:
                        stop_line += f" ({municipality})"
                    stop_line += wheelchair
                    
                    text += stop_line + "\n"
                
                if len(stops) > 10:
                    text += f"\n... and {len(stops) - 10} more stops"
        else:
            # Location search or general listing
            text = f"The MBTA system has {len(stops)} stops across all lines. What specific stop or line are you looking for?"
        
        return {
            "ok": True,
            "count": len(stops),
            "stops": processed_stops,
            "text": text,
            "query": query,
            "route": route
        }
        
    except requests.exceptions.RequestException as e:
        log.error(f"MBTA API request failed: {e}")
        return {
            "ok": False,
            "error": f"Failed to fetch stops from MBTA API: {str(e)}",
            "text": "Sorry, I couldn't retrieve stop information at this time. Please try again later."
        }
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return {
            "ok": False,
            "error": str(e),
            "text": "An unexpected error occurred while searching for stops."
        }


def get_stop_by_id(stop_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific stop.
    
    Args:
        stop_id: MBTA stop ID
    
    Returns:
        Dictionary with detailed stop information
    """
    try:
        params = {
            "api_key": MBTA_API_KEY,
            "include": "parent_station,facilities"
        }
        
        log.info(f"Fetching stop details for ID: {stop_id}")
        
        response = requests.get(
            f"{MBTA_BASE_URL}/stops/{stop_id}",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        stop = data.get("data", {})
        
        if not stop:
            return {
                "ok": False,
                "error": f"Stop not found: {stop_id}",
                "text": f"Could not find stop with ID {stop_id}"
            }
        
        attributes = stop.get("attributes", {})
        
        stop_info = {
            "id": stop.get("id"),
            "name": attributes.get("name", "Unknown"),
            "description": attributes.get("description"),
            "latitude": attributes.get("latitude"),
            "longitude": attributes.get("longitude"),
            "wheelchair_accessible": attributes.get("wheelchair_boarding") == 1,
            "platform_code": attributes.get("platform_code"),
            "platform_name": attributes.get("platform_name"),
            "municipality": attributes.get("municipality"),
            "address": attributes.get("address")
        }
        
        return {
            "ok": True,
            "stop": stop_info,
            "text": f"{stop_info['name']} - {stop_info.get('description', 'MBTA Stop')}"
        }
    
    except requests.exceptions.RequestException as e:
        log.error(f"MBTA API request failed: {e}")
        return {
            "ok": False,
            "error": str(e),
            "text": "Failed to fetch stop details"
        }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {
        "ok": True,
        "service": "mbta-stopfinder-agent",
        "version": "1.0.0",
        "mbta_api_configured": MBTA_API_KEY is not None
    }


@app.get("/stops")
def find_stops_endpoint(
    query: Optional[str] = Query(None, description="Search by stop name"),
    route: Optional[str] = Query(None, description="Filter by route"),
    latitude: Optional[float] = Query(None, description="Search by latitude"),
    longitude: Optional[float] = Query(None, description="Search by longitude"),
    radius: Optional[float] = Query(None, description="Search radius in meters")
):
    """
    REST endpoint to find MBTA stops.
    
    Examples:
    - GET /stops?query=Harvard
    - GET /stops?route=Red
    - GET /stops?query=Kendall&route=Red
    - GET /stops?latitude=42.373362&longitude=-71.118956&radius=1000
    """
    try:
        result = find_stops(
            query=query,
            route=route,
            latitude=latitude,
            longitude=longitude,
            radius=radius
        )
        return result
    except Exception as e:
        log.error(f"Error in /stops endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stops/{stop_id}")
def get_stop_endpoint(stop_id: str):
    """
    REST endpoint to get a specific stop by ID.
    
    Example:
    - GET /stops/place-harsq (Harvard Square)
    """
    try:
        result = get_stop_by_id(stop_id)
        return result
    except Exception as e:
        log.error(f"Error in /stops/{stop_id} endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """
    A2A protocol endpoint for agent-to-agent communication.
    Now detects route context in queries.
    """
    log.info(f"Received A2A message: type={message.type}")
    
    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")
            context = payload.get("context", {})
            
            # Detect if query mentions a specific route
            route = extract_route_from_query(query)
            
            # Extract search query from message - improved extraction
            search_query = query.lower()
            
            # Remove common phrases to extract the location name
            patterns_to_remove = [
                "find stops near ", "find stops at ", "find stops for ",
                "find stops on ", "stops on ", "stops in ",
                "stops near ", "stops at ", "stops for ",
                "where is ", "find ", "locate ",
                "how many stops are there in ", "how many stops on ",
                "how many stops ", "list stops on ", "show stops on ",
                "station", "stations", "stop", "stops",
                "the ", "a ", "an ", "are there", "in"
            ]
            
            for pattern in patterns_to_remove:
                search_query = search_query.replace(pattern, "")
            
            search_query = search_query.strip().strip("?.,!")
            
            # If we detected a route and no specific stop name, show all stops on that route
            if route:
                log.info(f"Processing route-specific query: '{query}' → route: {route}")
                result = find_stops(route=route)
            elif search_query and len(search_query) >= 2:
                log.info(f"Processing stop search: '{query}' → search term: '{search_query}'")
                result = find_stops(query=search_query)
            else:
                # General stop query
                log.info(f"Processing general stop query: '{query}'")
                result = find_stops()
            
            # Return A2A response
            return {
                "type": "response",
                "payload": result,
                "metadata": {
                    "status": "success",
                    "agent": "mbta-stopfinder-agent",
                    "search_query": search_query if not route else None,
                    "route_detected": route,
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
                "name": "find_mbta_stops",
                "description": "Find MBTA stops and stations by name, route, or location. Returns stop names, addresses, and accessibility information.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Stop name to search for (e.g., 'Harvard', 'Kendall', 'Park Street')"
                        },
                        "route": {
                            "type": "string",
                            "description": "Filter by route (e.g., 'Red', 'Orange', 'Green-B')"
                        }
                    }
                }
            },
            {
                "name": "get_mbta_stop",
                "description": "Get detailed information about a specific MBTA stop by its ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "stop_id": {
                            "type": "string",
                            "description": "MBTA stop ID (e.g., 'place-harsq' for Harvard Square)"
                        }
                    },
                    "required": ["stop_id"]
                }
            }
        ]
    }


@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    """MCP protocol: Call a tool."""
    tool_name = request.get("name")
    arguments = request.get("arguments", {})
    
    if tool_name == "find_mbta_stops":
        query = arguments.get("query")
        route = arguments.get("route")
        result = find_stops(query=query, route=route)
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": result.get("text", "No stops found")
                }
            ]
        }
    
    elif tool_name == "get_mbta_stop":
        stop_id = arguments.get("stop_id")
        result = get_stop_by_id(stop_id)
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": result.get("text", "Stop not found")
                }
            ]
        }
    
    return {
        "error": f"Unknown tool: {tool_name}"
    }


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8003"))
    log.info(f"Starting MBTA StopFinder Agent on port {port}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)