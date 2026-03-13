"""
Exchange Agent - Smart MCP Only Mode
Intelligently selects and chains MCP tools based on query
"""

import sys
import os
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import time
import uuid
import json
import asyncio

try:
    from .mcp_client import MCPClient
except ImportError:
    from mcp_client import MCPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY not found!")
    sys.exit(1)

from openai import AsyncOpenAI

openai_client = AsyncOpenAI(api_key=api_key)

mcp_client: Optional[MCPClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_client

    logger.info("=" * 80)
    logger.info("Starting Exchange Agent - Smart MCP Only Mode")
    logger.info("=" * 80)

    try:
        mcp_client = MCPClient()
        await mcp_client.initialize()
        if hasattr(mcp_client, "_available_tools"):
            logger.info(f"MCP Client initialized with {len(mcp_client._available_tools)} tools")
        else:
            logger.info("MCP Client initialized")
    except Exception as e:
        logger.error(f"MCP Client initialization failed: {e}")
        mcp_client = None

    yield

    logger.info("Shutting down...")
    if mcp_client:
        await mcp_client.cleanup()


app = FastAPI(
    title="MBTA Exchange Agent - Smart MCP Only",
    description="Intelligent MCP tool selection and chaining",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    query: str
    user_id: Optional[str] = "default_user"
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    tools_used: List[str]
    latency_ms: int
    metadata: Optional[Dict[str, Any]] = None


@app.get("/")
async def root():
    tools_count = 0
    if mcp_client and hasattr(mcp_client, "_available_tools"):
        tools_count = len(mcp_client._available_tools)

    return {
        "service": "MBTA Exchange Agent - Smart MCP Only",
        "version": "2.0.0",
        "mode": "mcp_only_smart",
        "mcp_initialized": mcp_client is not None and mcp_client._initialized,
        "mcp_tools_count": tools_count,
        "status": "healthy",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "mcp_initialized": mcp_client._initialized if mcp_client else False,
        "mcp_tools_count": len(mcp_client._available_tools)
        if mcp_client and hasattr(mcp_client, "_available_tools")
        else 0,
    }


async def select_tools_for_query(query: str) -> List[Dict[str, Any]]:
    """
    Use LLM to intelligently select which MCP tools are needed
    """

    available_tools = []
    if mcp_client and hasattr(mcp_client, "_available_tools"):
        available_tools = [
            {"name": tool.name, "description": tool.description or "No description"}
            for tool in mcp_client._available_tools
        ]

    if not available_tools:
        return []

    tools_description = "\n".join(
        [f"- {tool['name']}: {tool['description']}" for tool in available_tools[:20]]
    )

    prompt = f"""You are an intelligent MBTA tool selector for the MCP toolkit.

Query: "{query}"

Available MCP Tools (sample):
{tools_description}

Analyze the query and select which tools are ACTUALLY needed. Return them in execution order.

Key selection rules:
1. "delays", "alerts", "disruptions", "issues" -> mbta_get_alerts
2. "stations", "stops", "find", "near", "locate" -> mbta_search_stops
3. "route", "plan", "from X to Y", "how to get" -> mbta_plan_trip
4. "predictions", "arrivals", "next train" -> mbta_get_predictions
5. For complex queries, chain multiple tools in logical order

Return ONLY a JSON array with tool names, no other text:
["tool_name1", "tool_name2", ...]

Examples:
- "Red Line delays" -> ["mbta_get_alerts"]
- "Find Harvard and plan to MIT" -> ["mbta_search_stops", "mbta_plan_trip"]
- "Check delays and find stations" -> ["mbta_get_alerts", "mbta_search_stops"]
"""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )

        response_text = response.choices[0].message.content.strip()

        if "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        tools_list = json.loads(response_text)
        logger.info(f"Selected tools: {tools_list}")

        return [{"tool_name": t} for t in tools_list if isinstance(t, str)]

    except Exception as e:
        logger.error(f"Tool selection error: {e}")
        if any(w in query.lower() for w in ["delay", "alert", "issue"]):
            return [{"tool_name": "mbta_get_alerts"}]
        if any(w in query.lower() for w in ["stop", "station", "find"]):
            return [{"tool_name": "mbta_search_stops"}]
        if any(w in query.lower() for w in ["route", "plan", "from", "to"]):
            return [{"tool_name": "mbta_plan_trip"}]
        return []


async def extract_tool_parameters(query: str, tool_name: str) -> Dict[str, Any]:
    """
    Extract parameters for a specific tool from the query
    """

    prompt = f"""Extract ONLY the required parameters for: {tool_name}

Query: "{query}"

Rules:
1. mbta_get_alerts needs: route_id (extract line name if mentioned: Red, Orange, Blue, Green-B, etc)
2. mbta_search_stops needs: query (extract station/stop name being searched for)
3. mbta_plan_trip needs: from_location, to_location (extract origin and destination)

Return ONLY valid JSON with ONLY the required parameters for this tool:

Examples by tool:
- mbta_get_alerts: {{"route_id": "Red"}}
- mbta_search_stops: {{"query": "Harvard"}}
- mbta_plan_trip: {{"from_location": "Downtown Crossing", "to_location": "Kendall/MIT"}}

Now extract for tool "{tool_name}" from query: "{query}"

Return ONLY the JSON object, no explanation:
"""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )

        response_text = response.choices[0].message.content.strip()

        if "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        params = json.loads(response_text)
        if tool_name == "mbta_plan_trip":
            if "from_location" in params and "from" not in params:
                params["from"] = params.pop("from_location")
            if "to_location" in params and "to" not in params:
                params["to"] = params.pop("to_location")
        return params

    except Exception as e:
        logger.error(f"Parameter extraction error for {tool_name}: {e}")
        return {}


