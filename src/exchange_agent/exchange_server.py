# src/exchange_agent/exchange_server.py

import sys
import os


from dotenv import load_dotenv
load_dotenv()  


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    from src.observability.otel_config import setup_otel
    from src.observability.clickhouse_logger import get_clickhouse_logger
    setup_otel("exchange-agent")
    print("✅ OpenTelemetry configured for exchange-agent")
except Exception as e:
    print(f"⚠️  Could not setup observability: {e}")
    import traceback
    traceback.print_exc()
    print("Continuing without telemetry...")

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

# Add parent directory to Python path for imports
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Try relative imports first, fall back to absolute
try:
    from .mcp_client import MCPClient
    from ...stategraph_orchestrator_old import StateGraphOrchestrator
except ImportError:
    from mcp_client import MCPClient
    from stategraph_orchestrator_old import StateGraphOrchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Verify API key is loaded
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logger.error("=" * 60)
    logger.error("❌ OPENAI_API_KEY not found in environment!")
    logger.error("=" * 60)
    logger.error("Please ensure .env file exists in project root with:")
    logger.error("  OPENAI_API_KEY=sk-...")
    logger.error("=" * 60)
    sys.exit(1)
else:
    logger.info(f"✓ OpenAI API key loaded (ends with: ...{api_key[-4:]})")

# Initialize OpenAI client
from openai import OpenAI
openai_client = OpenAI(api_key=api_key)

# Global instances
mcp_client: Optional[MCPClient] = None
stategraph_orchestrator: Optional[StateGraphOrchestrator] = None
clickhouse_logger = None

# Tracer for OpenTelemetry
try:
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
    logger.info("✅ OpenTelemetry tracer initialized")
