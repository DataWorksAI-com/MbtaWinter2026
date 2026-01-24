"""
StateGraph-based Orchestrator for MBTA Agntcy
FULLY LLM-POWERED: Intent classification and routing via GPT-4o-mini
"""
import os
from typing import TypedDict, Annotated, Sequence, Literal
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
import operator
from dataclasses import dataclass
import asyncio
import httpx
from opentelemetry import trace
import logging
from urllib.parse import urlparse
from datetime import datetime, timedelta
from openai import OpenAI

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Registry configuration
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://23.92.17.180:6900")

# Discovery cache
_discovery_cache = {}
_cache_ttl = timedelta(minutes=5)


# ============================================================================
# STATE DEFINITION
# ============================================================================

class AgentState(TypedDict):
    """The state that flows through the StateGraph"""
    # Input
    user_message: str
    conversation_id: str
    intent: str
    confidence: float
    
    # Agent execution tracking
    messages: Annotated[Sequence[BaseMessage], operator.add]
    agents_to_call: list[str]
    agents_called: list[str]
    
    # Results from agents
    alerts_result: dict | None
    stops_result: dict | None
    planner_result: dict | None
    
    # Final output
    final_response: str
    should_end: bool
    
    # LLM decision metadata
    llm_routing_decision: dict | None


# ============================================================================
# AGENT CONFIGURATION
# ============================================================================

@dataclass
class AgentConfig:
    name: str
    url: str
    port: int


# Hardcoded fallback agents
FALLBACK_AGENTS = {
    "mbta-alerts": AgentConfig("mbta-alerts", "http://96.126.111.107", 8001),
    "mbta-stops": AgentConfig("mbta-stops", "http://96.126.111.107", 8003),
    "mbta-route-planner": AgentConfig("mbta-route-planner", "http://96.126.111.107", 8002),
}


def capability_from_agent_name(agent_name: str) -> str:
    """Map agent name to capability for registry search"""
    return {
        "mbta-alerts": "alerts",
        "mbta-stops": "stops",
        "mbta-route-planner": "trip-planning",
    }.get(agent_name, agent_name)


async def discover_agent(agent_name: str) -> AgentConfig:
    """Discover agent from registry with fallback"""
    
    # Check cache
    cache_key = agent_name
    if cache_key in _discovery_cache:
        cached_agent, cached_time = _discovery_cache[cache_key]
        if datetime.now() - cached_time < _cache_ttl:
            return cached_agent
    
    # Try registry discovery
    capability = capability_from_agent_name(agent_name)
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{REGISTRY_URL}/search",
                params={"capabilities": capability, "alive": "true"}
            )
            response.raise_for_status()
            results = response.json()
            
            if not results or len(results) == 0:
                raise ValueError(f"No agents with capability {capability}")
            
            agent_data = results[0]
            agent_url = agent_data.get("agent_url", "")
            parsed = urlparse(agent_url)
            
            if not parsed.hostname:
                raise ValueError(f"Invalid agent_url: {agent_url}")
            
            discovered_agent = AgentConfig(
                name=agent_data.get("agent_id"),
                url=f"{parsed.scheme}://{parsed.hostname}",
                port=parsed.port or 80
            )
            
            _discovery_cache[cache_key] = (discovered_agent, datetime.now())
            logger.info(f"✅ Discovered: {agent_name} at {discovered_agent.url}:{discovered_agent.port}")
            return discovered_agent
            
    except Exception as e:
        logger.warning(f"⚠️  Registry discovery failed: {e}")
        logger.info(f"📌 Using fallback for {agent_name}")
        return FALLBACK_AGENTS[agent_name]


