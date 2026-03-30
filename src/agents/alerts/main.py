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
INVALID_API_KEY_VALUES = {"", "your api key", "your_api_key", "changeme", "replace-me"}
APP_STARTUP_COMPLETE = False


def _is_valid_api_key(value: Optional[str]) -> bool:
    if not value:
        return False
    return value.strip().lower() not in INVALID_API_KEY_VALUES

if not _is_valid_api_key(MBTA_API_KEY):
    log.warning("MBTA_API_KEY not found in environment variables!")

# Pydantic models
class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = {}


HISTORICAL_PATTERNS = {
    "TECHNICAL_PROBLEM": {"median": 41, "min": 25, "max": 73, "sample_size": 23104, "description": "technical or signal issues"},
    "POLICE_ACTIVITY": {"median": 33, "min": 20, "max": 50, "sample_size": 2393, "description": "police incidents"},
    "MEDICAL_EMERGENCY": {"median": 33, "min": 23, "max": 63, "sample_size": 1953, "description": "medical emergencies"},
    "ACCIDENT": {"median": 40, "min": 18, "max": 68, "sample_size": 1047, "description": "accidents"},
    "MAINTENANCE": {"median": 46, "min": 28, "max": 82, "sample_size": 976, "description": "maintenance work"},
    "UNKNOWN_CAUSE": {"median": 34, "min": 21, "max": 90, "sample_size": 12061, "description": "unspecified incidents"},
}


def is_historical_query(query: str) -> bool:
    q = query.lower()
    indicators = [
        "based on past", "historical", "typically", "usually",
        "on average", "how long does", "how long do", "how long will",
    ]
    return any(i in q for i in indicators)


def extract_cause_from_query(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ["police", "investigation"]):
        return "POLICE_ACTIVITY"
    if any(w in q for w in ["medical", "passenger"]):
        return "MEDICAL_EMERGENCY"
    if any(w in q for w in ["technical", "signal", "equipment"]):
        return "TECHNICAL_PROBLEM"
    if any(w in q for w in ["accident", "collision"]):
        return "ACCIDENT"
    if any(w in q for w in ["maintenance", "construction", "scheduled", "work"]):
        return "MAINTENANCE"
    return "UNKNOWN_CAUSE"


def format_historical_answer(query: str, route: Optional[str], current_alert_count: int) -> str:
    cause = extract_cause_from_query(query)
    pattern = HISTORICAL_PATTERNS.get(cause, HISTORICAL_PATTERNS["UNKNOWN_CAUSE"])
    route_text = f" on the {route} Line" if route else ""

    response = (
        f"There {'is' if current_alert_count == 1 else 'are'} currently {current_alert_count} active alert(s){route_text}. "
        f"Based on {pattern['sample_size']:,} past {pattern['description']} in MBTA data (2020-2023), "
        f"typical resolution is about {pattern['median']} minutes "
        f"(usual range {pattern['min']}-{pattern['max']} minutes). "
        "If your trip is time-sensitive, plan extra time or use an alternative route."
    )
    return response


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


def is_crowding_query(query: str) -> bool:
    q = query.lower()
    crowding_terms = [
        "crowded", "crowding", "occupancy", "packed", "busy", "full", "capacity"
    ]
    return any(t in q for t in crowding_terms)


def _occupancy_bucket(status: str) -> int:
    # Lower is better (more seats), higher is worse (more crowded)
    order = {
        "MANY_SEATS_AVAILABLE": 1,
        "FEW_SEATS_AVAILABLE": 2,
        "STANDING_ROOM_ONLY": 3,
        "CRUSHED_STANDING_ROOM_ONLY": 4,
        "FULL": 5,
        "NO_DATA_AVAILABLE": 0,
        "NOT_ACCEPTING_PASSENGERS": 0,
    }
    return order.get((status or "").upper(), 0)


