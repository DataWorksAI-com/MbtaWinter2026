"""
Exchange Agent - Version 5.1 with Manual Protocol Override
Added: force_protocol parameter for UI control

Forced MCP path uses the exact Smart MCP Only logic:
- select_tools_for_query()
- extract_tool_parameters()
- call_mcp_tool_forced_exact()
- synthesize_response()

Auto and A2A logic remain unchanged.
"""

import sys
import os

# Load environment variables FIRST (before any other imports)
from dotenv import load_dotenv
load_dotenv()

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
from typing import Optional, Dict, Any, List, Literal, Tuple
import logging
import time
import uuid
import json
import asyncio
import random
import re

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

# Initialize OpenAI clients
from openai import OpenAI, AsyncOpenAI
openai_client = OpenAI(api_key=api_key)
async_openai_client = AsyncOpenAI(api_key=api_key)

# Global instances
mcp_client: Optional[MCPClient] = None
stategraph_orchestrator: Optional[StateGraphOrchestrator] = None
clickhouse_logger = None
_route_alias_cache: Dict[str, Any] = {"expires_at": 0.0, "items": []}

# Tracer for OpenTelemetry
try:
    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
    logger.info("✅ OpenTelemetry tracer initialized")
except ImportError:
    class NoOpTracer:
        def start_as_current_span(self, name):
            from contextlib import contextmanager
            @contextmanager
            def _span():
                yield type(
                    'obj',
                    (object,),
                    {
                        'set_attribute': lambda *args: None,
                        'set_status': lambda *args: None,
                        'record_exception': lambda *args: None
                    }
                )()
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

    logger.info("=" * 60)
    logger.info("Starting Exchange Agent v5.1 - Manual Protocol Override")
    logger.info("=" * 60)

    try:
        clickhouse_logger = get_clickhouse_logger()
        logger.info("✅ ClickHouse logger initialized")
    except Exception as e:
        logger.warning(f"⚠️  ClickHouse logger initialization failed: {e}")
        clickhouse_logger = None

    try:
        stategraph_orchestrator = StateGraphOrchestrator()
        logger.info("✅ StateGraph Orchestrator initialized")

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

    logger.info("Shutting down Exchange Agent...")
    if mcp_client:
        await mcp_client.cleanup()
    logger.info("✓ Shutdown complete")


app = FastAPI(
    title="MBTA Exchange Agent",
    description="Hybrid A2A + MCP with Manual Protocol Override",
    version="5.1.0",
    lifespan=lifespan
)

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

    FastAPIInstrumentor.instrument_app(app)
    logger.info("✅ FastAPI auto-instrumentation enabled")

    HTTPXClientInstrumentor().instrument()
    logger.info("✅ HTTPX auto-instrumentation enabled")
except Exception as e:
    logger.warning(f"⚠️  Auto-instrumentation failed: {e}")


# Request/Response models
class ChatRequest(BaseModel):
    query: str
    user_id: Optional[str] = "default_user"
    conversation_id: Optional[str] = None
    force_protocol: Optional[Literal["auto", "mcp", "a2a"]] = "auto"


class ChatResponse(BaseModel):
    response: str
    path: str  # "mcp", "a2a", "shortcut", or "a2a_fallback"
    latency_ms: int
    intent: str
    confidence: float
    metadata: Optional[Dict[str, Any]] = None


@app.get("/")
async def root():
    return {
        "service": "MBTA Exchange Agent",
        "version": "5.1.0",
        "architecture": "Hybrid A2A + MCP with Manual Protocol Override",
        "routing_logic": "GPT-4o-mini semantic classification with manual override",
        "features": ["llm_routing", "domain_analysis", "multi_agent_orchestration", "manual_override"],
        "optimization": "Semantic understanding of query intent",
        "mcp_available": mcp_client is not None and mcp_client._initialized,
        "stategraph_available": stategraph_orchestrator is not None,
        "clickhouse_available": clickhouse_logger is not None,
        "status": "healthy"
    }


# ============================================================
# STEP 0: SHORTCUT PATH DETECTION (NO LLM CALL)
# ============================================================

def is_greeting_or_simple_query(query: str) -> bool:
    """Fast pattern matching to detect greetings and simple queries."""
    query_lower = query.lower().strip()

    word_count = len(query_lower.split())
    if word_count > 10:
        return False

    greeting_patterns = [
        'hi', 'hello', 'hey', 'greetings', 'good morning',
        'good afternoon', 'good evening', 'howdy', 'sup', 'yo'
    ]

    return any(
        query_lower == greeting or query_lower.startswith(greeting + " ")
        for greeting in greeting_patterns
    )


