"""
MBTA Alerts Agent - Real API Integration
Fetches live service alerts from MBTA API v3
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
log = logging.getLogger("alerts-agent")

try:
    from src.observability.otel_config import setup_otel
    setup_otel("alerts-agent")
except Exception as e:
    log.warning(f"Could not setup telemetry: {e}")



# Initialize FastAPI
app = FastAPI(title="mbta-alerts-agent", version="1.0.0")
# Auto-instrument FastAPI for distributed tracing
try:
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:
    log.warning(f"Could not instrument FastAPI: {e}")

# MBTA API Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY')
MBTA_BASE_URL = "https://api-v3.mbta.com"

if not MBTA_API_KEY:
    log.warning("MBTA_API_KEY not found in environment variables!")

# Pydantic models
class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = {}


def parse_route_from_query(query: str) -> Optional[str]:
    """
    Extract route/line name from user query.
    
    Examples:
    - "Red Line delays" → "Red"
    - "orange line problems" → "Orange"
    - "blue line alerts" → "Blue"
    """
    query_lower = query.lower()
    
    # Map of keywords to MBTA route IDs
    route_mapping = {
        "red": "Red",
        "red line": "Red",
        "orange": "Orange",
        "orange line": "Orange",
        "blue": "Blue",
        "blue line": "Blue",
        "green": "Green",
        "green line": "Green",
        "green-b": "Green-B",
        "green-c": "Green-C",
        "green-d": "Green-D",
        "green-e": "Green-E",
        "mattapan": "Mattapan",
        "silver": "741",  # Silver Line SL1
        "silver line": "741",
    }
    
    for keyword, route_id in route_mapping.items():
        if keyword in query_lower:
            return route_id
    
    return None


def get_alerts(route: Optional[str] = None, activity: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch real-time alerts from MBTA API.
    
    Args:
        route: Filter by route (e.g., "Red", "Orange")
        activity: Filter by activity type (e.g., "BOARD", "EXIT", "RIDE")
    
    Returns:
        Dictionary with alert information
    """
    try:
        # Build API request
        params = {
            "api_key": MBTA_API_KEY,
        }
        
        # Add filters
        if route:
            params["filter[route]"] = route
        if activity:
            params["filter[activity]"] = activity
        
        # Make API request
        log.info(f"Fetching alerts from MBTA API (route={route})")
        response = requests.get(
            f"{MBTA_BASE_URL}/alerts",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        alerts = data.get("data", [])
        
        log.info(f"Found {len(alerts)} alerts")
        
        # No alerts case
        if len(alerts) == 0:
            route_text = f"the {route} Line" if route else "any MBTA services"
            return {
                "ok": True,
                "count": 0,
                "alerts": [],
                "text": f"Good news! There are no active alerts for {route_text}. Service is running normally.",
                "summary": "No alerts"
            }
        
        # Process alerts
        processed_alerts = []
        alert_summaries = []
        
        for alert in alerts[:10]:  # Limit to 10 most recent
            attributes = alert.get("attributes", {})
            
            alert_info = {
                "id": alert.get("id"),
                "header": attributes.get("header", "Service Alert"),
                "description": attributes.get("description", ""),
                "severity": attributes.get("severity", "unknown"),
                "effect": attributes.get("effect", "unknown"),
                "lifecycle": attributes.get("lifecycle", "unknown"),
                "created_at": attributes.get("created_at"),
                "updated_at": attributes.get("updated_at")
            }
            
            processed_alerts.append(alert_info)
            
            # Create summary text
            header = alert_info["header"]
            severity = alert_info["severity"]
            effect = alert_info["effect"]
            
            # Format severity emoji
            severity_emoji = {
                "10": "⚠️",  # Severe
                "7": "⚠️",   # Major
                "5": "ℹ️",   # Minor
                "3": "ℹ️",   # Unknown
            }.get(str(severity), "ℹ️")
            
            summary = f"{severity_emoji} {header}"
            if effect and effect != "UNKNOWN_EFFECT":
                summary += f" ({effect.replace('_', ' ').title()})"
            
            alert_summaries.append(summary)
        
        # Create response text
        route_text = f"the {route} Line" if route else "MBTA services"
        alert_text = f"Found {len(alerts)} active alert(s) for {route_text}:\n\n"
        alert_text += "\n".join(f"{i+1}. {summary}" for i, summary in enumerate(alert_summaries[:5]))
        
        if len(alerts) > 5:
            alert_text += f"\n\n... and {len(alerts) - 5} more alerts"
        
        return {
            "ok": True,
            "count": len(alerts),
            "alerts": processed_alerts,
            "text": alert_text,
            "summary": f"{len(alerts)} active alerts"
        }
    
    except requests.exceptions.RequestException as e:
        log.error(f"MBTA API request failed: {e}")
        return {
            "ok": False,
            "error": f"Failed to fetch alerts from MBTA API: {str(e)}",
            "text": "Sorry, I couldn't retrieve alerts at this time. Please try again later."
        }
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        return {
            "ok": False,
            "error": str(e),
            "text": "An unexpected error occurred while fetching alerts."
        }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {
        "ok": True,
        "service": "mbta-alerts-agent",
        "version": "1.0.0",
        "mbta_api_configured": MBTA_API_KEY is not None
    }


@app.get("/alerts")
def get_alerts_endpoint(
    route: Optional[str] = Query(None, description="Filter by route (e.g., Red, Orange)"),
    activity: Optional[str] = Query(None, description="Filter by activity type")
):
    """
    REST endpoint to get MBTA alerts.
    
    Examples:
    - GET /alerts
    - GET /alerts?route=Red
    - GET /alerts?route=Orange&activity=BOARD
    """
    try:
        result = get_alerts(route=route, activity=activity)
        return result
    except Exception as e:
        log.error(f"Error in /alerts endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """
    A2A protocol endpoint for agent-to-agent communication.
    
    Request format:
    {
        "type": "request",
        "payload": {
            "message": "Are there Red Line delays?",
            "context": {"intent": "alerts"}
        },
        "metadata": {}
    }
    
    Response format:
    {
        "type": "response",
        "payload": {
            "ok": true,
            "count": 2,
            "text": "Found 2 active alerts..."
        },
        "metadata": {"status": "success", "agent": "alerts-agent"}
    }
    """
    log.info(f"Received A2A message: type={message.type}")
    
    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")
            context = payload.get("context", {})
            
            # Parse route from query
            route = parse_route_from_query(query)
            
            log.info(f"Processing query: '{query}' (detected route: {route})")
            
            # Get alerts from MBTA API
            result = get_alerts(route=route)
            
            # Return A2A response
            return {
                "type": "response",
                "payload": result,
                "metadata": {
                    "status": "success",
                    "agent": "mbta-alerts-agent",
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
        log.error(f"A2A error: {e}")
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
    """
    MCP protocol: List available tools.
    """
    return {
        "tools": [
            {
                "name": "get_mbta_alerts",
                "description": "Get real-time service alerts from MBTA (Boston transit). Can filter by route (Red, Orange, Blue, Green lines).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "route": {
                            "type": "string",
                            "description": "Optional route filter (e.g., 'Red', 'Orange', 'Blue', 'Green')",
                            "enum": ["Red", "Orange", "Blue", "Green", "Green-B", "Green-C", "Green-D", "Green-E"]
                        }
                    }
                }
            }
        ]
    }


@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    """
    MCP protocol: Call a tool.
    """
    tool_name = request.get("name")
    arguments = request.get("arguments", {})
    
    if tool_name == "get_mbta_alerts":
        route = arguments.get("route")
        result = get_alerts(route=route)
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": result.get("text", "No alerts information available")
                }
            ]
        }
    
    return {
        "error": f"Unknown tool: {tool_name}"
    }


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8001"))
    log.info(f"Starting MBTA Alerts Agent on port {port}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)