def get_crowding(route: Optional[str] = None) -> Dict[str, Any]:
    """
    Get live crowding from MBTA vehicles occupancy_status.
    """
    try:
        params = {"api_key": MBTA_API_KEY}
        if route:
            params["filter[route]"] = route

        response = requests.get(f"{MBTA_BASE_URL}/vehicles", params=params, timeout=10)
        response.raise_for_status()
        vehicles = response.json().get("data", [])

        statuses = []
        for v in vehicles:
            attrs = v.get("attributes", {})
            s = attrs.get("occupancy_status")
            if s:
                statuses.append(s)

        if not statuses:
            route_text = f"{route} Line" if route else "MBTA"
            return {
                "ok": True,
                "text": f"I couldn't find enough live occupancy data for {route_text} right now. Please try again shortly.",
                "summary": "No occupancy data",
                "count": 0,
            }

        avg = sum(_occupancy_bucket(s) for s in statuses) / len(statuses)
        if avg <= 1.7:
            level = "LOW"
            guidance = "Trains are lightly occupied. Seats are usually available."
        elif avg <= 2.6:
            level = "MODERATE"
            guidance = "Trains are moderately busy. You'll likely find a seat."
        elif avg <= 3.6:
            level = "HIGH"
            guidance = "Trains are crowded. Expect standing room on many trips."
        else:
            level = "VERY HIGH"
            guidance = "Trains are very crowded right now. Expect limited space."

        route_text = f"{route} Line" if route else "MBTA"
        text = (
            f"🟡 **{route_text} Crowding: {level}**\n\n"
            f"📊 Overall: crowding estimate from {len(statuses)} active trains\n\n"
            f"{guidance}\n\n"
            f"ℹ️ *Data from MBTA real-time vehicle occupancy sensors (updated every 10-30 seconds)*"
        )

        return {
            "ok": True,
            "text": text,
            "summary": f"{route_text} crowding: {level}",
            "count": len(statuses),
        }
    except requests.exceptions.RequestException as e:
        log.error(f"MBTA vehicles API request failed: {e}")
        return {
            "ok": False,
            "error": f"Failed to fetch crowding from MBTA API: {str(e)}",
            "text": "Sorry, I couldn't retrieve live crowding right now. Please try again.",
        }
    except Exception as e:
        log.error(f"Unexpected crowding error: {e}")
        return {
            "ok": False,
            "error": str(e),
            "text": "An unexpected error occurred while checking crowding.",
        }


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


@app.on_event("startup")
def mark_startup_complete():
    global APP_STARTUP_COMPLETE
    APP_STARTUP_COMPLETE = True


@app.get("/live")
def live():
    return {
        "ok": True,
        "service": "mbta-alerts-agent",
        "version": "1.0.0",
        "startup_complete": APP_STARTUP_COMPLETE,
    }


@app.get("/health")
def health():
    """Readiness endpoint for registration and Kubernetes probes."""
    ready = APP_STARTUP_COMPLETE and _is_valid_api_key(MBTA_API_KEY)
    payload = {
        "ok": True,
        "ready": ready,
        "service": "mbta-alerts-agent",
        "version": "1.0.0",
        "startup_complete": APP_STARTUP_COMPLETE,
        "mbta_api_configured": _is_valid_api_key(MBTA_API_KEY)
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)


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

            # Handle live crowding/occupancy queries
            if is_crowding_query(query):
                result = get_crowding(route=route)
                return {
                    "type": "response",
                    "payload": result,
                    "metadata": {
                        "status": "success",
                        "agent": "mbta-alerts-agent",
                        "route_detected": route,
                        "mode": "crowding",
                        "timestamp": datetime.now().isoformat()
                    }
                }
            
            # Get alerts from MBTA API
            result = get_alerts(route=route)

            # Historical/predictive question path: enrich response with duration guidance
            if result.get("ok") and is_historical_query(query):
                result["text"] = format_historical_answer(
                    query=query,
                    route=route,
                    current_alert_count=result.get("count", 0)
                )
                result["summary"] = "Historical estimate with current alert context"
            
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