async def call_agent_api(agent_name: str, message: str) -> dict:
    """Call an agent via A2A protocol"""
    agent = await discover_agent(agent_name)
    url = f"{agent.url}:{agent.port}/a2a/message"
    
    payload = {
        "type": "request",
        "payload": {"message": message, "conversation_id": "stategraph-session"},
        "metadata": {"source": "stategraph-orchestrator", "agent": agent_name}
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        
        if result.get("type") == "response" and "payload" in result:
            return {
                "response": result["payload"].get("text", ""),
                "payload": result["payload"]
            }
        return result


# ============================================================================
# LLM-POWERED INTENT CLASSIFICATION
# ============================================================================

async def classify_with_llm(query: str) -> dict:
    """
    Use GPT-4o-mini to classify intent and determine agent routing strategy.
    Uses response_format for guaranteed JSON output.
    """
    
    prompt = f"""Analyze this MBTA transit query and determine intent and required agents.

Query: "{query}"

Available agents:
- mbta-alerts: Service alerts, delays, disruptions
- mbta-stops: Find stations, stop information  
- mbta-route-planner: Trip planning, route suggestions

Determine:
1. PRIMARY INTENT: alerts, stop_info, trip_planning, or general
2. CONFIDENCE: 0.0-1.0
3. AGENTS NEEDED (ordered): Which agents to call in sequence
   - 1 agent: ["mbta-alerts"] or ["mbta-stops"] or ["mbta-route-planner"]
   - 2 agents: ["mbta-alerts", "mbta-route-planner"] or ["mbta-stops", "mbta-route-planner"]
   - 3 agents: ["mbta-alerts", "mbta-stops", "mbta-route-planner"]

Rules:
- ONLY delays/alerts → ["mbta-alerts"]
- ONLY stops/stations → ["mbta-stops"]
- ONLY routing → ["mbta-route-planner"]
- Delays + routing → ["mbta-alerts", "mbta-route-planner"]
- Stops + routing → ["mbta-stops", "mbta-route-planner"]
- Delays + stops + routing → ["mbta-alerts", "mbta-stops", "mbta-route-planner"]

Return valid JSON only:
{{"intent": "alerts", "confidence": 0.9, "agents_needed": ["mbta-alerts"], "reasoning": "explanation"}}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a routing analyzer. Return ONLY valid JSON, no other text."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300,
            response_format={"type": "json_object"}  # Force JSON output
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse JSON
        import json
        result = json.loads(result_text)
        
        # Validate result has required fields
        if "intent" not in result or "agents_needed" not in result:
            raise ValueError(f"Missing required fields in LLM response: {result}")
        
        logger.info(f"🤖 LLM Classification: intent={result['intent']}, agents={result['agents_needed']}")
        logger.info(f"💭 Reasoning: {result.get('reasoning', 'N/A')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ LLM classification failed: {e}")
        logger.error(f"   Raw response: {result_text if 'result_text' in locals() else 'N/A'}")
        
        # Fallback
        return {
            "intent": "general",
            "confidence": 0.5,
            "agents_needed": [],
            "reasoning": f"LLM classification failed: {str(e)}"
        }


# ============================================================================
# NODE FUNCTIONS
# ============================================================================

async def classify_intent_node(state: AgentState) -> AgentState:
    """
    LLM-POWERED intent classification.
    Uses GPT-4o-mini to determine intent and agent routing strategy.
    """
    with tracer.start_as_current_span("classify_intent_node") as span:
        span.set_attribute("user_message", state["user_message"])
        
        # Call LLM for classification
        llm_result = await classify_with_llm(state["user_message"])
        
        intent = llm_result["intent"]
        confidence = llm_result["confidence"]
        
        logger.info(f"🤖 LLM classified: {intent} (confidence: {confidence:.2f})")
        logger.info(f"🎯 Agents needed: {', '.join(llm_result['agents_needed'])}")
        logger.info(f"💭 Reasoning: {llm_result['reasoning']}")
        
        span.set_attribute("intent", intent)
        span.set_attribute("confidence", confidence)
        span.set_attribute("agents_needed", ",".join(llm_result["agents_needed"]))
        
        return {
            **state,
            "intent": intent,
            "confidence": confidence,
            "llm_routing_decision": llm_result,
            "messages": [HumanMessage(content=state["user_message"])]
        }


async def alerts_agent_node(state: AgentState) -> AgentState:
    """Call alerts agent"""
    with tracer.start_as_current_span("alerts_agent_node"):
        logger.info(f"🚨 Calling alerts agent: {state['user_message'][:50]}...")
        result = await call_agent_api("mbta-alerts", state["user_message"])
        
        return {
            **state,
            "alerts_result": result,
            "agents_called": state.get("agents_called", []) + ["mbta-alerts"],
            "messages": [AIMessage(content=f"Alerts: {result.get('response', '')}", name="alerts-agent")]
        }


async def stops_agent_node(state: AgentState) -> AgentState:
    """Call stops agent"""
    with tracer.start_as_current_span("stops_agent_node"):
        logger.info(f"🚏 Calling stops agent: {state['user_message'][:50]}...")
        result = await call_agent_api("mbta-stops", state["user_message"])
        
        return {
            **state,
            "stops_result": result,
            "agents_called": state.get("agents_called", []) + ["mbta-stops"],
            "messages": [AIMessage(content=f"Stops: {result.get('response', '')}", name="stops-agent")]
        }


async def planner_agent_node(state: AgentState) -> AgentState:
    """Call route planner agent"""
    with tracer.start_as_current_span("planner_agent_node"):
        logger.info(f"🗺️  Calling planner agent: {state['user_message'][:50]}...")
        result = await call_agent_api("mbta-route-planner", state["user_message"])
        
        return {
            **state,
            "planner_result": result,
            "agents_called": state.get("agents_called", []) + ["mbta-route-planner"],
            "messages": [AIMessage(content=f"Route: {result.get('response', '')}", name="planner-agent")]
        }


async def synthesize_response_node(state: AgentState) -> AgentState:
    """Synthesize all agent responses"""
    with tracer.start_as_current_span("synthesize_response_node"):
        
        # Handle general queries
        if state["intent"] == "general":
            message = state["user_message"].lower()
            
            if any(word in message for word in ["hi", "hello", "hey"]):
                return {
                    **state,
                    "final_response": "Hello! I'm MBTA Agntcy, your Boston transit assistant. I can help you with service alerts, stop information, and trip planning. What would you like to know?",
                    "should_end": True
                }
            else:
                return {
                    **state,
                    "final_response": "I'm specialized in helping with Boston MBTA transit information. I can help you with:\n• Service alerts and delays\n• Finding stops and stations\n• Planning routes and trips\n\nWhat can I help you with today?",
                    "should_end": True
                }
        
        # Collect agent responses
        responses = []
        
        if state.get("alerts_result"):
            alert_response = state["alerts_result"].get("response", "")
            if alert_response and alert_response.strip():
                responses.append(alert_response)
        
        if state.get("stops_result"):
            stop_response = state["stops_result"].get("response", "")
            if stop_response and stop_response.strip() and \
               "couldn't" not in stop_response.lower():
                responses.append(stop_response)
        
        if state.get("planner_result"):
            planner_response = state["planner_result"].get("response", "")
            if planner_response and planner_response.strip():
                responses.append(planner_response)
        
        final_response = "\n\n".join(filter(None, responses)) if responses else \
            "I received your request but couldn't generate a complete response. Please try rephrasing."
        
        return {
            **state,
            "final_response": final_response,
            "should_end": True
        }


# ============================================================================
# LLM-POWERED ROUTING FUNCTIONS
# ============================================================================

def route_after_intent(state: AgentState) -> Literal["alerts", "stops", "planner", "synthesize"]:
    """
    LLM-informed routing.
    Uses LLM's agents_needed decision to determine first agent.
    """
    llm_decision = state.get("llm_routing_decision", {})
    agents_needed = llm_decision.get("agents_needed", [])
    
    if not agents_needed:
        # Fallback to intent-based routing
        intent = state["intent"]
        if intent == "alerts":
            return "alerts"
        elif intent in ["stop_info", "stops"]:
            return "stops"
        elif intent == "trip_planning":
            return "planner"
        else:
            return "synthesize"
    
    # Route based on LLM's agent sequence
    first_agent = agents_needed[0]
    
    logger.info(f"🤖 LLM routing: First agent = {first_agent} (sequence: {agents_needed})")
    
    if "alerts" in first_agent:
        return "alerts"
    elif "stops" in first_agent:
        return "stops"
    elif "planner" in first_agent or "route" in first_agent:
        return "planner"
    else:
        return "synthesize"


def route_after_alerts(state: AgentState) -> Literal["stops", "planner", "synthesize"]:
    """
    LLM-informed routing after alerts.
    Checks LLM's agent sequence to see what comes next.
    """
    llm_decision = state.get("llm_routing_decision", {})
    agents_needed = llm_decision.get("agents_needed", [])
    
    # If LLM said we need more agents after alerts
    if len(agents_needed) > 1 and agents_needed[0] == "mbta-alerts":
        next_agent = agents_needed[1]
        
        if "stops" in next_agent:
            logger.info(f"🤖 LLM chaining: alerts → stops")
            return "stops"
        elif "planner" in next_agent or "route" in next_agent:
            logger.info(f"🤖 LLM chaining: alerts → planner")
            return "planner"
    
    # Default: just synthesize
    return "synthesize"


def route_after_stops(state: AgentState) -> Literal["planner", "synthesize"]:
    """
    LLM-informed routing after stops.
    Checks if planner is next in LLM's sequence.
    """
    llm_decision = state.get("llm_routing_decision", {})
    agents_needed = llm_decision.get("agents_needed", [])
    
    # Check if planner is in the sequence after stops
    if "mbta-route-planner" in agents_needed or "planner" in str(agents_needed):
        # Find position of stops in sequence
        for i, agent in enumerate(agents_needed):
            if "stops" in agent:
                # If there's another agent after stops
                if i + 1 < len(agents_needed):
                    logger.info(f"🤖 LLM chaining: stops → planner")
                    return "planner"
                break
    
    return "synthesize"


def route_after_planner(state: AgentState) -> Literal["synthesize"]:
    """Always synthesize after planner"""
    return "synthesize"


# ============================================================================
# BUILD THE GRAPH
# ============================================================================

def build_mbta_graph() -> StateGraph:
    """Build StateGraph with LLM-powered routing"""
    
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("alerts", alerts_agent_node)
    workflow.add_node("stops", stops_agent_node)
    workflow.add_node("planner", planner_agent_node)
    workflow.add_node("synthesize", synthesize_response_node)
    
    workflow.set_entry_point("classify_intent")
    
    # Conditional edges
    workflow.add_conditional_edges(
        "classify_intent",
        route_after_intent,
        {
            "alerts": "alerts",
            "stops": "stops",
            "planner": "planner",
            "synthesize": "synthesize"
        }
    )
    
    workflow.add_conditional_edges(
        "alerts",
        route_after_alerts,
        {
            "stops": "stops",
            "planner": "planner",
            "synthesize": "synthesize"
        }
    )
    
    workflow.add_conditional_edges(
        "stops",
        route_after_stops,
        {
            "planner": "planner",
            "synthesize": "synthesize"
        }
    )
    
    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "synthesize": "synthesize"
        }
    )
    
    workflow.add_edge("synthesize", END)
    
    return workflow.compile()


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

class StateGraphOrchestrator:
    """LLM-powered multi-agent orchestrator"""
    
    def __init__(self):
        self.graph = build_mbta_graph()
        logger.info("✅ StateGraph initialized (FULLY LLM-POWERED)")
    
    async def process_message(self, user_message: str, conversation_id: str) -> dict:
        """Process message through LLM-powered StateGraph"""
        with tracer.start_as_current_span("stategraph_orchestrator") as span:
            span.set_attribute("conversation_id", conversation_id)
            
            initial_state: AgentState = {
                "user_message": user_message,
                "conversation_id": conversation_id,
                "intent": "",
                "confidence": 0.0,
                "messages": [],
                "agents_to_call": [],
                "agents_called": [],
                "alerts_result": None,
                "stops_result": None,
                "planner_result": None,
                "final_response": "",
                "should_end": False,
                "llm_routing_decision": None
            }
            
            final_state = await self.graph.ainvoke(initial_state)
            
            span.set_attribute("intent", final_state["intent"])
            span.set_attribute("agents_called", ",".join(final_state["agents_called"]))
            
            return {
                "response": final_state["final_response"],
                "intent": final_state["intent"],
                "confidence": final_state["confidence"],
                "agents_called": final_state["agents_called"],
                "metadata": {
                    "conversation_id": conversation_id,
                    "graph_execution": "completed",
                    "llm_decision": final_state.get("llm_routing_decision"),
                    "discovery": "registry-with-fallback"
                }
            }


async def main():
    """Test LLM-powered orchestrator"""
    orchestrator = StateGraphOrchestrator()
    
    test_queries = [
        "Red Line delays?",  # 1 agent
        "Best route to Harvard considering delays",  # 2 agents
        "Check delays, find nearby stops, plan route to Harvard"  # 3 agents
    ]
    
    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        print('='*60)
        
        result = await orchestrator.process_message(query, f"test-{hash(query)}")
        
        print(f"\nIntent: {result['intent']} (confidence: {result['confidence']})")
        print(f"Agents Called: {', '.join(result['agents_called'])}")
        print(f"LLM Decision: {result['metadata'].get('llm_decision')}")
        print(f"\nResponse:\n{result['response']}")


if __name__ == "__main__":
    asyncio.run(main())
