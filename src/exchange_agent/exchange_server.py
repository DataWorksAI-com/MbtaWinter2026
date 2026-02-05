# src/exchange_agent/exchange_server.py

import sys
import os

# Load environment variables FIRST (before any other imports)
from dotenv import load_dotenv
load_dotenv()  # This loads .env from current directory or parent directories

# Initialize OpenTelemetry BEFORE other imports
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
import random

# Add parent directory to Python path for imports
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Try relative imports first, fall back to absolute
try:
    from .mcp_client import MCPClient
    from .stategraph_orchestrator import StateGraphOrchestrator  
except ImportError:
    from mcp_client import MCPClient
    from stategraph_orchestrator import StateGraphOrchestrator  

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
        logger.info("✅ StateGraph Orchestrator initialized")
        
        # Validate registry connectivity and agent discovery
        logger.info("🔍 Validating registry connectivity...")
        await stategraph_orchestrator.startup_validation()
        logger.info("✅ Registry validation passed - A2A path ready")
        
    except RuntimeError as e:
        logger.error(f"❌ Registry validation failed: {e}")
        logger.error("A2A path unavailable - agents not discoverable")
        stategraph_orchestrator = None
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
    description="Hybrid A2A + MCP Orchestrator with Unified LLM Routing + Shortcut Path",
    version="3.2.0",
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
    path: str  # Now supports: "mcp", "a2a", or "shortcut"
    latency_ms: int
    intent: str
    confidence: float
    metadata: Optional[Dict[str, Any]] = None


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "MBTA Exchange Agent",
        "version": "3.2.0",
        "architecture": "Hybrid A2A + MCP with Unified LLM Routing + Shortcut Path",
        "features": ["shortcut_path_for_greetings", "unified_routing", "multi_agent_orchestration"],
        "optimization": "Single LLM call for Classification + Routing + Tool Selection",
        "mcp_available": mcp_client is not None and mcp_client._initialized,
        "stategraph_available": stategraph_orchestrator is not None,
        "clickhouse_available": clickhouse_logger is not None,
        "status": "healthy"
    }



# ============================================================================
# SHORTCUT PATH HELPERS (NEW in v3.2.0)
# ============================================================================

def is_greeting_or_simple_query(query: str) -> bool:
    """
    Fast pattern matching to detect greetings and simple queries.
    Returns True if query should take shortcut path (no LLM needed).
    """
    query_lower = query.lower().strip()
    query_length = len(query_lower)
    
    # Very short queries are likely greetings or simple
    if query_length < 5:
        return True
    
    # Common greeting patterns
    greeting_patterns = [
        'hi', 'hello', 'hey', 'greetings', 'good morning',
        'good afternoon', 'good evening', 'good day',
        'howdy', 'sup', 'yo', 'hiya', "what's up", 'whats up'
    ]
    
    # Simple help/info queries
    help_patterns = [
        'help', 'what can you do', 'what do you do',
        'who are you', 'what are you', 'introduce yourself',
        'capabilities', 'features', 'commands'
    ]
    
    # Off topic / non MBTA patterns
    offtopic_patterns = [
        'weather', 'time', 'date', 'joke', 'story',
        'game', 'movie', 'song', 'recipe', 'news'
    ]
    
    all_patterns = greeting_patterns + help_patterns + offtopic_patterns
    
    # Check if query matches any pattern
    for pattern in all_patterns:
        if pattern in query_lower:
            return True
    
    # Check if query is just a few words (likely greeting or off topic)
    word_count = len(query_lower.split())
    if word_count <= 3 and query_length < 20:
        # Check if it contains MBTA related keywords
        mbta_keywords = ['mbta', 'train', 'bus', 'subway', 'station', 'stop', 
                        'route', 'line', 'red', 'orange', 'blue', 'green',
                        'alert', 'delay', 'service', 'trip', 'schedule']
        
        has_mbta_keyword = any(keyword in query_lower for keyword in mbta_keywords)
        
        if not has_mbta_keyword:
            return True
    
    return False