def get_shortcut_response(query: str) -> str:
    """Generate response for shortcut path queries (NO LLM NEEDED)."""
    query_lower = query.lower().strip()

    greeting_keywords = ['hi', 'hello', 'hey', 'greetings']
    if any(keyword in query_lower for keyword in greeting_keywords):
        responses = [
            "Hello! I'm MBTA Agentcy. Ask about service alerts, routes, or stations!",
            "Hi! I can help with Boston MBTA transit info.",
        ]
        return random.choice(responses)

    return "I'm specialized in Boston MBTA transit..."


# ============================================================
# INTELLIGENT EXPERTISE-BASED ROUTING
# ============================================================

def needs_domain_expertise(query: str) -> tuple[bool, str, List[str]]:
    """
    Detect if query needs domain expertise beyond API data.
    """
    query_lower = query.lower()
    detected_patterns = []

    CROWDING = [
        "crowded", "crowd", "busy", "full", "packed", "space",
        "capacity", "occupancy", "how full", "standing room",
        "seats available", "room on"
    ]
    if any(kw in query_lower for kw in CROWDING):
        detected_patterns.append("crowding_analysis")
        return True, "Query requires crowding analysis with domain expertise", detected_patterns

    PREDICTIVE = ["should i wait", "worth waiting", "how long will", "when will"]
    if any(kw in query_lower for kw in PREDICTIVE):
        detected_patterns.append("predictive")
        return True, "Query requires predictive analysis", detected_patterns

    DECISION = ["should i", "recommend", "suggest", "better to", "what should i do"]
    if any(kw in query_lower for kw in DECISION):
        detected_patterns.append("decision_support")
        return True, "Query needs decision support", detected_patterns

    CONDITIONAL = ["if there are", "considering", "depending on"]
    if any(kw in query_lower for kw in CONDITIONAL):
        detected_patterns.append("conditional")
        return True, "Query has conditional logic", detected_patterns

    ANALYTICAL = ["why", "explain", "what caused", "how serious"]
    if any(kw in query_lower for kw in ANALYTICAL):
        detected_patterns.append("analytical")
        return True, "Query needs analytical interpretation", detected_patterns

    if re.search(r"from .+ to .+", query_lower):
        detected_patterns.append("routing")
        return True, "Query requires multi-agent coordination", detected_patterns

    return False, "Simple fact lookup - MCP can handle", detected_patterns


def infer_forced_mcp_tool_and_params(
    query: str,
    intent: str,
    available_tools: List[Dict[str, Any]]
) -> tuple[Optional[str], Dict[str, Any]]:
    """
    Ensure forced MCP has a concrete tool/params even when AUTO classifier
    didn't return mcp_tool.
    """
    tool_names = {t.get("name") for t in available_tools if isinstance(t, dict)}
    q = query.lower()

    def has(tool: str) -> bool:
        return tool in tool_names

    route_match = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:[?.!]|$)", query, flags=re.IGNORECASE)
    if route_match and has("mbta_plan_trip"):
        origin = route_match.group(1).strip()
        destination = route_match.group(2).strip()
        return "mbta_plan_trip", {"from": origin, "to": destination}

    if any(k in q for k in ["alert", "status", "delay", "disruption", "issue"]) and has("mbta_get_alerts"):
        for rid in ["Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E"]:
            if rid.lower().replace("-", " ") in q.replace("-", " "):
                return "mbta_get_alerts", {"route_id": rid}
        return "mbta_get_alerts", {}

    if any(k in q for k in ["stop", "station", "find", "where is"]) and has("mbta_search_stops"):
        return "mbta_search_stops", {"query": query}

    if any(k in q for k in ["next train", "prediction", "arrival", "arrive"]) and has("mbta_get_predictions"):
        return "mbta_get_predictions", {}

    if intent == "trip_planning" and has("mbta_plan_trip"):
        return "mbta_plan_trip", {}
    if intent == "alerts" and has("mbta_get_alerts"):
        return "mbta_get_alerts", {}
    if intent == "stops" and has("mbta_search_stops"):
        return "mbta_search_stops", {"query": query}

    if has("mbta_list_all_alerts"):
        return "mbta_list_all_alerts", {}
    if has("mbta_list_all_routes"):
        return "mbta_list_all_routes", {}
    return None, {}


