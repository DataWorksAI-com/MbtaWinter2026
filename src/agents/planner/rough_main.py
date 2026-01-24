from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any
import logging

app = FastAPI(title="planner-agent", version="1.0.0")
log = logging.getLogger("planner")

class A2AMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]

# Mock route planning (replace with your actual logic)
def _plan_route_mock(origin: str, destination: str):
    """Mock route planning"""
    return {
        "ok": True,
        "origin": origin,
        "destination": destination,
        "legs": [
            {
                "mode": "subway",
                "route": "Red Line",
                "from": origin,
                "to": destination,
                "duration": "15 minutes"
            }
        ],
        "text": f"Take the Red Line from {origin} to {destination}. Estimated travel time: 15 minutes."
    }

@app.get("/health")
def health():
    return {"ok": True, "service": "planner-agent"}

@app.get("/plan")
def plan(origin: str = Query(...), destination: str = Query(...)):
    """REST endpoint for trip planning"""
    try:
        log.info(f"Planning route from {origin} to {destination}")
        result = _plan_route_mock(origin, destination)
        return result
    except Exception as e:
        log.error(f"Planning error: {e}")
        raise HTTPException(status_code=502, detail=f"plan error: {e}")

@app.post("/a2a/message")
async def a2a_message(message: A2AMessage):
    """A2A protocol endpoint"""
    log.info(f"Received A2A message: {message.type}")
    
    try:
        if message.type == "request":
            payload = message.payload
            query = payload.get("message", "")
            
            # Simple parsing (you can enhance this)
            # Look for "from X to Y" pattern
            words = query.lower().split()
            origin = "Harvard"  # default
            destination = "MIT"  # default
            
            if "from" in words and "to" in words:
                from_idx = words.index("from")
                to_idx = words.index("to")
                origin = " ".join(words[from_idx+1:to_idx])
                destination = " ".join(words[to_idx+1:])
            
            result = plan(origin=origin, destination=destination)
            
            return {
                "type": "response",
                "payload": result,
                "metadata": {"status": "success", "agent": "planner-agent"}
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
                "name": "plan_trip",
                "description": "Plan a trip between two stops",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destination": {"type": "string"}
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
    
    if tool_name == "plan_trip":
        result = plan(
            origin=arguments.get("origin"),
            destination=arguments.get("destination")
        )
        return {"content": [{"type": "text", "text": result.get("text", "Route planned")}]}
    
    return {"error": f"Unknown tool: {tool_name}"}