def get_shortcut_response(query: str) -> str:
    """
    Generate appropriate response for shortcut path queries.
    No LLM needed, just template responses.
    """
    query_lower = query.lower().strip()
    
    # Greeting responses
    greeting_keywords = ['hi', 'hello', 'hey', 'greetings', 'good morning', 
                        'good afternoon', 'good evening', 'howdy']
    if any(keyword in query_lower for keyword in greeting_keywords):
        responses = [
            "Hello! I'm MBTA Agentcy, your Boston transit assistant. I use semantic agent discovery to connect you with the right transit information. What would you like to know?",
            "Hi there! I specialize in Boston MBTA transit information. I can help you with service alerts, route planning, and finding stops. What do you need?",
            "Hey! I'm your MBTA transit assistant. Ask me about delays, routes, station locations, or trip planning!",
        ]
        return random.choice(responses)
    
    # Help/capability responses
    help_keywords = ['help', 'what can you do', 'what do you do', 'who are you', 
                    'capabilities', 'features', 'commands']
    if any(keyword in query_lower for keyword in help_keywords):
        return """I'm MBTA Agentcy, your intelligent Boston transit assistant! I can help you with:

- **Service Alerts & Delays** - Real time information about disruptions, delays, and service changes
- **Finding Stops & Stations** - Locate stations, find nearby stops, get stop details
- **Route Planning** - Plan trips, get directions, find the best routes
- **Predictions & Schedules** - Next train times, departure schedules, arrival predictions
- **Live Vehicle Tracking** - See where trains and buses are right now

I use advanced agent discovery to find the best way to answer your questions. Just ask me naturally about Boston transit!"""
    
    # Off topic responses
    return "I'm specialized in Boston MBTA transit information. I couldn't find agents that match your query. Could you try asking about:\\n\\n• Service alerts and delays\\n• Finding stops and stations\\n• Planning routes and trips\\n• Train/bus predictions and schedules"

















# ============================================================================
# UNIFIED CLASSIFICATION + ROUTING + TOOL SELECTION (WITH SHORTCUT PATH)
# ============================================================================

