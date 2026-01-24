from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Dict, Any
import os, requests, logging

# Your existing imports
# from shared.agentfacts import agentfacts_default
# from Capstone.server.humanize import humanize_alerts

app = FastAPI(title="alerts-agent", version="1.0.0")
log = logging.getLogger("alerts")

MBTA_KEY = os.getenv("MBTA_API_KEY")
if not MBTA_KEY:
    log.warning("MBTA_API_KEY not set - using mock data for testing")
    MBTA_KEY = "mock-key"

# A2A Message Models
class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")

@app.get("/health")
def health():
    return {"ok": True, "service": "alerts-agent"}

# Original REST endpoint
def get_alerts(route: str | None = None, active_only: bool = True):
    """Get alerts from MBTA API"""
    params = {
        "api_key": MBTA_KEY,
        "sort": "-updated_at",
        "page[limit]": "25",
        "filter[activity]": "BOARD,EXIT,RIDE",
    }
    if route:
        params["filter[route]"] = route

    params["filter[lifecycle]"] = (
        "NEW,ONGOING,UPDATE" if active_only else "NEW,ONGOING,UPDATE,UPCOMING"
    )

    try:
        r = requests.get("https://api-v3.mbta.com/alerts", params=params, timeout=10)
        log.info("MBTA GET %s", r.url)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"MBTA API error: {e}")
        # Return mock data for testing
        return {
            "data": [
                {
                    "id": "test-1",
                    "attributes": {
                        "header": "No current alerts",
                        "description": "Service is running normally"
                    }
                }
            ]
        }

@app.get("/alerts")
def alerts(route: str | None = Query(default=None), active_only: bool = True):
    """REST endpoint for alerts"""
    try:
        log.info("alerts called route=%s active_only=%s", route, active_only)
        data = get_alerts(route=route, active_only=active_only) or {}
        items = data.get("data", [])
        
        # Simple humanization
        count = len(items)
        if count == 0:
            text = "No active alerts at this time. Service is running normally."
        else:
            text = f"Found {count} active alert(s). "
            for item in items[:3]:  # Show first 3
                header = item.get("attributes", {}).get("header", "Alert")
                text += f"{header}. "
        
        return {"ok": True, "route": route, "count": count, "text": text, "raw": data}
    except Exception as e:
        log.error(f"Error: {e}")
        raise HTTPException(status_code=502, detail=f"alerts error: {e}") from e

# A2A Endpoint
@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """A2A protocol endpoint"""
    log.info(f"Received A2A message: {message.type}")
    
    try:
        if message.type == "request":
            # Extract parameters from payload
            payload = message.payload
            query = payload.get("message", "")
            
            # Parse route from query if mentioned
            route = None
            query_lower = query.lower()
            if "red line" in query_lower:
                route = "Red"
            elif "orange line" in query_lower:
                route = "Orange"
            elif "blue line" in query_lower:
                route = "Blue"
            elif "green line" in query_lower:
                route = "Green"
            
            # Get alerts
            result = alerts(route=route, active_only=True)
            
            return {
                "type": "response",
                "payload": result,
                "metadata": {"status": "success", "agent": "alerts-agent"}
            }
        else:
            return {
                "type": "error",
                "payload": {"error": f"Unsupported message type: {message.type}"},
                "metadata": {"status": "error"}
            }
    except Exception as e:
        log.error(f"A2A error: {e}")
        return {
            "type": "error",
            "payload": {"error": str(e)},
            "metadata": {"status": "error"}
        }

# MCP Tools endpoint (for future MCP support)
@app.post("/mcp/tools/list")
def mcp_tools_list():
    """List available MCP tools"""
    return {
        "tools": [
            {
                "name": "get_alerts",
                "description": "Get MBTA service alerts and disruptions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "route": {"type": "string", "description": "Route name (Red, Orange, Blue, Green)"},
                        "active_only": {"type": "boolean", "default": True}
                    }
                }
            }
        ]
    }

@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    """Execute MCP tool"""
    tool_name = request.get("name")
    arguments = request.get("arguments", {})
    
    if tool_name == "get_alerts":
        result = alerts(
            route=arguments.get("route"),
            active_only=arguments.get("active_only", True)
        )
        return {"content": [{"type": "text", "text": result["text"]}]}
    
    return {"error": f"Unknown tool: {tool_name}"}