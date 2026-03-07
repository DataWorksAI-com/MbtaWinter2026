"""
StateGraph-based Orchestrator for MBTA Agntcy
SEMANTIC AGENT DISCOVERY + QUERY DECOMPOSITION + SLIM TRANSPORT
"""
import os
from typing import TypedDict, Annotated, Sequence, Literal, Dict
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
from .llm_client import get_llm_client
llm = get_llm_client()

import json

# Import SLIM client
try:
    from .slim_client import SlimAgentClient
    SLIM_AVAILABLE = True
except ImportError:
    SLIM_AVAILABLE = False

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

# raw = await llm.complete(system="", user=prompt, max_tokens=300)
# result = json.loads(raw)

REGISTRY_URL = os.getenv("REGISTRY_URL", "http://23.92.17.180:6900")

# Discovery cache
_agent_catalog_cache = None
_catalog_cache_time = None
_catalog_cache_ttl = timedelta(minutes=5)
_current_orchestrator = None


# ============================================================================
# STATE DEFINITION
# ============================================================================

class AgentState(TypedDict):
    """The state that flows through the StateGraph"""
    user_message: str
    conversation_id: str
    intent: str
    confidence: float
    matched_agents: list[str]
    agent_queries: Dict[str, str]  # NEW: Decomposed queries per agent
    messages: Annotated[Sequence[BaseMessage], operator.add]
    agents_called: list[str]
    agent_responses: list[dict]
    final_response: str
    should_end: bool
    llm_matching_decision: dict | None


@dataclass
class AgentConfig:
    name: str
    url: str
    port: int
    description: str
    capabilities: list[str]
    discovered_from_registry: bool = True


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def validate_registry_connection() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{REGISTRY_URL}/health")
            return response.status_code == 200
    except Exception as e:
        logger.error(f"❌ Registry health check failed: {e}")
        return False


async def get_agent_catalog_from_registry() -> list[dict]:
    global _agent_catalog_cache, _catalog_cache_time
    
    if _agent_catalog_cache and _catalog_cache_time:
        if datetime.now() - _catalog_cache_time < _catalog_cache_ttl:
            return _agent_catalog_cache
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{REGISTRY_URL}/list")
            response.raise_for_status()
            agent_list = response.json()
            
            agents_info = []
            for agent_id in agent_list.keys():
                if agent_id == 'agent_status':
                    continue
                
                try:
                    agent_response = await client.get(f"{REGISTRY_URL}/agents/{agent_id}")
                    if agent_response.status_code != 200:
                        continue
                    
                    agent_data = agent_response.json()
                    
                    agents_info.append({
                        "agent_id": agent_data.get("agent_id"),
                        "agent_url": agent_data.get("agent_url"),
                        "description": agent_data.get("description", ""),
                        "capabilities": agent_data.get("capabilities", []),
                        "alive": agent_data.get("alive", False)
                    })
                except Exception as e:
                    continue
            
            _agent_catalog_cache = agents_info
            _catalog_cache_time = datetime.now()
            return agents_info
            
    except Exception as e:
        logger.error(f"❌ Failed to fetch agent catalog: {e}")
        return []


async def semantic_agent_discovery(query: str) -> list[AgentConfig]:
    with tracer.start_as_current_span("semantic_agent_discovery"):
        agent_catalog = await get_agent_catalog_from_registry()
        if not agent_catalog:
            return []
        
        agent_descriptions = [f"• {a['agent_id']}: {a['description']}" for a in agent_catalog]
        catalog_text = "\n".join(agent_descriptions)
        
        prompt = f"""Match user query to available agents.

User Query: "{query}"

Available Agents:
{catalog_text}

Return JSON:
{{
  "matched_agents": ["agent_id_1"],
  "reasoning": "explanation",
  "confidence": 0.9
}}
"""
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            matched_agent_ids = result.get("matched_agents", [])
            
            matched_configs = []
            for agent_id in matched_agent_ids:
                agent_info = next((a for a in agent_catalog if a['agent_id'] == agent_id), None)
                if not agent_info:
                    continue
                
                parsed = urlparse(agent_info['agent_url'])
                config = AgentConfig(
                    name=agent_info['agent_id'],
                    url=f"{parsed.scheme or 'http'}://{parsed.hostname}",
                    port=parsed.port or 80,
                    description=agent_info['description'],
                    capabilities=agent_info.get('capabilities', []),
                    discovered_from_registry=True
                )
                matched_configs.append(config)
            
            return matched_configs
        except Exception as e:
            logger.error(f"❌ Semantic matching failed: {e}")
            return []