async def classify_route_and_select_tool(query: str, available_tools: List[Dict]) -> Dict:
    """
    OPTIMIZED ROUTING with early shortcut detection:
    
    STEP 0: Check for shortcut path (greetings, simple queries)
            -> If matched, return immediately (no LLM call needed)
    
    STEP 1-3: Single LLM call for complex queries:
        1. Intent classification
        2. Path selection (MCP vs A2A)
        3. Tool selection with parameters (if MCP chosen)
    
    This approach:
    - Saves LLM costs on greetings (common in demos/presentations)
    - Reduces latency for simple queries from ~400ms to under 10ms
    - Provides clear visual distinction in Jaeger traces
    - Maintains full capability for transit queries
    
    Returns:
        {
            "path": "shortcut|mcp|a2a",
            "intent": "greeting|alerts|stops|trip_planning|general",
            "confidence": 0.95,
            "reasoning": "Explanation of decision",
            "complexity": 0.0,
            
            # Only if path="shortcut":
            "shortcut_response": "Hello! I'm MBTA Agentcy...",
            "llm_calls": 0,
            "agents_invoked": 0,
            
            # Only if path="mcp":
            "mcp_tool": "mbta_get_alerts",
            "mcp_parameters": {"route_id": "Red"}
        }
    """
    
    with tracer.start_as_current_span("classify_route_and_select_tool") as span:
        span.set_attribute("query", query)
        span.set_attribute("query_length", len(query))
        span.set_attribute("available_tools_count", len(available_tools))
        
        # ================================================================
        # STEP 0: SHORTCUT PATH DETECTION (NO LLM CALL) - NEW in v3.2.0
        # ================================================================
        if is_greeting_or_simple_query(query):
            with tracer.start_as_current_span("shortcut_path_detection") as shortcut_span:
                shortcut_span.set_attribute("matched", True)
                shortcut_span.set_attribute("query_type", "greeting_or_simple")
                
                # Generate response without LLM
                shortcut_response = get_shortcut_response(query)
                
                decision = {
                    "path": "shortcut",
                    "intent": "greeting",
                    "confidence": 1.0,
                    "reasoning": "Simple greeting or general query detected via pattern matching",
                    "complexity": 0.0,
                    "shortcut_response": shortcut_response,
                    "llm_calls": 0,
                    "agents_invoked": 0
                }
                
                # Mark in span for easy Jaeger filtering
                span.set_attribute("routing.path", "shortcut")
                span.set_attribute("routing.method", "pattern_matching")
                span.set_attribute("llm.calls", 0)
                span.set_attribute("intent", "greeting")
                span.set_attribute("confidence", 1.0)
                span.set_attribute("complexity", 0.0)
                
                logger.info(f"⚡ SHORTCUT PATH: {decision['reasoning']}")
                logger.info(f"   Query: {query}")
                logger.info(f"   Response: {shortcut_response[:100]}...")
                logger.info(f"   Cost: $0.00 (no LLM call)")
                
                return decision
        
        # ================================================================
        # NOT A SHORTCUT - Proceed with full LLM routing
        # ================================================================
        span.set_attribute("routing.path", "full_pipeline")
        span.set_attribute("routing.method", "llm_classification")
        
        # Format available tools for the LLM
        tools_list = "\n".join([
            f"  • {tool['name']}: {tool['description']}"
            for tool in available_tools
        ]) if available_tools else "  (No MCP tools available - must use A2A)"
        
        system_prompt = f"""You are an intelligent MBTA query routing system.

**YOUR TASK:** Analyze the query and make ALL routing decisions in one response.

═══════════════════════════════════════════════════════════
STEP 1: CLASSIFY INTENT
═══════════════════════════════════════════════════════════
- "alerts": Service alerts, delays, disruptions, issues
  Examples: "Red Line delays?", "Any issues?", "What's happening on Orange Line?"
  
- "stops": Stop/station information, finding stops, stop details
  Examples: "Find Harvard station", "Stops on Green Line", "Where is Park Street?"
  
- "trip_planning": Route planning, directions, how to get somewhere
  Examples: "Park St to Harvard?", "How do I get to MIT?", "Route to Logan?"
  
- "general": Off topic, non MBTA queries
  Examples: "What's the weather?", "Who won the game?"

═══════════════════════════════════════════════════════════
STEP 2: CHOOSE PATH & SELECT TOOL (IF APPLICABLE)
═══════════════════════════════════════════════════════════

**PATH DECISION TREE:**

┌─ Is query about MBTA transit? ──NO──> path="a2a", intent="general"
│
└─ YES → Is it a simple fact lookup?
          │
          ├─ YES → Can an MCP tool handle it?
          │        │
          │        ├─ YES → path="mcp" + SELECT TOOL (Step 3)
          │        └─ NO  → path="a2a"
          │
          └─ NO (complex/multi step) → path="a2a"

**MCP Path (Fast, ~400ms):**
- Best for: Single API call, real time data, simple fact lookup
- Handles: alerts, predictions, vehicle tracking, stop search, schedules
- Examples: "Red Line delays?", "Next train at Park St?", "Where are trains?"
- → Proceed to STEP 3 to select tool

**A2A Path (Multi Agent, ~1500ms):**
- Best for: Trip planning, conditional logic, multi step reasoning
- Handles: complex routing, considering multiple factors
- Examples: "Park St to Harvard?", "Best route if delays?", "Plan trip to airport"
- → Skip tool selection, return path="a2a"

═══════════════════════════════════════════════════════════
STEP 3: SELECT MCP TOOL (ONLY IF path="mcp")
═══════════════════════════════════════════════════════════

Available MCP Tools:
{tools_list}

**PARAMETER NAMING (CRITICAL):**
- Use "route_id" NOT "route" (e.g., route_id="Red")
- Use "stop_id" NOT "stop" (e.g., stop_id="place-pktrm")
- Use "direction_id" NOT "direction" (0 or 1)
- Use "latitude" and "longitude" for location based queries
- Use "query" for text search (stop names, etc.)

**Common MBTA Route IDs:**
- Red Line: "Red"
- Orange Line: "Orange"
- Blue Line: "Blue"
- Green Line: "Green-B", "Green-C", "Green-D", "Green-E"

**Tool Selection Strategy:**
- Match query intent to tool capability
- Extract parameters from natural language
- If uncertain about parameters, prefer tools that accept fewer params
- Examples:
  * "Red Line delays" → mbta_get_alerts, route_id="Red"
  * "Any alerts?" → mbta_list_all_alerts (no params needed)
  * "Find Harvard" → mbta_search_stops, query="Harvard"

═══════════════════════════════════════════════════════════
CONFIDENCE & COMPLEXITY SCORING
═══════════════════════════════════════════════════════════

**Confidence (0.0 to 1.0):**
- 0.9 to 1.0: Crystal clear intent and perfect tool match
- 0.7 to 0.8: Reasonably clear
- 0.5 to 0.6: Somewhat ambiguous
- 0.0 to 0.4: Very ambiguous or off topic

**Complexity (0.0 to 1.0):**
- 0.0 to 0.3: Simple (single fact, one API call)
- 0.4 to 0.6: Medium (some context needed)
- 0.7 to 1.0: Complex (multi step, coordination, conditional logic)

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════

Return ONLY valid JSON (no markdown, no code blocks):

**If path="mcp":**
{{
  "intent": "alerts",
  "confidence": 0.95,
  "path": "mcp",
  "reasoning": "Simple alert query for Red Line - direct API call possible",
  "complexity": 0.2,
  "mcp_tool": "mbta_get_alerts",
  "mcp_parameters": {{"route_id": "Red"}}
}}

**If path="a2a":**
{{
  "intent": "trip_planning",
  "confidence": 0.9,
  "path": "a2a",
  "reasoning": "Complex routing query requires multi agent coordination",
  "complexity": 0.8
}}

**If general/off topic:**
{{
  "intent": "general",
  "confidence": 0.95,
  "path": "a2a",
  "reasoning": "Not MBTA related, route to general handler",
  "complexity": 0.1
}}"""

        user_message = f"""Query: "{query}"

Analyze this query and provide complete routing decision."""

        try:
            with tracer.start_as_current_span("llm_unified_routing") as llm_span:
                llm_span.set_attribute("model", "gpt-4o-mini")
                llm_span.set_attribute("purpose", "intent_classification_and_routing")
                
                # Make single LLM call
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
                
                # Remove markdown formatting if present
                if decision_text.startswith("```json"):
                    decision_text = decision_text.replace("```json", "").replace("```", "").strip()
                elif decision_text.startswith("```"):
                    decision_text = decision_text.replace("```", "").strip()
                
                # Parse JSON response
                decision = json.loads(decision_text)
                
                # Validate and set defaults
                decision.setdefault("complexity", 0.5)
                decision.setdefault("confidence", 0.5)
                decision.setdefault("reasoning", "No reasoning provided")
                decision["llm_calls"] = 1
                
                # Validate MCP path has required tool info
                if decision["path"] == "mcp":
                    if "mcp_tool" not in decision:
                        logger.warning("MCP path selected but no tool specified - falling back to A2A")
                        decision["path"] = "a2a"
                        decision["reasoning"] += " (fallback: no tool specified)"
                    elif "mcp_parameters" not in decision:
                        logger.warning("MCP path selected but no parameters - using empty dict")
                        decision["mcp_parameters"] = {}
                
                # Add telemetry attributes
                span.set_attribute("intent", decision['intent'])
                span.set_attribute("confidence", decision['confidence'])
                span.set_attribute("path", decision['path'])
                span.set_attribute("complexity", decision['complexity'])
                span.set_attribute("llm.calls", 1)
                
                if decision["path"] == "mcp":
                    span.set_attribute("mcp_tool", decision.get('mcp_tool', 'unknown'))
                    span.set_attribute("mcp_parameters", json.dumps(decision.get('mcp_parameters', {})))
                
                # Log decision
                logger.info(f"🧠 Unified Decision:")
                logger.info(f"   Intent: {decision['intent']} (confidence: {decision['confidence']:.2f})")
                logger.info(f"   Path: {decision['path']} (complexity: {decision['complexity']:.2f})")
                logger.info(f"   Reasoning: {decision['reasoning']}")
                
                if decision["path"] == "mcp":
                    logger.info(f"   Tool: {decision['mcp_tool']}")
                    logger.info(f"   Parameters: {decision['mcp_parameters']}")
                
                return decision
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Raw response: {decision_text}")
            # Fallback to safe default
            return {
                "intent": "general",
                "confidence": 0.3,
                "path": "a2a",
                "reasoning": f"JSON parsing error: {str(e)}",
                "complexity": 0.5,
                "llm_calls": 1
            }
        except Exception as e:
            logger.error(f"Unified routing failed: {e}", exc_info=True)
            # Safe fallback
            return {
                "intent": "general",
                "confidence": 0.3,
                "path": "a2a",
                "reasoning": f"Error in analysis: {str(e)}",
                "complexity": 0.5,
                "llm_calls": 1
            }


