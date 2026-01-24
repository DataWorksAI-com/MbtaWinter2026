from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any
import logging

app = FastAPI(title="stopfinder-agent", version="1.0.0")
log = logging.getLogger("stopfinder")

class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]

# Mock stop data
STOPS = {
    "harvard": {"name": "Harvard", "lat": 42.373362, "lng": -71.118956},
    "mit": {"name": "MIT/Kendall", "lat": 42.36249079, "lng": -71.08617653},
    "park street": {"name": "Park Street", "lat": 42.35639457, "lng": -71.0624242},
    "downtown crossing": {"name": "Downtown Crossing", "lat": 42.355518, "lng": -71.060225},
    "kendall": {"name": "Kendall/MIT", "lat": 42.36249079, "lng": -71.08617653},
}

def _normalize_stop_mock(name: str) -> str:
    """Mock stop normalization"""
    name_lower = name.lower().strip()
    if name_lower in STOPS:
        return STOPS[name_lower]["name"]
    return name.title()

@app.get("/health")
def health():
    return {"ok": True, "service": "stopfinder-agent"}

@app.get("/normalize")
def normalize(name: str = Query(...)):
    """REST endpoint to normalize stop names"""
    try:
        normalized = _normalize_stop_mock(name)
        return {"ok": True, "input": name, "normalized": normalized}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"normalize error: {e}")

@app.get("/find")
def find_stops(query: str = Query(...)):
    """Find stops matching query"""
    query_lower = query.lower()
    matches = [
        {"name": stop["name"], "lat": stop["lat"], "lng": stop["lng"]}
        for key, stop in STOPS.items()
        if query_lower in key or query_lower in stop["name"].lower()
    ]
    return {"ok": True, "query": query, "matches": matches}

@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """A2A protocol endpoint"""
    log.info(f"Received A2A message: {message.type}")
    
    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")
            
            # Find stops based on query
            result = find_stops(query=query)
            
            return {
                "type": "response",
                "payload": result,
                "metadata": {"status": "success", "agent": "stopfinder-agent"}
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

@app.post("/mcp/tools/list")
def mcp_tools_list():
    return {
        "tools": [
            {
                "name": "find_stops",
                "description": "Find MBTA stops by name or location",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Stop name or location"}
                    },
                    "required": ["query"]
                }
            }
        ]
    }

@app.post("/mcp/tools/call")
def mcp_tools_call(request: Dict[str, Any]):
    tool_name = request.get("name")
    arguments = request.get("arguments", {})
    
    if tool_name == "find_stops":
        result = find_stops(query=arguments.get("query", ""))
        matches_text = ", ".join([m["name"] for m in result.get("matches", [])])
        return {"content": [{"type": "text", "text": f"Found stops: {matches_text}"}]}
    
    return {"error": f"Unknown tool: {tool_name}"}