def _normalize_text_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _extract_routes_from_payload(payload: Any) -> List[Tuple[str, List[str]]]:
    routes: List[Tuple[str, List[str]]] = []

    def _walk(node: Any):
        if isinstance(node, dict):
            route_id = node.get("route_id") or node.get("id")
            if isinstance(route_id, str):
                aliases = [route_id]
                for key in ("long_name", "short_name", "name", "description", "label"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        aliases.append(value.strip())
                routes.append((route_id.strip(), aliases))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return routes


async def get_route_alias_index() -> List[Tuple[str, str]]:
    now = time.time()
    if _route_alias_cache["items"] and _route_alias_cache["expires_at"] > now:
        return _route_alias_cache["items"]

    if not mcp_client or not mcp_client._initialized:
        return []

    try:
        payload = await mcp_client.list_all_routes()
        extracted = _extract_routes_from_payload(payload)
        pairs: List[Tuple[str, str]] = []
        seen = set()

        for route_id, aliases in extracted:
            for alias in aliases:
                normalized = _normalize_text_for_match(alias)
                if len(normalized) < 2:
                    continue
                key = (normalized, route_id)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)

                if normalized.endswith(" line"):
                    stripped = normalized[:-5].strip()
                    if len(stripped) >= 2:
                        key2 = (stripped, route_id)
                        if key2 not in seen:
                            seen.add(key2)
                            pairs.append(key2)

        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        _route_alias_cache["items"] = pairs
        _route_alias_cache["expires_at"] = now + 300
        return pairs
    except Exception as e:
        logger.warning(f"Route alias discovery failed: {e}")
        return []


async def detect_route_ids_in_query(query: str) -> List[str]:
    norm_query = f" {_normalize_text_for_match(query)} "
    alias_pairs = await get_route_alias_index()
    matches: List[Tuple[int, str]] = []

    for alias, route_id in alias_pairs:
        token = f" {alias} "
        idx = norm_query.find(token)
        if idx >= 0:
            matches.append((idx, route_id))

    ordered_ids: List[str] = []
    seen_ids = set()
    for _, route_id in sorted(matches, key=lambda x: x[0]):
        if route_id not in seen_ids:
            seen_ids.add(route_id)
            ordered_ids.append(route_id)

    return ordered_ids