# ============================================================================
# MAIN CHAT ENDPOINT (OPTIMIZED WITH SHORTCUT + UNIFIED ROUTING)
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint with optimized hybrid MCP + A2A + Shortcut support
    
    THREE PATHS:
    1. SHORTCUT (~10ms) - Greetings, simple queries (no LLM, pattern matching only)
    2. MCP (~400ms) - Simple transit queries (1 LLM call for routing + 1 for synthesis)
    3. A2A (~1500ms) - Complex multi agent coordination (1 LLM call for routing)
    
    FULLY INSTRUMENTED with OpenTelemetry tracing and ClickHouse logging.
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
        
        logger.info("=" * 80)
        logger.info(f"📨 Received query: {query}")
        logger.info(f"   Conversation ID: {conversation_id}")
        logger.info(f"   User ID: {request.user_id}")
        
        # ============================================================
        # Get available MCP tools
        # ============================================================
        available_tools = []
        if mcp_client and mcp_client._initialized:
            if hasattr(mcp_client, '_available_tools') and mcp_client._available_tools:
                for tool in mcp_client._available_tools:
                    available_tools.append({
                        "name": tool.name,
                        "description": tool.description or "No description available"
                    })
                logger.info(f"📋 {len(available_tools)} MCP tools available")
        
        # ============================================================
        # UNIFIED ROUTING (WITH SHORTCUT CHECK) - NEW in v3.2.0
        # Classification + Routing + Tool Selection Combined
        # ============================================================
        decision = await classify_route_and_select_tool(query, available_tools)
        
        intent = decision["intent"]
        confidence = decision["confidence"]
        chosen_path = decision["path"]
        
        # Log to ClickHouse: User message
        if clickhouse_logger:
            try:
                clickhouse_logger.log_conversation(
                    conversation_id=conversation_id,
                    user_id=request.user_id,
                    role="user",
                    content=query,
                    intent=intent,
                    routed_to_orchestrator=(chosen_path == "a2a"),
                    metadata={
                        "confidence": confidence,
                        "complexity": decision['complexity'],
                        "reasoning": decision['reasoning'],
                        "path": chosen_path
                    }
                )
            except Exception as e:
                logger.warning(f"ClickHouse logging failed: {e}")
        
        # ============================================================
        # Execute chosen path
        # ============================================================
        response_text = ""
        path_taken = ""
        metadata = {
            "unified_decision": {
                "intent": intent,
                "confidence": confidence,
                "path": chosen_path,
                "reasoning": decision["reasoning"],
                "complexity": decision["complexity"],
                "llm_calls": decision.get("llm_calls", 0)
            }
        }
        
        if chosen_path == "shortcut":
            # ========================================================
            # SHORTCUT PATH - Instant response (no LLM, no agents)
            # NEW in v3.2.0
            # ========================================================
            with tracer.start_as_current_span("handle_shortcut_path") as shortcut_span:
                shortcut_span.set_attribute("query", query)
                shortcut_span.set_attribute("response_type", "greeting_or_simple")
                shortcut_span.set_attribute("cost_usd", 0.0)
                shortcut_span.set_attribute("llm.calls", 0)
                shortcut_span.set_attribute("agents.invoked", 0)
                
                response_text = decision["shortcut_response"]
                path_taken = "shortcut"
                
                metadata["shortcut_execution"] = {
                    "method": "pattern_matching",
                    "llm_calls": 0,
                    "cost_usd": 0.0,
                    "agents_invoked": 0
                }
                
                logger.info(f"⚡ SHORTCUT PATH executed")
                logger.info(f"   Cost: $0.00")
                logger.info(f"   LLM Calls: 0")
        
        elif chosen_path == "mcp" and mcp_client and mcp_client._initialized:
            # ========================================================
            # MCP FAST PATH - Tool already selected by unified LLM!
            # ========================================================
            tool_name = decision['mcp_tool']
            tool_params = decision['mcp_parameters']
            
            logger.info(f"🚀 MCP Fast Path:")
            logger.info(f"   Tool: {tool_name}")
            logger.info(f"   Parameters: {tool_params}")
            
            try:
                # Call MCP tool directly - no additional LLM call needed!
                tool_result = await call_mcp_tool_dynamic(tool_name, tool_params)
                
                metadata["mcp_execution"] = {
                    "tool": tool_name,
                    "parameters": tool_params,
                    "success": True
                }
                
                # Synthesize natural language response
                response_text = await synthesize_mcp_response_with_llm(
                    query,
                    tool_name,
                    tool_result
                )
                
                path_taken = "mcp"
                logger.info(f"✅ MCP execution successful")
                
            except Exception as e:
                logger.error(f"❌ MCP execution failed: {e}")
                root_span.record_exception(e)
                
                # Fallback to A2A
                logger.info("↪️  Falling back to A2A path")
                response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
                path_taken = "a2a_fallback"
                metadata.update(a2a_metadata)
                metadata["mcp_error"] = str(e)
        
        elif chosen_path == "a2a":
            # ========================================================
            # A2A MULTI AGENT PATH
            # ========================================================
            logger.info(f"🔄 A2A Path: {decision['reasoning']}")
            
            response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
            path_taken = "a2a"
            metadata.update(a2a_metadata)
        
        else:
            # MCP selected but client not available - fallback
            logger.warning("MCP path selected but client not available - falling back to A2A")
            response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
            path_taken = "a2a_fallback"
            metadata.update(a2a_metadata)
            metadata["fallback_reason"] = "MCP client not available"
        
        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Add final span attributes
        root_span.set_attribute("path_taken", path_taken)
        root_span.set_attribute("latency_ms", latency_ms)
        root_span.set_attribute("intent", intent)
        root_span.set_attribute("confidence", confidence)
        
        logger.info(f"✅ Response generated via {path_taken} in {latency_ms}ms")
        logger.info("=" * 80)
        
        # Log to ClickHouse: Assistant response
        if clickhouse_logger:
            try:
                clickhouse_logger.log_conversation(
                    conversation_id=conversation_id,
                    user_id=request.user_id,
                    role="assistant",
                    content=response_text[:1000],  # Truncate if too long
                    intent=intent,
                    routed_to_orchestrator=(path_taken in ["a2a", "a2a_fallback"]),
                    metadata={
                        "path": path_taken,
                        "latency_ms": latency_ms,
                        "confidence": confidence
                    }
                )
            except Exception as e:
                logger.warning(f"ClickHouse logging failed: {e}")
        
        return ChatResponse(
            response=response_text,
            path=path_taken,
            latency_ms=latency_ms,
            intent=intent,
            confidence=confidence,
            metadata=metadata
        )