async def call_agent_via_slim(slim_client, agent_config: AgentConfig, message: str) -> dict:
    agent_map = {
        "mbta-alerts": "alerts",
        "mbta-route-planner": "planner",
        "mbta-stops": "stopfinder",
        "mbta-planner": "planner",
        "mbta-stopfinder": "stopfinder"
    }
    
    slim_name = agent_map.get(agent_config.name)
    if not slim_name:
        raise ValueError(f"Agent {agent_config.name} not mapped")
    
    return await slim_client.call_agent(slim_name, message)


async def call_agent_via_http(agent_config: AgentConfig, message: str, conversation_id: str) -> dict:
    url = f"{agent_config.url}:{agent_config.port}/a2a/message"
    
    payload = {
        "type": "request",
        "payload": {"message": message, "conversation_id": conversation_id},
        "metadata": {"source": "stategraph", "agent_name": agent_config.name}
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        
        if result.get("type") == "response" and "payload" in result:
            return {
                "response": result["payload"].get("text", ""),
                "agent_used": agent_config.name
            }
        return result


# ============================================================================
# NODE FUNCTIONS
# ============================================================================

async def semantic_discovery_node(state: AgentState) -> AgentState:
    """Match query to agents"""
    with tracer.start_as_current_span("semantic_discovery"):
        matched_agents = await semantic_agent_discovery(state["user_message"])
        matched_agent_ids = [agent.name for agent in matched_agents]
        
        intent = "general"
        confidence = 0.5
        
        if matched_agent_ids:
            first_agent = matched_agents[0]
            desc = first_agent.description.lower()
            
            if any(w in desc for w in ["alert", "delay"]):
                intent, confidence = "alerts", 0.85
            elif any(w in desc for w in ["stop", "station"]):
                intent, confidence = "stops", 0.85
            elif any(w in desc for w in ["route", "planning"]):
                intent, confidence = "trip_planning", 0.85
        
        return {
            **state,
            "matched_agents": matched_agent_ids,
            "intent": intent,
            "confidence": confidence,
            "agent_queries": {},  # Will be filled by decomposition node
            "agent_responses": [],
            "agents_called": [],
            "messages": [HumanMessage(content=state["user_message"])],
            "llm_matching_decision": {"matched_agents": matched_agent_ids}
        }


async def query_decomposition_node(state: AgentState) -> AgentState:
    """
    NEW NODE: Decompose complex queries into agent-specific sub-queries
    """
    with tracer.start_as_current_span("query_decomposition") as span:
        matched_agents = state.get("matched_agents", [])
        user_message = state["user_message"]
        
        # If only 1 agent or no agents, no decomposition needed
        if len(matched_agents) <= 1:
            logger.info("ℹ️  Single agent query - no decomposition needed")
            return {**state, "agent_queries": {}}
        
        logger.info(f"🔧 Decomposing query for {len(matched_agents)} agents")
        
        # Get agent catalog for descriptions
        agent_catalog = await get_agent_catalog_from_registry()
        
        # Build agent context
        agent_context = []
        for agent_id in matched_agents:
            agent_info = next((a for a in agent_catalog if a['agent_id'] == agent_id), None)
            if agent_info:
                agent_context.append(f"• {agent_id}: {agent_info['description']}")
        
        context_text = "\n".join(agent_context)
        
        # Decompose with LLM
        prompt = f"""You are decomposing a complex user query for multiple specialized agents.

Original User Query: "{user_message}"

Matched Agents and their capabilities:
{context_text}

Your Task:
For EACH matched agent, extract ONLY the relevant sub-question from the original query.
Make each sub-query standalone and focused on that agent's capabilities.

Examples:
- Original: "Check delays then find MIT station"
  - mbta-alerts: "Are there any service delays?"
  - mbta-stopfinder: "Find MIT station"

- Original: "I need to get from Park St to Harvard. Are there delays?"
  - mbta-planner: "Route from Park Street to Harvard"
  - mbta-alerts: "Are there any delays?"

Return ONLY valid JSON mapping agent IDs to their specific queries:
{{
  "agent-id-1": "specific focused query for this agent",
  "agent-id-2": "specific focused query for that agent"
}}

Keep queries concise and focused. If the original query doesn't have a relevant part for an agent, use the agent's description to infer what would be helpful.
"""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=300,
                response_format={"type": "json_object"}
            )
            
            agent_queries = json.loads(response.choices[0].message.content)
            
            logger.info(f"✅ Query decomposed:")
            for agent_id, query in agent_queries.items():
                logger.info(f"   • {agent_id}: '{query}'")
            
            span.set_attribute("decomposed_queries", json.dumps(agent_queries))
            
            return {
                **state,
                "agent_queries": agent_queries
            }
            
        except Exception as e:
            logger.error(f"❌ Query decomposition failed: {e}")
            # Fallback: use original query for all
            return {**state, "agent_queries": {}}