except ImportError:
    # Fallback no-op tracer
    class NoOpTracer:
        def start_as_current_span(self, name):
            from contextlib import contextmanager
            @contextmanager
            def _span():
                yield type('obj', (object,), {'set_attribute': lambda *args: None, 'set_status': lambda *args: None, 'record_exception': lambda *args: None})()
            return _span()
    tracer = NoOpTracer()
    logger.warning("⚠️  OpenTelemetry not available, using no-op tracer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle
    Startup: Initialize MCP client, StateGraph orchestrator, and ClickHouse logger
    Shutdown: Cleanup resources
    """
    global mcp_client, stategraph_orchestrator, clickhouse_logger
    
    # Startup
    logger.info("=" * 60)
    logger.info("Starting Exchange Agent with Hybrid A2A + MCP Support")
    logger.info("=" * 60)
    
    # Initialize ClickHouse Logger
    try:
        clickhouse_logger = get_clickhouse_logger()
        logger.info("✅ ClickHouse logger initialized")
    except Exception as e:
        logger.warning(f"⚠️  ClickHouse logger initialization failed: {e}")
        clickhouse_logger = None
    
    # Initialize StateGraph Orchestrator (for A2A path)
    try:
        stategraph_orchestrator = StateGraphOrchestrator()
        logger.info("✅ StateGraph Orchestrator initialized - A2A path available")
    except Exception as e:
        logger.error(f"❌ StateGraph Orchestrator initialization failed: {e}")
        logger.exception(e)
        stategraph_orchestrator = None
    
    # Initialize MCP Client (for fast path)
    try:
        mcp_client = MCPClient()
        await mcp_client.initialize()
        logger.info("✅ MCP Client initialized - Fast path available")
    except Exception as e:
        logger.warning(f"⚠️  MCP Client initialization failed: {e}")
        logger.warning("Falling back to A2A agents only")
        mcp_client = None
    
    logger.info("=" * 60)
    
    yield
    
    # Shutdown
    logger.info("Shutting down Exchange Agent...")
    if mcp_client:
        await mcp_client.cleanup()
    logger.info("✓ Shutdown complete")


# Create FastAPI app with lifespan
app = FastAPI(
    title="MBTA Exchange Agent",
    description="Hybrid A2A + MCP Orchestrator with LLM-Powered Routing",
    version="3.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# AUTO-INSTRUMENTATION - Automatically trace HTTP requests/responses
# ============================================================================
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    
    # Auto-instrument FastAPI (all endpoints)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("✅ FastAPI auto-instrumentation enabled")
    
    # Auto-instrument HTTPX (HTTP client for A2A calls)
    HTTPXClientInstrumentor().instrument()
    logger.info("✅ HTTPX auto-instrumentation enabled")
except Exception as e:
    logger.warning(f"⚠️  Auto-instrumentation failed: {e}")


# Request/Response models
class ChatRequest(BaseModel):
    query: str
    user_id: Optional[str] = "default_user"
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    path: str  # "mcp" or "a2a"
    latency_ms: int
    intent: str
    confidence: float
    metadata: Optional[Dict[str, Any]] = None


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "MBTA Exchange Agent",
        "version": "3.0.0",
        "architecture": "Hybrid A2A + MCP with LLM Routing",
        "mcp_available": mcp_client is not None and mcp_client._initialized,
        "stategraph_available": stategraph_orchestrator is not None,
        "clickhouse_available": clickhouse_logger is not None,
        "status": "healthy"
    }


# ============================================================================
# TIER 1: UNIFIED CLASSIFICATION + ROUTING (LLM)
# ============================================================================

async def classify_and_route_with_llm(query: str) -> Dict:
    """
    Single LLM call for both intent classification AND routing decision.
    Replaces separate embedding-based classifier + routing LLM.
    
    Returns:
        {
            "intent": str,           # alerts, stops, trip_planning, general
            "confidence": float,     # 0.0-1.0
            "path": str,            # mcp or a2a
            "reasoning": str,       # explanation
            "complexity": float,    # 0.0-1.0
            "fallback_to_a2a_if_fails": bool
        }
    """
    
    system_prompt = """You are an intelligent routing agent for MBTA transit queries.

**YOUR TASK:** Analyze the query and provide BOTH classification AND routing decision.

**CLASSIFICATION - Determine Intent:**
- "alerts": Service alerts, delays, disruptions, issues
  Examples: "Red Line delays?", "Any alerts?", "What's happening on Orange Line?"
  
- "stops": Stop/station information, finding stops, stop details
  Examples: "Find Harvard station", "Stops on Green Line", "Where is Park Street?"
  
- "trip_planning": Route planning, directions, how to get somewhere
  Examples: "Park St to Harvard?", "How do I get to MIT?", "Route to Logan?"
  
- "general": Greetings, off-topic, non-MBTA queries
  Examples: "Hi", "How are you?", "What's the weather?", "Who won the game?"

**ROUTING - Choose Path:**

MCP Protocol (Fast Path, ~400ms):
- Best for: Single-fact lookups, simple queries, real-time data
- Handles: alerts, predictions, vehicle tracking, stop search, schedules
- Examples: "Red Line delays?", "Next train at Park St?", "Where are trains?"

A2A Protocol (Multi-Agent, ~1500ms):
- Best for: Trip planning, conditional logic, multi-step reasoning
- Handles: complex routing, considering multiple factors
- Examples: "Park St to Harvard?", "Best route if delays?", "Plan trip to airport"

**DECISION GUIDELINES:**
1. NOT about MBTA transit → intent="general", path="a2a"
2. Greeting/off-topic → intent="general", path="a2a"
3. Simple MBTA fact lookup → intent=[appropriate], path="mcp"
4. Complex coordination needed → intent=[appropriate], path="a2a"
5. Multi-step/conditional logic → intent=[appropriate], path="a2a"

**COMPLEXITY SCORING (0.0-1.0):**
- 0.0-0.3: Simple (single fact, one API call)
- 0.4-0.6: Medium (some context needed)
- 0.7-1.0: Complex (multi-step, coordination, conditional logic)

**CONFIDENCE SCORING (0.0-1.0):**
- 0.9-1.0: Very clear intent
- 0.7-0.8: Reasonably clear
- 0.5-0.6: Somewhat ambiguous
- 0.0-0.4: Very ambiguous or off-topic

Return ONLY valid JSON (no markdown, no code blocks):
{
  "intent": "alerts|stops|trip_planning|general",
  "confidence": 0.0-1.0,
  "path": "mcp|a2a",
  "reasoning": "brief explanation",
  "complexity": 0.0-1.0,
  "fallback_to_a2a_if_fails": true/false
}"""

    user_message = f"""Query: "{query}"

Analyze this query and provide classification + routing decision."""

    try:
        with tracer.start_as_current_span("classify_and_route_with_llm") as span:
            span.set_attribute("query", query)
            
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=300
            )
            
            decision_text = response.choices[0].message.content.strip()
            
            # Remove markdown if present
            if decision_text.startswith("```json"):
                decision_text = decision_text.replace("```json", "").replace("```", "").strip()
            elif decision_text.startswith("```"):
                decision_text = decision_text.replace("```", "").strip()
            
            decision = json.loads(decision_text)
            
            # Ensure all fields present
            decision.setdefault("fallback_to_a2a_if_fails", True)
            decision.setdefault("complexity", 0.5)
            decision.setdefault("confidence", 0.5)
            
            # Add span attributes
            span.set_attribute("intent", decision['intent'])
            span.set_attribute("confidence", decision['confidence'])
            span.set_attribute("path", decision['path'])
            span.set_attribute("complexity", decision['complexity'])
            
            logger.info(f"🧠 LLM Decision: intent={decision['intent']}, "
                       f"confidence={decision['confidence']:.2f}, "
                       f"path={decision['path']}, "
                       f"complexity={decision['complexity']:.2f}")
            
            return decision
            
    except Exception as e:
        logger.error(f"LLM classification/routing failed: {e}", exc_info=True)
        # Fallback to safe default
        return {
            "intent": "general",
            "confidence": 0.3,
            "path": "a2a",
            "reasoning": f"Error in LLM analysis: {str(e)}",
            "complexity": 0.5,
            "fallback_to_a2a_if_fails": True
        }


# ============================================================================
# MAIN CHAT ENDPOINT (WITH FULL TRACING)
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint with hybrid MCP + A2A support
    Uses single LLM for both classification and routing
    FULLY INSTRUMENTED with OpenTelemetry tracing and ClickHouse logging
    """
    
    # Create root span for entire request
    with tracer.start_as_current_span("chat_endpoint") as root_span:
        start_time = time.time()
        query = request.query
        conversation_id = request.conversation_id or str(uuid.uuid4())
        
        # Add root span attributes
        root_span.set_attribute("query", query)
        root_span.set_attribute("conversation_id", conversation_id)
        root_span.set_attribute("user_id", request.user_id)
        
        if not query or not query.strip():
            raise HTTPException(status_code=400, detail="Query cannot be empty")
        
        logger.info(f"📨 Received query: {query}")
        logger.info(f"   Conversation ID: {conversation_id}")
        
        # ============================================================
        # SINGLE LLM CALL - Classification + Routing Combined
        # ============================================================
        decision = await classify_and_route_with_llm(query)
        
        primary_intent = decision["intent"]
        primary_confidence = decision["confidence"]
        chosen_path = decision["path"]
        
        logger.info(f"🎯 Intent: {primary_intent} | Confidence: {primary_confidence:.3f}")
        logger.info(f"🚀 Routing: {chosen_path} path | Complexity: {decision['complexity']:.3f}")
        
        # Log to ClickHouse: User message
        if clickhouse_logger:
            try:
                clickhouse_logger.log_conversation(
                    conversation_id=conversation_id,
                    user_id=request.user_id,
                    role="user",
                    content=query,
                    intent=primary_intent,
                    routed_to_orchestrator=(chosen_path == "a2a"),
                    metadata={
                        "confidence": primary_confidence,
                        "complexity": decision['complexity'],
                        "reasoning": decision['reasoning']
                    }
                )
            except Exception as e:
                logger.warning(f"ClickHouse logging failed: {e}")
        
        # Step 2: Execute chosen path
        response_text = ""
        path_taken = ""
        metadata = {
            "classification_and_routing": {
                "intent": primary_intent,
                "confidence": primary_confidence,
                "path": chosen_path,
                "reasoning": decision["reasoning"],
                "complexity": decision["complexity"]
            }
        }
        
        if chosen_path == "mcp" and mcp_client and mcp_client._initialized:
            # MCP FAST PATH
            logger.info("🚀 Routing to MCP Fast Path")
            
            try:
                response_text, mcp_metadata = await handle_mcp_path(query, primary_intent)
                path_taken = "mcp"
                metadata.update(mcp_metadata)
                
            except Exception as e:
                logger.error(f"❌ MCP path failed: {e}")
                root_span.record_exception(e)
                
                # Fallback to A2A if allowed
                if decision.get("fallback_to_a2a_if_fails", True):
                    logger.info("↪️  Falling back to A2A path")
                    response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
                    path_taken = "a2a_fallback"
                    metadata.update(a2a_metadata)
                    metadata["mcp_error"] = str(e)
                else:
                    response_text = f"I encountered an error: {str(e)}"
                    path_taken = "mcp_error"
        
        else:
            # A2A AGENT PATH
            logger.info(f"🔄 Routing to A2A Path - Reason: {decision['reasoning']}")
            
            response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
            path_taken = "a2a"
            metadata.update(a2a_metadata)
        
        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Add final span attributes
        root_span.set_attribute("path_taken", path_taken)
        root_span.set_attribute("latency_ms", latency_ms)
        root_span.set_attribute("intent", primary_intent)
        root_span.set_attribute("confidence", primary_confidence)
        
        logger.info(f"✅ Response generated via {path_taken} in {latency_ms}ms")
        
        # Log to ClickHouse: Assistant response
        if clickhouse_logger:
            try:
                clickhouse_logger.log_conversation(
                    conversation_id=conversation_id,
                    user_id=request.user_id,
                    role="assistant",
                    content=response_text[:1000],  # Truncate if too long
                    intent=primary_intent,
                    routed_to_orchestrator=(path_taken == "a2a"),
                    metadata={
                        "path": path_taken,
                        "latency_ms": latency_ms,
                        "confidence": primary_confidence
                    }
                )
            except Exception as e:
                logger.warning(f"ClickHouse logging failed: {e}")
        
        return ChatResponse(
            response=response_text,
            path=path_taken,
            latency_ms=latency_ms,
            intent=primary_intent,
            confidence=primary_confidence,
            metadata=metadata
        )


# ============================================================================
# TIER 2a: MCP PATH HANDLER (LLM-POWERED TOOL SELECTION) - WITH TRACING
# ============================================================================

async def handle_mcp_path(query: str, intent: str) -> tuple[str, Dict[str, Any]]:
    """
    Handle query using MCP fast path with LLM-powered tool selection
    FULLY INSTRUMENTED with tracing
    
    Returns: (response_text, metadata)
    """
    
    with tracer.start_as_current_span("handle_mcp_path") as span:
        span.set_attribute("query", query)
        span.set_attribute("intent", intent)
        
        metadata = {"tools_used": []}
        
        try:
            # Step 1: Get available MCP tools
            available_tools = []
            if hasattr(mcp_client, '_available_tools') and mcp_client._available_tools:
                for tool in mcp_client._available_tools:
                    tool_info = {
                        "name": tool.name,
                        "description": tool.description or "No description available"
                    }
                    available_tools.append(tool_info)
            
            if not available_tools:
                logger.warning("No MCP tools available, falling back to A2A")
                raise Exception("No MCP tools available")
            
            logger.info(f"📋 Found {len(available_tools)} available MCP tools")
            span.set_attribute("available_tools_count", len(available_tools))
            
            # Step 2: LLM selects appropriate tool
            tool_selection = await select_mcp_tool_with_llm(query, intent, available_tools)
            
            tool_name = tool_selection["tool_name"]
            tool_params = tool_selection["parameters"]
            
            logger.info(f"🔧 Selected tool: {tool_name} with params: {tool_params}")
            logger.info(f"💭 Reasoning: {tool_selection['reasoning']}")
            
            span.set_attribute("tool_selected", tool_name)
            span.set_attribute("tool_params", json.dumps(tool_params))
            
            metadata["llm_reasoning"] = tool_selection["reasoning"]
            metadata["tool_params"] = tool_params
            
            # Step 3: Call the selected MCP tool dynamically
            tool_result = await call_mcp_tool_dynamic(tool_name, tool_params)
            
            metadata["tools_used"].append(tool_name)
            
            # Step 4: LLM synthesizes natural language response
            response = await synthesize_mcp_response_with_llm(query, tool_name, tool_result)
            
            span.set_attribute("response_length", len(response))
            
            return response, metadata
            
        except Exception as e:
            logger.error(f"Error in MCP path: {e}", exc_info=True)
            span.record_exception(e)
            raise


async def select_mcp_tool_with_llm(query: str, intent: str, available_tools: List[Dict]) -> Dict:
    """
    Use LLM to select the most appropriate MCP tool for the query
    
    Returns:
        {
            "tool_name": str,
            "parameters": dict,
            "reasoning": str
        }
    """
    
    # Format tools for LLM
    tools_description = "\n".join([
        f"- {tool['name']}: {tool['description']}"
        for tool in available_tools
    ])
    
    system_prompt = f"""You are an expert at selecting the right MBTA API tool for a query.

Available tools:
{tools_description}

**IMPORTANT PARAMETER NAMING:**
- Use "route_id" not "route" (e.g., route_id="Red")
- Use "stop_id" not "stop" (e.g., stop_id="place-pktrm")
- Use "direction_id" not "direction" (e.g., direction_id=0)
- Common parameters: route_id, stop_id, direction_id, latitude, longitude

**Common MBTA Route IDs:**
- Red Line: "Red"
- Orange Line: "Orange"
- Blue Line: "Blue"
- Green Line: "Green-B", "Green-C", "Green-D", "Green-E"

Select the BEST tool and extract appropriate parameters.

Return ONLY valid JSON (no markdown):
{{
  "tool_name": "exact_tool_name",
  "parameters": {{"param1": "value1"}},
  "reasoning": "why this tool and these parameters"
}}"""

    user_message = f"""Query: "{query}"
Intent: {intent}

Select the appropriate tool and parameters."""

    try:
        with tracer.start_as_current_span("select_mcp_tool_with_llm") as span:
            span.set_attribute("query", query)
            span.set_attribute("intent", intent)
            span.set_attribute("available_tools_count", len(available_tools))
            
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            selection_text = response.choices[0].message.content.strip()
            
            # Remove markdown if present
            if selection_text.startswith("```json"):
                selection_text = selection_text.replace("```json", "").replace("```", "").strip()
            elif selection_text.startswith("```"):
                selection_text = selection_text.replace("```", "").strip()
            
            selection = json.loads(selection_text)
            
            # Ensure parameters is a dict
            if not isinstance(selection.get("parameters"), dict):
                selection["parameters"] = {}
            
            span.set_attribute("selected_tool", selection["tool_name"])
            span.set_attribute("tool_parameters", json.dumps(selection["parameters"]))
            
            return selection
            
    except Exception as e:
        logger.error(f"Tool selection failed: {e}", exc_info=True)
        # Fallback to safe default based on intent
        if intent == "alerts":
            return {
                "tool_name": "mbta_get_alerts",
                "parameters": {},
                "reasoning": f"Fallback due to error: {str(e)}"
            }
        else:
            return {
                "tool_name": "mbta_search_stops",
                "parameters": {"query": query},
                "reasoning": f"Fallback due to error: {str(e)}"
            }


async def call_mcp_tool_dynamic(tool_name: str, parameters: Dict) -> Dict[str, Any]:
    """
    Dynamically call any MCP tool by name with given parameters
    """
    
    with tracer.start_as_current_span("call_mcp_tool_dynamic") as span:
        span.set_attribute("tool_name", tool_name)
        span.set_attribute("parameters", json.dumps(parameters))
        
        # Map tool names to MCP client methods
        tool_method_map = {
            "mbta_get_alerts": mcp_client.get_alerts,
            "mbta_get_routes": mcp_client.get_routes,
            "mbta_get_stops": mcp_client.get_stops,
            "mbta_search_stops": mcp_client.search_stops,
            "mbta_get_predictions": mcp_client.get_predictions,
            "mbta_get_predictions_for_stop": mcp_client.get_predictions_for_stop,
            "mbta_get_schedules": mcp_client.get_schedules,
            "mbta_get_trips": mcp_client.get_trips,
            "mbta_get_vehicles": mcp_client.get_vehicles,
            "mbta_get_nearby_stops": mcp_client.get_nearby_stops,
            "mbta_plan_trip": mcp_client.plan_trip,
            "mbta_list_all_routes": mcp_client.list_all_routes,
            "mbta_list_all_stops": mcp_client.list_all_stops,
            "mbta_list_all_alerts": mcp_client.list_all_alerts,
        }
        
        if tool_name not in tool_method_map:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        method = tool_method_map[tool_name]
        
        try:
            result = await method(**parameters)
            span.set_attribute("success", True)
            return result
        except Exception as e:
            logger.error(f"Error calling {tool_name}: {e}", exc_info=True)
            span.record_exception(e)
            span.set_attribute("success", False)
            raise


async def synthesize_mcp_response_with_llm(query: str, tool_name: str, tool_result: Dict) -> str:
    """
    Use LLM to convert MCP JSON response into natural language
    """
    
    system_prompt = """You are a helpful MBTA transit assistant.

Convert the technical API response into a natural, conversational answer.

Guidelines:
- Be concise but informative
- Use natural language, not technical jargon
- Include relevant details (times, locations, routes)
- If there's a lot of data, summarize the most important points
- Be helpful and friendly

DO NOT include phrases like "Based on the data" or "According to the API".
Just answer the question naturally."""

    # Truncate very large responses
    tool_result_str = json.dumps(tool_result, indent=2)
    if len(tool_result_str) > 4000:
        tool_result_str = tool_result_str[:4000] + "\n... (truncated)"
    
    user_message = f"""Query: "{query}"
Tool used: {tool_name}

API Response:
{tool_result_str}

Convert this to a natural language answer."""

    try:
        with tracer.start_as_current_span("synthesize_mcp_response_with_llm") as span:
            span.set_attribute("query", query)
            span.set_attribute("tool_name", tool_name)
            
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.7,
                max_tokens=500
            )
            
            synthesized_response = response.choices[0].message.content.strip()
            span.set_attribute("response_length", len(synthesized_response))
            
            return synthesized_response
            
    except Exception as e:
        logger.error(f"Response synthesis failed: {e}", exc_info=True)
        # Fallback to basic response
        return f"I found information about your query, but had trouble formatting it. Raw data: {str(tool_result)[:200]}"