# ============================================================================
# MCP TOOL EXECUTION (DYNAMIC DISPATCH)
# ============================================================================

async def call_mcp_tool_dynamic(tool_name: str, parameters: Dict) -> Dict[str, Any]:
    """
    Dynamically call any MCP tool by name with given parameters
    
    Args:
        tool_name: Name of the MCP tool to call
        parameters: Dictionary of parameters to pass to the tool
        
    Returns:
        Tool execution result as dictionary
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
            raise ValueError(f"Unknown MCP tool: {tool_name}")
        
        method = tool_method_map[tool_name]
        
        try:
            logger.info(f"🔧 Calling {tool_name} with params: {parameters}")
            result = await method(**parameters)
            span.set_attribute("success", True)
            span.set_attribute("result_size", len(str(result)))
            logger.info(f"✓ Tool execution successful")
            return result
            
        except Exception as e:
            logger.error(f"Error calling {tool_name}: {e}", exc_info=True)
            span.record_exception(e)
            span.set_attribute("success", False)
            raise


# ============================================================================
# RESPONSE SYNTHESIS (NATURAL LANGUAGE GENERATION)
# ============================================================================

async def synthesize_mcp_response_with_llm(query: str, tool_name: str, tool_result: Dict) -> str:
    """
    Convert MCP JSON response into natural, conversational language
    
    Args:
        query: Original user query
        tool_name: Name of the tool that was called
        tool_result: JSON result from the MCP tool
        
    Returns:
        Natural language response string
    """
    
    system_prompt = """You are a helpful MBTA transit assistant.