async def expand_mcp_parameter_sets(query: str, tool_name: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(parameters, dict):
        return [parameters]

    route_value = parameters.get("route_id")
    if not isinstance(route_value, str):
        return [parameters]

    route_ids_in_query = await detect_route_ids_in_query(query)
    if len(route_ids_in_query) < 2:
        return [parameters]

    logger.info(f"🔎 Multi-route query detected for {tool_name}: {route_ids_in_query}")
    return [{**parameters, "route_id": route_id} for route_id in route_ids_in_query]


# ============================================================================
# UNIFIED CLASSIFICATION + ROUTING + TOOL SELECTION
# ============================================================================

async def classify_route_and_select_tool(query: str, available_tools: List[Dict], force_protocol: str = "auto") -> Dict:
    """
    OPTIMIZED ROUTING with manual protocol override support
    """
    with tracer.start_as_current_span("classify_route_and_select_tool") as span:
        span.set_attribute("query", query)
        span.set_attribute("force_protocol", force_protocol)
        span.set_attribute("query_length", len(query))
        span.set_attribute("available_tools_count", len(available_tools))

        if force_protocol == "mcp":
            logger.info("🔧 MANUAL OVERRIDE: Forcing MCP path")
            try:
                auto_decision = await classify_route_and_select_tool(query, available_tools, "auto")
                decision = dict(auto_decision)
                decision["path"] = "mcp"
                decision["reasoning"] = "Manual override: User selected MCP protocol (auto tool selection)"
                decision["manual_override"] = True

                if not decision.get("mcp_tool"):
                    inferred_tool, inferred_params = infer_forced_mcp_tool_and_params(
                        query=query,
                        intent=str(decision.get("intent", "")),
                        available_tools=available_tools
                    )
                    if inferred_tool:
                        decision["mcp_tool"] = inferred_tool
                        decision["mcp_parameters"] = inferred_params
                        decision["reasoning"] += f"; inferred tool={inferred_tool}"
                return decision
            except Exception as e:
                logger.error(f"MCP override classification failed: {e}")
                return {
                    "path": "mcp",
                    "intent": "general",
                    "confidence": 0.5,
                    "reasoning": "Manual override with classification error",
                    "manual_override": True,
                    "llm_calls": 1
                }

        elif force_protocol == "a2a":
            logger.info("🔧 MANUAL OVERRIDE: Forcing A2A path")
            return {
                "path": "a2a",
                "intent": "general",
                "confidence": 1.0,
                "reasoning": "Manual override: User selected A2A protocol",
                "complexity": 0.5,
                "llm_calls": 0,
                "manual_override": True
            }

        if is_greeting_or_simple_query(query):
            with tracer.start_as_current_span("shortcut_path_detection") as shortcut_span:
                shortcut_span.set_attribute("matched", True)

                shortcut_response = get_shortcut_response(query)

                decision = {
                    "path": "shortcut",
                    "intent": "greeting",
                    "confidence": 1.0,
                    "reasoning": "Simple greeting detected via pattern matching",
                    "complexity": 0.0,
                    "shortcut_response": shortcut_response,
                    "llm_calls": 0,
                    "manual_override": False
                }

                span.set_attribute("routing.path", "shortcut")
                span.set_attribute("llm.calls", 0)

                logger.info(f"⚡ SHORTCUT PATH: {decision['reasoning']}")
                return decision

        tools_list = "\n".join([
            f"  • {tool['name']}: {tool['description']}"
            for tool in available_tools
        ]) if available_tools else "  (No MCP tools available - must use A2A)"

        system_prompt = f"""You are an intelligent MBTA query routing system.

**YOUR TASK:** Analyze the query and make ALL routing decisions in one response.

═══════════════════════════════════════════════════════════
STEP 1: CLASSIFY INTENT
═══════════════════════════════════════════════════════════

CRITICAL: Understand what counts as MBTA-related!

**"alerts"** - Anything about MBTA service, delays, or disruptions (CURRENT OR HISTORICAL):
  ✅ Current status: "Red Line delays?", "Any issues now?", "Current service disruptions?"
  ✅ Historical patterns: "How long do medical delays take?", "Typical delay duration?", "Usually how long?"
  ✅ Pattern questions: "Based on past data...", "On average...", "Generally how long...", "Typically..."
  ✅ Crowding: "How crowded?", "Is it busy?", "Room on trains?", "Packed?", "Full trains?"
  ✅ Predictions: "Should I wait?", "Worth waiting?", "How long will this last?", "When will it clear?"
  ✅ Analysis: "How serious?", "Why delays?", "What's causing this?"

  PRINCIPLE: If asking about MBTA delays, duration, crowding, patterns, or service status → "alerts"

**"stops"** - Station/stop information, finding stations:
  ✅ "Where is Copley?", "Find Harvard station", "Stops on Green Line"
  ✅ "What station is nearest to X?", "List all stations", "Show me Orange Line stops"

**"trip_planning"** - Route planning, directions, how to get somewhere:
  ✅ "Route from X to Y", "How do I get to X?", "Best route to Y?"
  ✅ "Park St to Harvard?", "Get me from X to Y", "Directions to MIT?"

**"general"** - NOT about MBTA/transit at all (completely off-topic):
  ❌ "What's the weather in Boston?" - Not transit
  ❌ "Who won the Red Sox game?" - Not transit (even though it says "Red")
  ❌ "Boston history facts?" - Not transit
  ❌ "What's 2+2?" - Not transit
  ❌ "Tell me a joke" - Not transit

PRINCIPLE: If query mentions MBTA, trains, T, subway, delays, crowding, stations, routes, or ANY transit topic → NOT "general"!

Only classify as "general" if the query has NOTHING to do with Boston public transit.

═══════════════════════════════════════════════════════════
STEP 2: CHOOSE PATH & SELECT TOOL
═══════════════════════════════════════════════════════════

**Decision Tree:**

Is it MBTA-related?
  ├─ NO → path="a2a", intent="general"
  └─ YES → Does it need analysis/prediction/historical data/expertise?
            ├─ YES → path="a2a" (Domain experts needed)
            │         Examples: "How long do delays take?", "Should I wait?", "How crowded?", "Route from X to Y"
            └─ NO → Can MCP tool provide the answer?
                      ├─ YES → path="mcp" + select tool
                      │         Examples: "Red Line delays RIGHT NOW?", "Next train at Park?"
                      └─ NO → path="a2a"

**MCP Path (Fast, ~400ms):**
- Best for: Current real-time data lookup with single API call
- Examples: "Red Line delays right now?", "Next train at Park St?", "Where are Orange Line trains?"
- NO analysis, NO historical, NO predictions - just current facts

**A2A Path (Domain Experts, ~1500ms):**
- Best for: Requires analysis, expertise, historical data, predictions, or multi-agent coordination
- Examples:
  * "How long do delays usually take?" → Needs historical data from domain expert
  * "Should I wait?" → Needs decision support analysis
  * "How crowded is it?" → Needs crowding analysis
  * "Route from X to Y" → Needs multi-agent coordination
  * "Best route considering delays?" → Needs expert reasoning

PRINCIPLE:
- Current fact → MCP
- Analysis/Prediction/Historical/Expertise → A2A

═══════════════════════════════════════════════════════════
STEP 3: SELECT MCP TOOL (ONLY IF path="mcp")
═══════════════════════════════════════════════════════════

Available MCP Tools:
{tools_list}

**PARAMETER NAMING (CRITICAL):**
- Use "route_id" NOT "route" (e.g., route_id="Red")
- Use "stop_id" NOT "stop"
- Red Line = "Red", Orange = "Orange", Blue = "Blue", Green = "Green-B"

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════

Return ONLY valid JSON (no markdown, no code blocks):

**For A2A path:**
{{
  "intent": "alerts",
  "confidence": 0.95,
  "path": "a2a",
  "reasoning": "Historical MBTA delay pattern question requires domain expertise with historical data",
  "complexity": 0.6
}}

**For MCP path:**
{{
  "intent": "alerts",
  "confidence": 0.95,
  "path": "mcp",
  "reasoning": "Current alert lookup can be answered with direct API call",
  "complexity": 0.2,
  "mcp_tool": "mbta_get_alerts",
  "mcp_parameters": {{"route_id": "Red"}}
}}"""

        user_message = f"""Query: "{query}"

Analyze and provide routing decision."""

        try:
            with tracer.start_as_current_span("llm_unified_routing") as llm_span:
                llm_span.set_attribute("model", "gpt-4o-mini")

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

                if decision_text.startswith("```json"):
                    decision_text = decision_text.replace("```json", "").replace("```", "").strip()
                elif decision_text.startswith("```"):
                    decision_text = decision_text.replace("```", "").strip()

                decision = json.loads(decision_text)
                decision.setdefault("complexity", 0.5)
                decision.setdefault("confidence", 0.5)
                decision.setdefault("reasoning", "No reasoning provided")
                decision["llm_calls"] = 1
                decision["manual_override"] = False

                if decision["path"] == "mcp":
                    if "mcp_tool" not in decision:
                        logger.warning("MCP selected but no tool - fallback to A2A")
                        decision["path"] = "a2a"
                    elif "mcp_parameters" not in decision:
                        decision["mcp_parameters"] = {}

                span.set_attribute("intent", decision['intent'])
                span.set_attribute("confidence", decision['confidence'])
                span.set_attribute("path", decision['path'])

                logger.info("🧠 LLM Decision:")
                logger.info(f"   Intent: {decision['intent']} ({decision['confidence']:.2f})")
                logger.info(f"   Path: {decision['path']} (complexity: {decision['complexity']:.2f})")
                logger.info(f"   Reasoning: {decision['reasoning']}")

                return decision

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return {
                "intent": "general",
                "confidence": 0.3,
                "path": "a2a",
                "reasoning": f"JSON error: {str(e)}",
                "complexity": 0.5,
                "llm_calls": 1,
                "manual_override": False
            }
        except Exception as e:
            logger.error(f"Routing failed: {e}", exc_info=True)
            return {
                "intent": "general",
                "confidence": 0.3,
                "path": "a2a",
                "reasoning": f"Error: {str(e)}",
                "complexity": 0.5,
                "llm_calls": 1,
                "manual_override": False
            }


# ============================================================================
# EXACT SMART MCP ONLY FUNCTIONS FOR FORCED MCP
# ============================================================================

async def select_tools_for_query(query: str) -> List[Dict[str, Any]]:
    """
    Use LLM to intelligently select which MCP tools are needed
    """
    available_tools = []
    if mcp_client and hasattr(mcp_client, '_available_tools'):
        available_tools = [
            {"name": tool.name, "description": tool.description or "No description"}
            for tool in mcp_client._available_tools
        ]

    if not available_tools:
        return []

    tools_description = "\n".join([
        f"- {tool['name']}: {tool['description']}"
        for tool in available_tools[:20]
    ])

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
        response = await async_openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200
        )

        response_text = (response.choices[0].message.content or "").strip()

        if "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        tools_list = json.loads(response_text)
        logger.info(f"🧠 Selected tools: {tools_list}")

        return [{"tool_name": t} for t in tools_list if isinstance(t, str)]

    except Exception as e:
        logger.error(f"Tool selection error: {e}")
        q = query.lower()
        if any(w in q for w in ["delay", "alert", "issue"]):
            return [{"tool_name": "mbta_get_alerts"}]
        elif any(w in q for w in ["stop", "station", "find"]):
            return [{"tool_name": "mbta_search_stops"}]
        elif any(w in q for w in ["route", "plan", "from", "to"]):
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
        response = await async_openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150
        )

        response_text = (response.choices[0].message.content or "").strip()

        if "```" in response_text:
            response_text = response_text.split("```")[1].strip()
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        return json.loads(response_text)

    except Exception as e:
        logger.error(f"Parameter extraction error for {tool_name}: {e}")
        return {}