# ============================================================================
# TIER 2b: A2A PATH HANDLER (STATEGRAPH ORCHESTRATION) - WITH TRACING
# ============================================================================

async def handle_a2a_path(query: str, conversation_id: str) -> tuple[str, Dict[str, Any]]:
    """
    Handle query using A2A agent orchestration via StateGraph
    FULLY INSTRUMENTED with tracing
    
    Returns: (response_text, metadata)
    """
    
    with tracer.start_as_current_span("handle_a2a_path") as span:
        span.set_attribute("query", query)
        span.set_attribute("conversation_id", conversation_id)
        
        if not stategraph_orchestrator:
            logger.error("StateGraph orchestrator not available")
            return ("I'm having trouble processing your request right now. Please try again.", {})
        
        try:
            # Call StateGraph orchestrator
            logger.info(f"Running StateGraph orchestration for conversation: {conversation_id}")
            
            result = await stategraph_orchestrator.process_message(query, conversation_id)
            
            # Extract response and metadata from StateGraph result
            response_text = result.get("response", "")
            
            metadata = {
                "stategraph_intent": result.get("intent"),
                "stategraph_confidence": result.get("confidence"),
                "agents_called": result.get("agents_called", []),
                "graph_execution": result.get("metadata", {}).get("graph_execution", "completed")
            }
            
            # Add span attributes
            span.set_attribute("agents_called", json.dumps(metadata['agents_called']))
            span.set_attribute("agents_count", len(metadata['agents_called']))
            span.set_attribute("response_length", len(response_text))
            
            logger.info(f"StateGraph completed - Agents called: {', '.join(metadata['agents_called'])}")
            
            return response_text, metadata
            
        except Exception as e:
            logger.error(f"Error in A2A path: {e}", exc_info=True)
            span.record_exception(e)
            return (f"I encountered an error processing your request: {str(e)}", {"error": str(e)})


# ============================================================================
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