async def execute_agents_node(state: AgentState) -> AgentState:
    """Execute agents with decomposed queries"""
    with tracer.start_as_current_span("execute_agents"):
        matched_agent_ids = state.get("matched_agents", [])
        if not matched_agent_ids:
            return {**state, "agents_called": [], "agent_responses": []}
        
        global _current_orchestrator
        agent_catalog = await get_agent_catalog_from_registry()
        agent_queries = state.get("agent_queries", {})
        
        responses = []
        agents_called = []
        
        for agent_id in matched_agent_ids:
            agent_info = next((a for a in agent_catalog if a['agent_id'] == agent_id), None)
            if not agent_info:
                continue
            
            parsed = urlparse(agent_info['agent_url'])
            agent_config = AgentConfig(
                name=agent_info['agent_id'],
                url=f"{parsed.scheme or 'http'}://{parsed.hostname}",
                port=parsed.port or 80,
                description=agent_info['description'],
                capabilities=agent_info.get('capabilities', []),
                discovered_from_registry=True
            )
            
            # Use decomposed query if available, otherwise full query
            agent_specific_query = agent_queries.get(agent_id, state["user_message"])
            
            logger.info(f"📞 Calling {agent_id} with: '{agent_specific_query[:60]}...'")
            
            with tracer.start_as_current_span(f"agent: {agent_id}") as agent_span:
                try:
                    # Try SLIM first
                    if _current_orchestrator and _current_orchestrator.use_slim and _current_orchestrator.slim_client:
                        try:
                            logger.info(f"📡 Calling {agent_id} via SLIM...")
                            result = await call_agent_via_slim(
                                _current_orchestrator.slim_client,
                                agent_config,
                                agent_specific_query  # ← Using decomposed query!
                            )
                            agent_span.set_attribute("transport", "slim")
                            agent_span.set_attribute("query_decomposed", agent_id in agent_queries)
                            logger.info(f"✅ SLIM success for {agent_id}")
                        except Exception as e:
                            logger.warning(f"⚠️  SLIM failed: {e}")
                            result = await call_agent_via_http(
                                agent_config,
                                agent_specific_query,
                                state["conversation_id"]
                            )
                            agent_span.set_attribute("transport", "http_fallback")
                    else:
                        result = await call_agent_via_http(
                            agent_config,
                            agent_specific_query,
                            state["conversation_id"]
                        )
                        agent_span.set_attribute("transport", "http")
                    
                    agent_label = agent_id if not result.get("error") else f"{agent_id} (failed)"
                    responses.append(result)
                    agents_called.append(agent_label)
                    
                except Exception as e:
                    agent_span.record_exception(e)
                    logger.error(f"❌ Exception calling {agent_id}: {e}")
                    responses.append({"response": f"Error: {e}", "error": True})
                    agents_called.append(f"{agent_id} (error)")
        
        return {
            **state,
            "agent_responses": responses,
            "agents_called": agents_called,
            "messages": [
                AIMessage(content=f"{r.get('agent_used', 'agent')}: {r.get('response', '')[:100]}", 
                         name=r.get('agent_used', 'agent'))
                for r in responses if not r.get('error')
            ]
        }


async def synthesize_response_node(state: AgentState) -> AgentState:
    """Synthesize final response"""
    with tracer.start_as_current_span("synthesize"):
        if not state.get("matched_agents", []):
            message = state["user_message"].lower()
            if any(w in message for w in ["hi", "hello", "hey"]):
                return {
                    **state,
                    "final_response": "Hello! I'm MBTA Agntcy with SLIM transport. What can I help you with?",
                    "should_end": True
                }
            else:
                return {
                    **state,
                    "final_response": "I'm specialized in Boston MBTA transit. Try asking about alerts, stops, or routes.",
                    "should_end": True
                }
        
        responses = [r.get("response", "") for r in state.get("agent_responses", []) if not r.get("error") and r.get("response")]
        
        if responses:
            final_response = "\n\n".join(responses)
        else:
            final_response = "Agents are currently unavailable."
        
        return {
            **state,
            "final_response": final_response,
            "should_end": True
        }