async def call_mcp_tool_forced_exact(tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Call an MCP tool using the exact Smart MCP Only mapping logic."""
    if not mcp_client:
        raise ValueError("MCP client not initialized")

    logger.info(f"📞 Calling {tool_name} with {parameters}")
    result = await mcp_client.call_tool(tool_name, parameters)
    logger.info(f"✅ {tool_name} returned data")
    return result


async def synthesize_response(query: str, tool_results: Dict[str, Any], tools_used: List[str]) -> str:
    """Convert tool results to natural language"""
    if not tool_results:
        return "No tools were executed. Unable to process query."

    results_text = "\n".join([
        f"**{tool}:**\n{json.dumps(result, indent=2)}"
        for tool, result in tool_results.items()
    ])

    prompt = f"""You are an MBTA transit assistant. Convert API results to helpful responses.

User asked: "{query}"

Tools executed: {', '.join(tools_used)}

Results:
{results_text}

Provide a concise, natural language response that answers the user's question. Be helpful and direct."""

    try:
        response = await async_openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500
        )

        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"Synthesis error: {e}")
        return f"Retrieved {len(tool_results)} tool result(s). Unable to synthesize response."


# ============================================================================
# MAIN CHAT ENDPOINT (with manual override support)
# ============================================================================

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    Main chat endpoint with manual protocol override

    - "auto" - intelligent routing (default)
    - "mcp" - exact Smart MCP Only logic
    - "a2a" - force A2A path
    """
    with tracer.start_as_current_span("chat_endpoint") as root_span:
        start_time = time.time()
        query = request.query
        conversation_id = request.conversation_id or str(uuid.uuid4())
        force_protocol = request.force_protocol or "auto"

        root_span.set_attribute("query", query)
        root_span.set_attribute("conversation_id", conversation_id)
        root_span.set_attribute("user_id", request.user_id)
        root_span.set_attribute("force_protocol", force_protocol)

        if not query or not query.strip():
            raise HTTPException(status_code=400, detail="Query cannot be empty")

        logger.info("=" * 80)
        logger.info(f"📨 Received query: {query}")
        logger.info(f"   Conversation ID: {conversation_id}")
        logger.info(f"   Force Protocol: {force_protocol}")

        available_tools = []
        if mcp_client and mcp_client._initialized:
            if hasattr(mcp_client, '_available_tools') and mcp_client._available_tools:
                for tool in mcp_client._available_tools:
                    available_tools.append({
                        "name": tool.name,
                        "description": tool.description or ""
                    })
                logger.info(f"📋 {len(available_tools)} MCP tools available")

        with tracer.start_as_current_span("routing_with_override") as routing_span:
            needs_expertise, expertise_reasoning, detected_patterns = needs_domain_expertise(query)

            routingSpanReason = expertise_reasoning
            routing_span.set_attribute("needs_expertise", needs_expertise)
            routing_span.set_attribute("reasoning", routingSpanReason)
            routing_span.set_attribute("detected_patterns", str(detected_patterns))

            decision = await classify_route_and_select_tool(query, available_tools, force_protocol)

            if needs_expertise and not decision.get("manual_override"):
                original_path = decision["path"]
                decision["path"] = "a2a"
                decision["reasoning"] = f"EXPERTISE REQUIRED: {expertise_reasoning}"

                if original_path != "a2a":
                    logger.info(f"   ✓ OVERRIDE: {original_path} → a2a (expertise needed)")

            if decision.get("manual_override"):
                if force_protocol == "mcp" and (not mcp_client or not mcp_client._initialized):
                    logger.warning("MCP forced but unavailable - fallback to A2A")
                    decision["path"] = "a2a"
                    decision["reasoning"] = "Manual override requested MCP but unavailable - using A2A"

            intent = decision["intent"]
            confidence = decision["confidence"]
            chosen_path = decision["path"]

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
                        "complexity": decision.get('complexity', 0.5),
                        "reasoning": decision['reasoning'],
                        "path": chosen_path,
                        "force_protocol": force_protocol,
                        "manual_override": decision.get("manual_override", False)
                    }
                )
            except Exception as e:
                logger.warning(f"ClickHouse logging failed: {e}")

        response_text = ""
        path_taken = ""
        metadata = {
            "unified_decision": {
                "intent": intent,
                "confidence": confidence,
                "path": chosen_path,
                "reasoning": decision["reasoning"],
                "complexity": decision.get("complexity", 0.5),
                "llm_calls": decision.get("llm_calls", 0),
                "manual_override": decision.get("manual_override", False),
                "force_protocol": force_protocol
            }
        }

        if chosen_path == "shortcut":
            with tracer.start_as_current_span("handle_shortcut_path"):
                response_text = decision["shortcut_response"]
                path_taken = "shortcut"

                metadata["shortcut_execution"] = {
                    "method": "pattern_matching",
                    "llm_calls": 0,
                    "cost_usd": 0.0
                }

                logger.info("⚡ SHORTCUT PATH executed")

        elif chosen_path == "mcp" and mcp_client and mcp_client._initialized:
            # ========================================================
            # FORCED MCP: EXACT SMART MCP ONLY SERVER LOGIC
            # ========================================================
            if decision.get("manual_override") and force_protocol == "mcp":
                logger.info("🧠 Forced MCP: exact Smart MCP Only flow")

                tools_to_call = await select_tools_for_query(query)
                logger.info(f"✅ Selected {len(tools_to_call)} tools")

                if not tools_to_call:
                    response_text = "I couldn't determine which tools to use for your query. Try asking about MBTA alerts, stops, or route planning."
                    tools_used: List[str] = []
                else:
                    tool_results: Dict[str, Any] = {}
                    tools_used: List[str] = []

                    for tool_call in tools_to_call:
                        tool_name = tool_call['tool_name']

                        try:
                            logger.info(f"📋 Extracting parameters for {tool_name}...")
                            params = await extract_tool_parameters(query, tool_name)

                            logger.info(f"🔧 Calling {tool_name}")
                            result = await call_mcp_tool_forced_exact(tool_name, params)
                            tool_results[tool_name] = result
                            tools_used.append(tool_name)
                            logger.info(f"✅ {tool_name} succeeded")

                        except Exception as e:
                            logger.error(f"❌ {tool_name} failed: {e}")
                            root_span.record_exception(e)
                            tool_results[tool_name] = {"error": str(e)}
                            tools_used.append(f"{tool_name} (error)")

                    logger.info("🎯 Synthesizing response...")
                    response_text = await synthesize_response(query, tool_results, tools_used)

                path_taken = "mcp"
                metadata["mcp_execution"] = {
                    "mode": "forced_exact_smart_mcp",
                    "tools_attempted": len(tools_to_call),
                    "tools_succeeded": len([t for t in locals().get("tools_used", []) if "(error)" not in t]),
                    "tools_used": locals().get("tools_used", [])
                }

            # ========================================================
            # AUTO MCP: KEEP ORIGINAL LOGIC UNCHANGED
            # ========================================================
            else:
                tool_name = decision.get('mcp_tool')
                tool_params = decision.get('mcp_parameters', {})

                if not tool_name:
                    logger.warning("MCP path but no tool - fallback to A2A")
                    response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
                    path_taken = "a2a_fallback"
                    metadata.update(a2a_metadata)
                    metadata["fallback_reason"] = "MCP tool not specified"
                else:
                    logger.info(f"🚀 MCP Fast Path:")
                    logger.info(f"   Tool: {tool_name}")
                    logger.info(f"   Parameters: {tool_params}")

                    try:
                        expanded_params = await expand_mcp_parameter_sets(query, tool_name, tool_params)
                        tool_calls = []
                        call_results = []

                        for params in expanded_params:
                            call_results.append(await call_mcp_tool_dynamic(tool_name, params))
                            tool_calls.append({
                                "tool": tool_name,
                                "parameters": params
                            })

                        if len(call_results) == 1:
                            tool_result = call_results[0]
                        else:
                            tool_result = {
                                "aggregation": {
                                    "tool": tool_name,
                                    "strategy": "multi_call",
                                    "parameter": "route_id",
                                    "calls_count": len(call_results)
                                },
                                "results": call_results
                            }

                        metadata["mcp_execution"] = {
                            "tool": tool_name,
                            "parameters": tool_params,
                            "expanded_calls": tool_calls,
                            "success": True
                        }

                        response_text = await synthesize_mcp_response_with_llm(query, tool_name, tool_result)

                        path_taken = "mcp"
                        logger.info("✅ MCP execution successful")

                    except Exception as e:
                        logger.error(f"❌ MCP execution failed: {e}")
                        root_span.record_exception(e)

                        logger.info("↪️  Falling back to A2A path")
                        response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
                        path_taken = "a2a_fallback"
                        metadata.update(a2a_metadata)
                        metadata["mcp_error"] = str(e)

        elif chosen_path == "a2a":
            logger.info(f"🔄 A2A Path: {decision['reasoning']}")
            response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
            path_taken = "a2a"
            metadata.update(a2a_metadata)

        else:
            logger.warning("MCP selected but unavailable - fallback to A2A")
            response_text, a2a_metadata = await handle_a2a_path(query, conversation_id)
            path_taken = "a2a_fallback"
            metadata.update(a2a_metadata)
            metadata["fallback_reason"] = "MCP unavailable"

        latency_ms = int((time.time() - start_time) * 1000)

        root_span.set_attribute("path_taken", path_taken)
        root_span.set_attribute("latency_ms", latency_ms)

        logger.info(f"✅ Response via {path_taken} in {latency_ms}ms")
        logger.info("=" * 80)

        if clickhouse_logger:
            try:
                clickhouse_logger.log_conversation(
                    conversation_id=conversation_id,
                    user_id=request.user_id,
                    role="assistant",
                    content=response_text[:1000],
                    intent=intent,
                    routed_to_orchestrator=(path_taken in ["a2a", "a2a_fallback"]),
                    metadata={
                        "path": path_taken,
                        "latency_ms": latency_ms,
                        "confidence": confidence,
                        "force_protocol": force_protocol,
                        "manual_override": decision.get("manual_override", False)
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
    """Dynamically call any MCP tool."""
    with tracer.start_as_current_span("call_mcp_tool_dynamic") as span:
        span.set_attribute("tool_name", tool_name)
        span.set_attribute("parameters", json.dumps(parameters))

        logger.info(f"🔧 Calling {tool_name} with params: {parameters}")

        try:
            result = await mcp_client.call_tool(tool_name, parameters)
        except Exception:
            raise

        span.set_attribute("success", True)
        logger.info("✓ Tool execution successful")
        return result


# ============================================================================
# RESPONSE SYNTHESIS
# ============================================================================

async def synthesize_mcp_response_with_llm(query: str, tool_name: str, tool_result: Dict) -> str:
    """Convert MCP JSON response into natural language."""
    system_prompt = """You are a helpful MBTA transit assistant.