Convert the technical API response into a natural, conversational answer.

Guidelines:
- Be concise but informative
- Use natural language, not technical jargon
- Include relevant details (times, locations, routes)
- If there's a lot of data, summarize the most important points
- Be helpful and friendly
- Format times in a readable way (e.g., "in 5 minutes" rather than timestamps)

DO NOT include phrases like:
- "Based on the data"
- "According to the API"
- "The response shows"
- "Here is what I found"

Just answer the question naturally as if you knew this information."""

    # Truncate very large responses to avoid token limits
    tool_result_str = json.dumps(tool_result, indent=2)
    if len(tool_result_str) > 4000:
        tool_result_str = tool_result_str[:4000] + "\n... (truncated)"
    
    user_message = f"""User Query: "{query}"

Tool Used: {tool_name}

API Response:
{tool_result_str}

Convert this to a natural, helpful answer."""

    try:
        with tracer.start_as_current_span("synthesize_mcp_response_with_llm") as span:
            span.set_attribute("query", query)
            span.set_attribute("tool_name", tool_name)
            span.set_attribute("result_size", len(tool_result_str))
            
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
            
            logger.info(f"✓ Response synthesized ({len(synthesized_response)} chars)")
            
            return synthesized_response
            
    except Exception as e:
        logger.error(f"Response synthesis failed: {e}", exc_info=True)
        # Fallback to basic response
        return f"I found information about your query, but had trouble formatting it. Raw data: {str(tool_result)[:200]}..."


# ============================================================================
# A2A PATH HANDLER (STATEGRAPH ORCHESTRATION)
# ============================================================================

async def handle_a2a_path(query: str, conversation_id: str) -> tuple[str, Dict[str, Any]]:
    """
    Handle query using A2A agent orchestration via StateGraph
    FULLY INSTRUMENTED with tracing
    
    Args:
        query: User's query
        conversation_id: Unique conversation identifier
        
    Returns:
        Tuple of (response_text, metadata)
    """
    
    with tracer.start_as_current_span("handle_a2a_path") as span:
        span.set_attribute("query", query)
        span.set_attribute("conversation_id", conversation_id)
        
        if not stategraph_orchestrator:
            logger.error("StateGraph orchestrator not available")
            return (
                "I'm having trouble processing your request right now. Please try again.",
                {"error": "StateGraph orchestrator not available"}
            )
        
        try:
            # Call StateGraph orchestrator
            logger.info(f"🔄 Running StateGraph orchestration")
            
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
            
            logger.info(f"✓ StateGraph completed")
            logger.info(f"   Agents called: {', '.join(metadata['agents_called'])}")
            
            return response_text, metadata
            
        except Exception as e:
            logger.error(f"Error in A2A path: {e}", exc_info=True)
            span.record_exception(e)
            return (
                f"I encountered an error processing your request: {str(e)}",
                {"error": str(e)}
            )


# ============================================================================
# ADDITIONAL ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Detailed health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "components": {
            "mcp_client": {
                "available": mcp_client is not None,
                "initialized": mcp_client._initialized if mcp_client else False,
                "tools_count": len(mcp_client._available_tools) if mcp_client and hasattr(mcp_client, '_available_tools') else 0
            },
            "stategraph": {
                "available": stategraph_orchestrator is not None
            },
            "clickhouse": {
                "available": clickhouse_logger is not None
            }
        }
    }


@app.get("/metrics")
async def get_metrics():
    """Basic metrics endpoint"""
    tools_available = []
    if mcp_client and hasattr(mcp_client, '_available_tools'):
        tools_available = [tool.name for tool in mcp_client._available_tools]
    
    return {
        "mcp_tools_available": len(tools_available),
        "mcp_tools": tools_available,
        "stategraph_available": stategraph_orchestrator is not None,
        "optimization": "unified_llm_routing_with_shortcut",  # Updated
        "llm_calls_per_request": {
            "shortcut_path": 0,  # NEW - no LLM calls
            "mcp_path": 2,  # 1 unified + 1 synthesis
            "a2a_path": 1,  # 1 unified only
        }
    }


# ============================================================================
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 80)
    logger.info("🚀 Starting MBTA Exchange Agent Server")
    logger.info("   Version: 3.1.0")
    logger.info("   Optimization: Unified LLM Routing")
    logger.info("   Cost Reduction: ~33%")
    logger.info("   Latency Improvement: ~200ms")
    logger.info("=" * 80)
    
    uvicorn.run(app, host="0.0.0.0", port=8100)