# ============================================================================
# ROUTING FUNCTIONS
# ============================================================================

def route_after_discovery(state: AgentState) -> Literal["decompose_query", "synthesize"]:
    """Route to decomposition if agents matched"""
    if state.get("matched_agents", []):
        return "decompose_query"
    else:
        return "synthesize"


def route_after_decomposition(state: AgentState) -> Literal["execute_agents"]:
    """Always execute after decomposition"""
    return "execute_agents"


def route_after_execution(state: AgentState) -> Literal["synthesize"]:
    """Always synthesize after execution"""
    return "synthesize"


# ============================================================================
# BUILD GRAPH
# ============================================================================

def build_mbta_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("semantic_discovery", semantic_discovery_node)
    workflow.add_node("decompose_query", query_decomposition_node)  # NEW NODE
    workflow.add_node("execute_agents", execute_agents_node)
    workflow.add_node("synthesize", synthesize_response_node)
    
    # Entry point
    workflow.set_entry_point("semantic_discovery")
    
    # Edges
    workflow.add_conditional_edges(
        "semantic_discovery",
        route_after_discovery,
        {"decompose_query": "decompose_query", "synthesize": "synthesize"}
    )
    
    # NEW: Route from decomposition to execution
    workflow.add_conditional_edges(
        "decompose_query",
        route_after_decomposition,
        {"execute_agents": "execute_agents"}
    )
    
    workflow.add_conditional_edges(
        "execute_agents",
        route_after_execution,
        {"synthesize": "synthesize"}
    )
    
    workflow.add_edge("synthesize", END)
    
    return workflow.compile()


# ============================================================================
# ORCHESTRATOR
# ============================================================================

class StateGraphOrchestrator:
    def __init__(self):
        global _current_orchestrator
        
        logger.info("=" * 80)
        logger.info("🚀 StateGraph with Query Decomposition + SLIM")
        logger.info("=" * 80)
        
        self.graph = build_mbta_graph()
        
        self.use_slim = os.getenv("USE_SLIM", "false").lower() == "true"
        self.slim_client = None
        
        if self.use_slim and SLIM_AVAILABLE:
            self.slim_client = SlimAgentClient()
            logger.info("✅ SLIM mode enabled")
        
        _current_orchestrator = self
        logger.info("✅ StateGraph initialized")
    
    async def startup_validation(self):
        logger.info("🔍 Startup validation...")
        
        if not await validate_registry_connection():
            raise RuntimeError(f"Registry at {REGISTRY_URL} not accessible")
        
        agent_catalog = await get_agent_catalog_from_registry()
        if agent_catalog:
            logger.info(f"📚 {len(agent_catalog)} agents registered")
        
        if self.use_slim and self.slim_client:
            try:
                await self.slim_client.initialize()
                logger.info("✅ SLIM client initialized")
            except Exception as e:
                logger.warning(f"⚠️  SLIM init failed: {e}")
                self.use_slim = False
        
        logger.info("✅ Startup complete")
    
    async def process_message(self, user_message: str, conversation_id: str) -> dict:
        with tracer.start_as_current_span("stategraph"):
            initial_state: AgentState = {
                "user_message": user_message,
                "conversation_id": conversation_id,
                "intent": "",
                "confidence": 0.0,
                "matched_agents": [],
                "agent_queries": {},
                "messages": [],
                "agents_called": [],
                "agent_responses": [],
                "final_response": "",
                "should_end": False,
                "llm_matching_decision": None
            }
            
            final_state = await self.graph.ainvoke(initial_state)
            
            return {
                "response": final_state["final_response"],
                "intent": final_state["intent"],
                "confidence": final_state["confidence"],
                "matched_agents": final_state.get("matched_agents", []),
                "agents_called": final_state["agents_called"],
                "metadata": {
                    "conversation_id": conversation_id,
                    "discovery": "semantic",
                    "transport": "slim" if self.use_slim else "http",
                    "query_decomposition": final_state.get("agent_queries", {}),
                    "registry_url": REGISTRY_URL
                }
            }