Convert the technical API response into a natural, conversational answer.

Be concise but informative. Use natural language, not technical jargon."""

    tool_result_str = json.dumps(tool_result, indent=2)
    if len(tool_result_str) > 4000:
        tool_result_str = tool_result_str[:4000] + "\n... (truncated)"

    user_message = f"""User Query: "{query}"

Tool Used: {tool_name}

API Response:
{tool_result_str}

Convert to natural answer."""

    try:
        with tracer.start_as_current_span("synthesize_response"):
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-4o-mini",
                temperature=0.0,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        return f"I found information but had trouble formatting it: {str(tool_result)[:200]}..."


# ============================================================================
# A2A PATH HANDLER
# ============================================================================

async def handle_a2a_path(query: str, conversation_id: str) -> tuple[str, Dict[str, Any]]:
    """Handle query using A2A agents with domain expertise."""
    with tracer.start_as_current_span("handle_a2a_path") as span:
        span.set_attribute("query", query)
        span.set_attribute("conversation_id", conversation_id)

        if not stategraph_orchestrator:
            logger.error("StateGraph unavailable")
            return (
                "I'm having trouble processing your request. Please try again.",
                {"error": "StateGraph unavailable"}
            )

        try:
            logger.info("🔄 Running StateGraph orchestration")
            result = await stategraph_orchestrator.process_message(query, conversation_id)

            response_text = result.get("response", "")
            metadata = {
                "stategraph_intent": result.get("intent"),
                "stategraph_confidence": result.get("confidence"),
                "agents_called": result.get("agents_called", []),
                "graph_execution": result.get("metadata", {}).get("graph_execution", "completed")
            }

            span.set_attribute("agents_called", json.dumps(metadata['agents_called']))
            span.set_attribute("agents_count", len(metadata['agents_called']))

            logger.info("✓ StateGraph completed")
            logger.info(f"   Agents: {', '.join(metadata['agents_called'])}")

            return response_text, metadata

        except Exception as e:
            logger.error(f"A2A error: {e}", exc_info=True)
            span.record_exception(e)
            return (f"Error: {str(e)}", {"error": str(e)})


# ============================================================================
# HEALTH & METRICS
# ============================================================================

@app.get("/health")
async def health_check():
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
            },
            "routing": {
                "method": "intelligent_with_manual_override",
                "version": "5.1"
            }
        }
    }


@app.get("/metrics")
async def get_metrics():
    tools_available = []
    if mcp_client and hasattr(mcp_client, '_available_tools'):
        tools_available = [tool.name for tool in mcp_client._available_tools]

    return {
        "mcp_tools_available": len(tools_available),
        "mcp_tools": tools_available,
        "stategraph_available": stategraph_orchestrator is not None,
        "version": "5.1.0",
        "routing_method": "intelligent_with_manual_override",
        "features": {
            "auto_routing": True,
            "manual_mcp_override": True,
            "manual_a2a_override": True,
            "shortcut_path": True,
            "domain_expertise_detection": True
        }
    }


if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 80)
    logger.info("🚀 Starting MBTA Exchange Agent Server")
    logger.info("   Version: 5.1.0")
    logger.info("   Routing: Intelligent with Manual Protocol Override")
    logger.info("   Features: Auto routing + UI control buttons")
    logger.info("=" * 80)

    uvicorn.run(app, host="0.0.0.0", port=8100)