async def call_mcp_tool(tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Call an MCP tool"""

    if not mcp_client:
        raise ValueError("MCP client not initialized")

    logger.info(f"Calling {tool_name} with {parameters}")
    result = await mcp_client.call_tool(tool_name, parameters or {})
    logger.info(f"{tool_name} returned data")
    return result


async def synthesize_response(query: str, tool_results: Dict[str, Any], tools_used: List[str]) -> str:
    """Convert tool results to natural language"""

    if not tool_results:
        return "No tools were executed. Unable to process query."

    results_text = "\n".join(
        [f"**{tool}:**\n{json.dumps(result, indent=2)}" for tool, result in tool_results.items()]
    )

    prompt = f"""You are an MBTA transit assistant. Convert API results to helpful responses.

User asked: "{query}"

Tools executed: {', '.join(tools_used)}

Results:
{results_text}

Provide a concise, natural language response that answers the user's question. Be helpful and direct."""

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500,
        )

        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Synthesis error: {e}")
        return f"Retrieved {len(tool_results)} tool result(s). Unable to synthesize response."


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Smart MCP-only chat endpoint"""

    start_time = time.time()
    query = request.query
    conversation_id = request.conversation_id or str(uuid.uuid4())

    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    logger.info(f"Query: {query}")
    logger.info(f"Conversation: {conversation_id}")

    if not mcp_client or not mcp_client._initialized:
        raise HTTPException(status_code=503, detail="MCP client not available")

    logger.info("Selecting tools...")
    tools_to_call = await select_tools_for_query(query)
    logger.info(f"Selected {len(tools_to_call)} tools")

    if not tools_to_call:
        response_text = (
            "I couldn't determine which tools to use for your query. "
            "Try asking about MBTA alerts, stops, or route planning."
        )
        tools_used = []
    else:
        tool_results = {}
        tools_used = []

        for tool_call in tools_to_call:
            tool_name = tool_call["tool_name"]

            try:
                logger.info(f"Extracting parameters for {tool_name}...")
                params = await extract_tool_parameters(query, tool_name)

                logger.info(f"Calling {tool_name}")
                result = await call_mcp_tool(tool_name, params)
                tool_results[tool_name] = result
                tools_used.append(tool_name)
                logger.info(f"{tool_name} succeeded")

            except Exception as e:
                logger.error(f"{tool_name} failed: {e}")
                tool_results[tool_name] = {"error": str(e)}
                tools_used.append(f"{tool_name} (error)")

        logger.info("Synthesizing response...")
        response_text = await synthesize_response(query, tool_results, tools_used)

    latency_ms = int((time.time() - start_time) * 1000)
    logger.info(f"Complete in {latency_ms}ms using {len(tools_used)} tool(s)")

    return ChatResponse(
        response=response_text,
        tools_used=tools_used,
        latency_ms=latency_ms,
        metadata={
            "conversation_id": conversation_id,
            "tools_attempted": len(tools_to_call),
            "tools_succeeded": len([t for t in tools_used if "(error)" not in t]),
            "mode": "mcp_smart",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8110)
