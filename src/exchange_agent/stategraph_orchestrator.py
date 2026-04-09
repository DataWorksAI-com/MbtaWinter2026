"""
StateGraph Orchestrator - v4.4 with Domain Expertise Context Passing
Skips StopFinder for known station names, passes alerts analysis to planner
Version: 4.4 - Domain Expert Coordination
"""

import os
from typing import TypedDict, Annotated, Sequence, Literal, Dict, List, Any, Optional
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
import json
import re

# Import SLIM client
try:
    from .slim_client import SlimAgentClient
    SLIM_AVAILABLE = True
except ImportError:
    SLIM_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("SLIM not available")

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

# Configuration
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://registry:6900")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
USE_SWITCHBOARD = os.getenv("USE_SWITCHBOARD", "false").lower() in ("1", "true", "yes", "on")

# Caching
_agent_catalog_cache = None
_catalog_cache_time = None
_catalog_cache_ttl = timedelta(minutes=5)
_switchboard_cache = None
_switchboard_cache_time = None
_switchboard_cache_ttl = timedelta(minutes=2)
_current_orchestrator = None


# ============================================================================
# STATE
# ============================================================================

class AgentState(TypedDict):
    user_message: str
    conversation_id: str
    intent: str
    confidence: float
    matched_agents: List[str]
    discovered_agent_configs: List[Dict[str, Any]]
    agent_queries: Dict[str, str]
    llm_matching_decision: Dict[str, Any]
    messages: Annotated[Sequence[BaseMessage], operator.add]
    agents_called: List[str]
    agent_responses: List[Dict[str, Any]]
    
    # Parsed values
    origin_text: str
    destination_text: str
    
    # Resolved station names
    resolved_origin: str
    resolved_destination: str
    
    # Disruption tracking - NEW: Store full domain analysis
    has_disruptions: bool
    affected_routes: List[str]
    severity_level: str
    alerts_domain_analysis: Dict[str, Any]  # NEW: Full analysis from Alerts Agent
    
    final_response: str
    should_end: bool
    routing_decision: str


@dataclass
class AgentConfig:
    name: str
    url: str
    port: int
    description: str
    capabilities: List[str]
    discovered_from_registry: bool = True


# ============================================================================
# QUERY PARSING
# ============================================================================

def extract_origin_destination(query: str) -> Dict[str, str]:
    """Extract origin and destination from natural language"""
    q = query.lower()
    result = {"origin": "", "destination": ""}
    
    # Pattern 1: "I am at X and ... to Y"
    match = re.search(r"i(?:'m| am) at (.+?) and (?:need to |want to )?get to (.+?)(?:\s+in time|\s+for|\s+\.|$)", q)
    if match:
        result["origin"] = match.group(1).strip()
        result["destination"] = match.group(2).strip()
        logger.info(f"✓ PARSED (pattern 1): origin='{result['origin']}', dest='{result['destination']}'")
        return result
    
    # Pattern 2: "route from X to Y" or "from X to Y"
    match = re.search(r"(?:route |get )?from (.+?) to (.+?)(?:\s+in time|\s+for|[,.]|$)", q)
    if match:
        result["origin"] = match.group(1).strip().rstrip(".,")
        result["destination"] = match.group(2).strip().rstrip(".,")
        logger.info(f"✓ PARSED (pattern 2): origin='{result['origin']}', dest='{result['destination']}'")
        return result
    
    # Pattern 3: "get to Y" or "to Y"
    match = re.search(r"(?:get |go )?to (.+?)(?:\s+in time|\s+for|\s+\.|$)", q)
    if match:
        result["destination"] = match.group(1).strip()
        logger.info(f"✓ PARSED (pattern 3): dest='{result['destination']}'")
        return result
    
    logger.warning(f"⚠️ No parse match for: '{query}'")
    return result


def is_likely_station_name(text: str) -> bool:
    """Check if text is likely already an MBTA station name"""
    if not text:
        return False
    
    text_lower = text.strip().lower()
    
    # Common MBTA station name patterns
    station_indicators = [
        "station", "square", "/mit", "kendall",
        # Red Line
        "alewife", "davis", "porter", "harvard", "central",
        "park street", "downtown crossing", "south station",
        "broadway", "andrew", "jfk", "quincy",
        # Orange Line
        "oak grove", "malden", "wellington", "sullivan",
        "community college", "north station", "haymarket",
        "state", "chinatown", "tufts", "back bay",
        "forest hills", "ruggles",
        # Blue Line
        "wonderland", "revere", "beachmont", "suffolk downs",
        "airport", "wood island", "aquarium", "government center",
        # Green Line
        "lechmere", "science park", "north station", "haymarket",
        "government center", "park street", "boylston",
        "arlington", "copley", "hynes", "kenmore",
        "blandford", "boston university", "pleasant street",
        "saint paul", "kent street", "st paul", "hawes street",
        "longwood", "brookline village", "brookline hills",
        "beaconsfield", "reservoir", "chestnut hill",
        "newton", "riverside", "heath street",
        # Common words
        "street", "square", "center", "place", "ave"
    ]
    
    if any(indicator in text_lower for indicator in station_indicators):
        logger.info(f"✓ '{text}' looks like a station name (skipping resolution)")
        return True
    
    if len(text.split()) <= 2 and len(text) < 20:
        return False
    
    logger.info(f"✓ '{text}' looks like a landmark (needs resolution)")
    return False


def extract_station_from_stopfinder(text: str) -> str:
    """Extract station name from StopFinder response"""
    # Pattern: "Found: Kenmore in Boston"
    match = re.search(r"Found:\s*([^\n]+)", text)
    if match:
        station = match.group(1).strip()
        station = re.sub(r' in [A-Z][a-z]+', '', station)
        station = station.split('\n')[0].split('📍')[0].strip()
        return station
    
    # Pattern: "1. Station Name (City)"
    match = re.search(r'^\d+\.\s+([^(♿\n]+)', text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    
    return ""


def extract_alerts_domain_analysis(alerts_response: str) -> Dict[str, Any]:
    """
    Extract domain analysis from Alerts Agent response.
    
    FIXED: Only suggests avoiding routes for SEVERE disruptions,
    not minor scheduled work.
    """
    analysis = {
        "has_analysis": False,
        "overall_recommendation": "unknown",
        "affected_routes": [],
        "severity": "unknown",
        "should_avoid_routes": [],
        "delay_impact": "unknown"
    }
    
    if not alerts_response:
        return analysis
    
    # Detect if it's just scheduled maintenance (not severe disruption)
    is_scheduled = "scheduled" in alerts_response.lower() or "📋" in alerts_response
    
    # Extract delay impact for scheduled work
    impact_match = re.search(r"(\d+)-(\d+)\s*minutes?\s*additional", alerts_response.lower())
    if impact_match:
        impact_min = int(impact_match.group(1))
        impact_max = int(impact_match.group(2))
        analysis["delay_impact"] = f"{impact_min}-{impact_max} min"
        
        # Only avoid if impact is severe (>20 min)
        if impact_max > 20:
            analysis["severity"] = "major"
        else:
            analysis["severity"] = "minor"
    
    # Extract affected routes
    for line in ["Red Line", "Orange Line", "Blue Line", "Green Line"]:
        if line in alerts_response:
            analysis["affected_routes"].append(line.split()[0])  # Just "Red", "Orange", etc.
    
    # CRITICAL FIX: Only avoid routes for SEVERE disruptions
    # Scheduled work with <20 min impact = don't avoid, just note
    if is_scheduled:
        if analysis["severity"] == "minor":
            # Minor scheduled work - don't avoid route
            analysis["should_avoid_routes"] = []
            analysis["overall_recommendation"] = "allow_extra_time"
            logger.info(f"✓ Scheduled work with minor impact - route still usable")
        else:
            # Major scheduled work - might avoid
            analysis["should_avoid_routes"] = analysis["affected_routes"]
            analysis["overall_recommendation"] = "consider_alternative"
            logger.info(f"✓ Major scheduled work - consider alternatives")
    else:
        # Active incident - check severity
        if "🔴" in alerts_response or "critical" in alerts_response.lower():
            analysis["severity"] = "critical"
            analysis["should_avoid_routes"] = analysis["affected_routes"]
            analysis["overall_recommendation"] = "take_alternative"
        elif "🟠" in alerts_response or "major" in alerts_response.lower():
            analysis["severity"] = "major"
            analysis["should_avoid_routes"] = analysis["affected_routes"]
            analysis["overall_recommendation"] = "take_alternative"
    
    logger.info(f"✓ Context: severity={analysis['severity']}, avoid={analysis['should_avoid_routes']}, impact={analysis['delay_impact']}")
    
    return analysis


# ============================================================================
# REGISTRY
# ============================================================================

async def validate_registry() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{REGISTRY_URL}/health")
            return r.status_code == 200
    except:
        return False


async def get_agent_catalog() -> List[Dict]:
    """Legacy single-registry catalog load. Used as fallback when switchboard is off."""
    global _agent_catalog_cache, _catalog_cache_time
    
    if _agent_catalog_cache and _catalog_cache_time:
        if datetime.now() - _catalog_cache_time < _catalog_cache_ttl:
            return _agent_catalog_cache
    
    try:
        import urllib.request as _ur, asyncio as _aio, json as _json
        _resp = await _aio.to_thread(
            lambda: _json.loads(_ur.urlopen(f"{REGISTRY_URL}/list", timeout=10).read())
        )
        agent_list = _resp

        agents = []
        for aid in agent_list.keys():
            if aid == 'agent_status':
                continue
            try:
                _ar = await _aio.to_thread(
                    lambda a=aid: _json.loads(_ur.urlopen(f"{REGISTRY_URL}/agents/{a}", timeout=10).read())
                )
                if _ar.get("alive") or _ar.get("status") == "alive" or True:
                    agents.append(_ar)
            except:
                pass

        _agent_catalog_cache = agents
        _catalog_cache_time = datetime.now()
        logger.info(f"✓ Registry: {len(agents)} agents")
        return agents
    except Exception as e:
        logger.error(f"Registry error: {e}")
        return []


# ============================================================================
# SWITCHBOARD DISCOVERY
# ============================================================================

def _infer_capabilities(query: str) -> List[str]:
    """Derive capability hints from a user query for switchboard filtering."""
    q = query.lower()
    caps: List[str] = []

    if any(w in q for w in ["route", "plan", "trip", "get from", "get to", "directions", "how do i get"]):
        caps.extend(["routing", "route", "trip", "alerts", "stops"])
    else:
        if any(w in q for w in ["alert", "delay", "disruption", "wait", "crowding", "status"]):
            caps.append("alerts")
        if any(w in q for w in ["stop", "station", "find", "where is", "nearest"]):
            caps.append("stops")

    return caps


async def get_switchboard_candidates(capabilities: Optional[List[str]] = None) -> List[Dict]:
    """Query the Switchboard /discover endpoint for federated candidates."""
    global _switchboard_cache, _switchboard_cache_time

    cache_key = ",".join(sorted(capabilities)) if capabilities else "__all__"

    if _switchboard_cache and _switchboard_cache_time:
        if datetime.now() - _switchboard_cache_time < _switchboard_cache_ttl:
            cached = _switchboard_cache.get(cache_key)
            if cached is not None:
                return cached

    import urllib.request as _ur, asyncio as _aio, json as _json
    from urllib.parse import urlencode as _ue

    params: Dict[str, str] = {}
    if capabilities:
        params["capabilities"] = ",".join(capabilities)

    qs = f"?{_ue(params)}" if params else ""
    url = f"{REGISTRY_URL}/switchboard/discover{qs}"

    try:
        raw = await _aio.to_thread(
            lambda: _json.loads(_ur.urlopen(url, timeout=10).read())
        )
        candidates = raw.get("candidates", [])
        registries = raw.get("registries_queried", [])
        logger.info(f"✓ Switchboard: {len(candidates)} candidates from {registries}")

        if _switchboard_cache is None:
            _switchboard_cache = {}
        _switchboard_cache[cache_key] = candidates
        _switchboard_cache_time = datetime.now()
        return candidates
    except Exception as e:
        logger.error(f"Switchboard error: {e}, falling back to local catalog")
        return await get_agent_catalog()


def _normalize_agent_id(agent: Dict) -> str:
    """Return the canonical local agent_id regardless of federation prefix or skill- prefix."""
    aid = agent.get("agent_id", "")
    # Strip federation prefixes like @mit: @neu: @agntcy:
    if aid.startswith("@") and ":" in aid:
        aid = aid.split(":", 1)[1]
    # MIT NANDA prefixes agent names with "skill-" (e.g. skill-mbta-stopfinder -> mbta-stopfinder)
    if aid.startswith("skill-"):
        aid = aid[len("skill-"):]
    return aid


def _deduplicate_candidates(candidates: List[Dict]) -> List[Dict]:
    """Keep the best record per logical agent.

    Priority reflects the target demo topology:
      agntcy (planner) > mit (stopfinder) > nanda (local) > neu
    """
    priority = {"agntcy": 0, "mit": 1, "nanda": 2, "neu": 3}
    best: Dict[str, Dict] = {}
    for c in candidates:
        canonical = _normalize_agent_id(c)
        source = c.get("source_registry", "nanda")
        existing = best.get(canonical)
        if existing is None or priority.get(source, 99) < priority.get(existing.get("source_registry", "nanda"), 99):
            best[canonical] = c
    return list(best.values())


async def semantic_discovery(query: str) -> List[AgentConfig]:
    """Discover agents for a query. Uses Switchboard when enabled, else legacy catalog."""
    if USE_SWITCHBOARD:
        caps = _infer_capabilities(query)
        candidates = await get_switchboard_candidates(caps if caps else None)
        candidates = _deduplicate_candidates(candidates)
        logger.info(f"✓ Switchboard discovery: {len(candidates)} candidates (caps={caps})")
    else:
        candidates = await get_agent_catalog()

    if not candidates:
        return []
    
    descriptions = [f"• {a.get('agent_id', 'unknown')}: {a.get('description', '')}" for a in candidates]
    catalog_text = "\n".join(descriptions)
    
    prompt = f"""Match query to the most relevant agents based on what the user actually needs.

Query: "{query}"

Available Agents:
{catalog_text}

MATCHING RULES:
1. If query is ONLY about alerts/delays/disruptions (no routing) → ONLY alerts agent
   Examples: "Red Line delays?", "Should I wait?", "How long will delays last?", "Why delays?"

2. If query asks for route/directions with origin and destination → stopfinder + alerts + planner
   Examples: "Route from Park to Harvard", "Get from X to Y", "How do I get to Harvard?"

3. If query asks about stops/stations (no routing) → ONLY stopfinder agent
   Examples: "Find Harvard station", "Stops on Red Line"

DO NOT match planner agent for queries that don't have explicit routing intent (from X to Y).
DO NOT match planner for queries asking about delay duration, wait times, or disruption analysis.

Return JSON with ONLY the agents truly needed: {{"matched_agents": ["id1", "id2"]}}
"""
    
    try:
        import urllib.request as _urllib
        import asyncio as _asyncio
        _payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 150,
            "response_format": {"type": "json_object"}
        }).encode()
        _req = _urllib.Request(
            "https://api.openai.com/v1/chat/completions",
            data=_payload,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        _resp = await _asyncio.to_thread(
            lambda: json.loads(_urllib.urlopen(_req, timeout=10).read())
        )
        result = json.loads(_resp["choices"][0]["message"]["content"])
        matched_ids = result.get("matched_agents", [])
        
        configs = []
        for aid in matched_ids:
            # Match by exact ID or by canonical name (handles @neu:X, @agntcy:X)
            ainfo = next((a for a in candidates if a.get('agent_id') == aid), None)
            if ainfo is None:
                ainfo = next((a for a in candidates if _normalize_agent_id(a) == aid), None)
            if ainfo:
                p = urlparse(ainfo.get('agent_url', ''))
                configs.append(AgentConfig(
                    name=ainfo.get('agent_id', aid),
                    url=f"{p.scheme or 'http'}://{p.hostname}" if p.hostname else "",
                    port=p.port or (443 if p.scheme == 'https' else 80),
                    description=ainfo.get('description', ''),
                    capabilities=ainfo.get('capabilities', [])
                ))
        
        source_label = "switchboard" if USE_SWITCHBOARD else "registry"
        logger.info(f"✓ Discovery ({source_label}): {matched_ids}")
        return configs
    except:
        return []


# ============================================================================
# AGENT COMMUNICATION
# ============================================================================

async def call_agent_slim(slim_client, config: AgentConfig, msg: str) -> Dict:
    agent_map = {
        "mbta-alerts": "alerts",
        "mbta-planner": "planner",
        "mbta-route-planner": "planner",
        "mbta-stops": "stopfinder",
        "mbta-stopfinder": "stopfinder"
    }
    
    name = agent_map.get(config.name)
    if not name:
        raise ValueError(f"No SLIM mapping: {config.name}")
    
    return await slim_client.call_agent(name, msg)


async def call_agent_http(config: AgentConfig, msg: str, conv_id: str) -> Dict:
    url = f"{config.url}:{config.port}/a2a/message"
    payload = {
        "type": "request",
        "payload": {"message": msg, "conversation_id": conv_id},
        "metadata": {"source": "stategraph"}
    }
    
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        result = r.json()
        
        if result.get("type") == "response" and "payload" in result:
            return {"response": result["payload"].get("text", ""), "agent_used": config.name}
        return result


async def call_stopfinder_for_location(location: str, config: AgentConfig, conv_id: str) -> str:
    """Call StopFinder and extract station name"""
    if not location:
        return ""
    
    query = location
    
    global _current_orchestrator
    
    try:
        if _current_orchestrator and _current_orchestrator.use_slim and _current_orchestrator.slim_client:
            try:
                result = await call_agent_slim(_current_orchestrator.slim_client, config, query)
            except:
                result = await call_agent_http(config, query, conv_id)
        else:
            result = await call_agent_http(config, query, conv_id)
        
        response_text = result.get("response", "")
        station = extract_station_from_stopfinder(response_text)
        
        logger.info(f"   '{location}' → '{station}'")
        return station
        
    except Exception as e:
        logger.error(f"StopFinder error for '{location}': {e}")
        return ""


# ============================================================================
# NODES
# ============================================================================

async def discovery_node(state: AgentState) -> AgentState:
    """Semantic agent discovery"""
    with tracer.start_as_current_span("discovery"):
        matched = await semantic_discovery(state["user_message"])
        matched_ids = [a.name for a in matched]
        discovered_configs = [
            {"agent_id": c.name, "agent_url": f"{c.url}:{c.port}" if c.port not in (80, 443, None) else c.url,
             "description": c.description, "capabilities": c.capabilities}
            for c in matched
        ]

        # Infer intent
        intent = "general"
        conf = 0.5
        
        if matched_ids:
            first = matched[0]
            d = first.description.lower()
            
            if any(w in d for w in ["route", "plan", "trip"]):
                intent, conf = "trip_planning", 0.85
            elif any(w in d for w in ["alert", "delay"]):
                intent, conf = "alerts", 0.85
            elif any(w in d for w in ["stop", "station"]):
                intent, conf = "stop_info", 0.85
        
        # Parse origin/destination
        parsed = extract_origin_destination(state["user_message"])
        
        return {
            **state,
            "matched_agents": matched_ids,
            "discovered_agent_configs": discovered_configs,
            "intent": intent,
            "confidence": conf,
            "origin_text": parsed["origin"],
            "destination_text": parsed["destination"],
            "agent_queries": {},
            "agents_called": [],
            "agent_responses": [],
            "messages": [HumanMessage(content=state["user_message"])],
            "llm_matching_decision": {"matched_agents": matched_ids},
            "resolved_origin": "",
            "resolved_destination": "",
            "has_disruptions": False,
            "affected_routes": [],
            "severity_level": "none",
            "alerts_domain_analysis": {},  # NEW
            "routing_decision": ""
        }


def routing_node(state: AgentState) -> AgentState:
    """Intelligent routing decision"""
    intent = state["intent"]
    matched = state.get("matched_agents", [])
    
    logger.info(f"🎯 Routing: {intent}")
    
    # Check if 3 agents matched (means trip planning)
    if intent == "trip_planning" or len(matched) >= 3:
        # Order: stopfinder → alerts → planner
        ordered = []
        
        for a in matched:
            if "stopfinder" in a or "stops" in a:
                ordered.append(a)
                break
        
        if not any("alerts" in a for a in ordered):
            ordered.append("mbta-alerts")
        
        for a in matched:
            if "planner" in a or "route" in a:
                ordered.append(a)
                break
        
        state["matched_agents"] = list(dict.fromkeys(ordered))
        state["routing_decision"] = "FULL_CHAIN"
        
        logger.info(f"✓ Chain: {' → '.join(state['matched_agents'])}")
    
    return state


async def execute_agents_node(state: AgentState) -> AgentState:
    """
    OPTIMIZED v4.4: 
    - Only calls StopFinder for actual landmarks
    - Extracts domain analysis from Alerts Agent
    - Passes alerts context to Planner Agent
    """
    with tracer.start_as_current_span("execute_agents"):
        matched = state.get("matched_agents", [])
        if not matched:
            return {**state, "agents_called": [], "agent_responses": []}
        
        global _current_orchestrator
        catalog = state.get("discovered_agent_configs") or await get_agent_catalog()
        
        responses = []
        agents_called = []
        
        # Get parsed values
        origin = state.get("origin_text", "")
        destination = state.get("destination_text", "")
        
        logger.info(f"🔄 Execute: {' → '.join(matched)}")
        logger.info(f"   Origin: '{origin}', Dest: '{destination}'")
        
        # OPTIMIZATION: Check if they're already station names
        origin_is_station = is_likely_station_name(origin)
        dest_is_station = is_likely_station_name(destination)
        
        logger.info(f"   Origin is station: {origin_is_station}, Dest is station: {dest_is_station}")
        
        # STEP 1: Resolve with StopFinder (ONLY if needed)
        resolved_origin = ""
        resolved_destination = ""
        
        # Find StopFinder config
        stopfinder_id = None
        for aid in matched:
            if "stopfinder" in aid or "stops" in aid:
                stopfinder_id = aid
                break
        
        if stopfinder_id and (not origin_is_station or not dest_is_station):
            ainfo = next((a for a in catalog if a['agent_id'] == stopfinder_id), None)
            if ainfo:
                p = urlparse(ainfo['agent_url'])
                sf_config = AgentConfig(
                    name=ainfo['agent_id'],
                    url=f"{p.scheme or 'http'}://{p.hostname}",
                    port=p.port or (443 if p.scheme == 'https' else 80),
                    description=ainfo['description'],
                    capabilities=ainfo.get('capabilities', [])
                )
                
                logger.info(f"📍 Resolving landmarks with StopFinder...")
                
                if origin and not origin_is_station:
                    logger.info(f"   Resolving origin landmark: '{origin}'")
                    resolved_origin = await call_stopfinder_for_location(origin, sf_config, state["conversation_id"])
                elif origin:
                    logger.info(f"   Skipping origin (already station): '{origin}'")
                    resolved_origin = origin
                
                if destination and not dest_is_station:
                    logger.info(f"   Resolving destination landmark: '{destination}'")
                    resolved_destination = await call_stopfinder_for_location(destination, sf_config, state["conversation_id"])
                elif destination:
                    logger.info(f"   Skipping destination (already station): '{destination}'")
                    resolved_destination = destination
                
                final_origin = resolved_origin if resolved_origin else origin
                final_destination = resolved_destination if resolved_destination else destination
                
                logger.info(f"✓ Final stations: '{final_origin}' → '{final_destination}'")
                
                if (origin and not origin_is_station) or (destination and not dest_is_station):
                    agents_called.append(stopfinder_id)
        else:
            resolved_origin = origin
            resolved_destination = destination
            logger.info(f"✓ Both are stations, using as-is: '{origin}' → '{destination}'")
        
        # STEP 2: Execute remaining agents (Alerts and Planner)
        # NEW: Track full domain analysis from Alerts Agent
        has_disruptions = False
        affected = []
        severity = "none"
        alerts_analysis = {}
        
        for idx, agent_id in enumerate(matched):
            # Skip StopFinder (already handled)
            if agent_id == stopfinder_id:
                continue
            
            canonical_id = _normalize_agent_id({"agent_id": agent_id})
            ainfo = next((a for a in catalog if a['agent_id'] == agent_id or _normalize_agent_id(a) == canonical_id), None)
            if not ainfo:
                continue
            agent_id = ainfo['agent_id']  # use the full federation ID from catalog
            
            p = urlparse(ainfo['agent_url'])
            config = AgentConfig(
                name=ainfo['agent_id'],
                url=f"{p.scheme or 'http'}://{p.hostname}",
                port=p.port or (443 if p.scheme == 'https' else 80),
                description=ainfo['description'],
                capabilities=ainfo.get('capabilities', [])
            )
            
            # Construct query
            if canonical_id == "mbta-alerts":
                agent_query = state["user_message"]  # Pass full query for context
                logger.info(f"⚠️ Alerts Agent")

            elif canonical_id in ["mbta-planner", "mbta-route-planner"]:
                # Planner: Use RESOLVED station names + alerts context
                from_station = resolved_origin if resolved_origin else origin
                to_station = resolved_destination if resolved_destination else destination
                
                # Check if user EXPLICITLY wants multiple route options
                user_msg_lower = state["user_message"].lower()
                wants_multiple = any(word in user_msg_lower for word in [
                    "two route", "multiple route", "give me two", "give me multiple",
                    "show me options", "give me options", "different routes",
                    "compare routes"
                ])
                # NOTE: Removed "alternative", "route option", "considering" 
                # These don't mean user wants multiple options shown
                
                if from_station and to_station:
                    if wants_multiple:
                        agent_query = f"""IMPORTANT: Plan route using these EXACT station names.

Origin: {from_station}
Destination: {to_station}

Provide TWO or MORE different route options. For each:
- Transfer stations
- Lines used
- Estimated time
- Number of stops

Rank by reliability if disruptions exist, or by speed if no disruptions."""
                    else:
                        agent_query = f"""IMPORTANT: Plan route using these EXACT station names.

Origin: {from_station}
Destination: {to_station}

Plan the route between these stations."""
                elif to_station:
                    agent_query = f"Find routes to {to_station}"
                else:
                    agent_query = state["user_message"]
                
                # NEW: Add alerts domain analysis context if available
                if alerts_analysis:
                    logger.info(f"🧠 Passing alerts analysis to Planner:")
                    logger.info(f"   Recommendation: {alerts_analysis.get('overall_recommendation')}")
                    logger.info(f"   Severity: {alerts_analysis.get('severity')}")
                    logger.info(f"   Avoid routes: {alerts_analysis.get('should_avoid_routes')}")
                    
                    agent_query += f"\n\nALERTS ANALYSIS CONTEXT:"
                    agent_query += f"\n- Overall recommendation: {alerts_analysis.get('overall_recommendation', 'unknown')}"
                    agent_query += f"\n- Severity: {alerts_analysis.get('severity', 'unknown')}"
                    
                    if alerts_analysis.get('should_avoid_routes'):
                        agent_query += f"\n- AVOID these routes: {', '.join(alerts_analysis['should_avoid_routes'])}"
                        agent_query += f"\n- Find alternative routes that don't use these lines"
                    
                    if alerts_analysis.get('overall_recommendation') == 'take_alternative':
                        agent_query += f"\n- CRITICAL: Major disruptions detected, prioritize alternative routes"
                
                logger.info(f"🗺️ Planner with context")
            
            else:
                agent_query = state["user_message"]
            
            logger.info(f"📞 [{idx+1}] {agent_id}")
            
            with tracer.start_as_current_span(f"agent_{agent_id}"):
                try:
                    if _current_orchestrator and _current_orchestrator.use_slim and _current_orchestrator.slim_client:
                        try:
                            result = await call_agent_slim(_current_orchestrator.slim_client, config, agent_query)
                        except:
                            result = await call_agent_http(config, agent_query, state["conversation_id"])
                    else:
                        result = await call_agent_http(config, agent_query, state["conversation_id"])
                    # Normalize agent id on every response for stable synthesis mapping.
                    result["agent_used"] = agent_id
                    
                    resp_text = result.get("response", "")
                    
                    # NEW: Extract domain analysis from Alerts Agent
                    if canonical_id == "mbta-alerts":
                        # Extract domain expertise insights
                        alerts_analysis = extract_alerts_domain_analysis(resp_text)
                        
                        # Also extract simple disruption markers
                        if "🔴" in resp_text or "🟠" in resp_text:
                            has_disruptions = True
                            
                            for route in ["Red Line", "Orange Line", "Blue Line", "Green Line"]:
                                if route in resp_text:
                                    affected.append(route)
                            
                            if "🔴" in resp_text:
                                severity = "critical"
                            elif "🟠" in resp_text:
                                severity = "major"
                            
                            logger.info(f"⚠️ Disruptions: {affected}, severity={severity}")
                        else:
                            logger.info(f"✓ No major disruptions")
                    
                    responses.append(result)
                    agents_called.append(agent_id)
                    
                except Exception as e:
                    logger.error(f"❌ {agent_id}: {e}")
                    responses.append({"response": f"Error: {e}", "error": True})
                    agents_called.append(f"{agent_id}_error")
        
        return {
            **state,
            "agent_responses": responses,
            "agents_called": agents_called,
            "resolved_origin": resolved_origin,
            "resolved_destination": resolved_destination,
            "has_disruptions": has_disruptions,
            "affected_routes": affected,
            "severity_level": severity,
            "alerts_domain_analysis": alerts_analysis,  # NEW: Pass analysis forward
            "messages": [
                AIMessage(content=f"{r.get('agent_used', 'agent')}: {r.get('response', '')[:100]}", 
                         name=r.get('agent_used', 'agent'))
                for r in responses if not r.get('error')
            ]
        }


async def synthesize_node(state: AgentState) -> AgentState:
    """
    FIXED synthesis v4.5 - Prevents over-synthesizing
    
    RULES:
    1. Single agent → Return directly (NO synthesis!)
    2. Alerts + Planner (no issues) → Return planner only
    3. Alerts + Planner (has issues) → Simple combination
    4. Full chain → Minimal synthesis or direct return
    """
    with tracer.start_as_current_span("synthesize"):
        # Handle no agents
        if not state.get("matched_agents"):
            msg = state["user_message"].lower()
            if any(w in msg for w in ["hi", "hello", "hey"]):
                return {**state, "final_response": "Hello! I help with MBTA routes, alerts, and stops.", "should_end": True}
            return {**state, "final_response": "I specialize in MBTA transit.", "should_end": True}
        
        agent_responses = [r for r in state.get("agent_responses", []) if not r.get("error") and r.get("response")]
        responses = [r.get("response", "") for r in agent_responses]
        response_by_agent = {
            str(r.get("agent_used", "")).lower(): r.get("response", "")
            for r in agent_responses
        }
        
        if not responses:
            return {**state, "final_response": "Agents unavailable.", "should_end": True}
        
        agents_called_list = state.get("agents_called", [])
        
        # ================================================================
        # RULE 1: Single agent → Direct return
        # ================================================================
        if len(agents_called_list) == 1:
            response = responses[0]
            if isinstance(response, dict):
                response = response.get("response", str(response))
            
            logger.info(f"📍 Single agent - direct return (no synthesis)")
            return {**state, "final_response": response, "should_end": True}
        
        # ================================================================
        # RULE 2: Alerts + Planner → Smart combination
        # ================================================================
        if len(agents_called_list) == 2:
            has_alerts = any("alert" in a.lower() for a in agents_called_list)
            has_planner = any("planner" in a.lower() for a in agents_called_list)
            
            if has_alerts and has_planner:
                alerts_resp = next((txt for aid, txt in response_by_agent.items() if "alert" in aid), "")
                planner_resp = next((txt for aid, txt in response_by_agent.items() if "planner" in aid), "")
                
                # Check if no real issues
                alerts_lower = alerts_resp.lower() if alerts_resp else ""
                no_issues = any(p in alerts_lower for p in ["no current", "no major", "✅"]) or len(alerts_resp) < 100
                
                if no_issues:
                    logger.info("✓ No issues - planner only")
                    return {**state, "final_response": planner_resp, "should_end": True}
                else:
                    # Has issues - LLM synthesis for coherent response
                    try:
                        import urllib.request as _urllib
                        import asyncio as _asyncio
                        synthesis_prompt = f"""You are an MBTA transit assistant. Synthesize the alerts and route information into a clear, concise response.

User query: {state['user_message']}

Alerts:
{alerts_resp}

Route:
{planner_resp}

Instructions:
- Lead with the route recommendation
- Include alerts directly on the trip route
- Include any service disruptions (shuttle buses, line suspensions) on nearby or connecting lines that could affect alternatives
- Suggest alternative routes if a connecting line has major disruptions
- Include arrival times if provided
- Keep it to 4-6 sentences"""
                        _payload = json.dumps({
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": synthesis_prompt}],
                            "temperature": 0.3,
                            "max_tokens": 300,
                        }).encode()
                        _req = _urllib.Request(
                            "https://api.openai.com/v1/chat/completions",
                            data=_payload,
                            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                            method="POST"
                        )
                        _resp = await _asyncio.to_thread(lambda: json.loads(_urllib.urlopen(_req, timeout=10).read()))
                        combined = _resp["choices"][0]["message"]["content"]
                        logger.info("✓ LLM synthesis of alerts + planner")
                    except Exception as e:
                        logger.warning(f"LLM synthesis failed, falling back: {e}")
                        combined = f"{alerts_resp}\n\n{planner_resp}"
                    return {**state, "final_response": combined, "should_end": True}
        
        # ================================================================
        # RULE 3: Full chain → Preserve details
        # ================================================================
        routing = state.get("routing_decision", "")
        
        if routing == "FULL_CHAIN":
            planner_resp = next((txt for aid, txt in response_by_agent.items() if "planner" in aid), "")
            
            # Check if multiple routes requested
            user_msg = state.get("user_message", "").lower()
            wants_multiple = "two route" in user_msg or "give me multiple" in user_msg
            
            if wants_multiple:
                logger.info("📊 Multiple routes - direct return")
                return {**state, "final_response": planner_resp, "should_end": True}
            
            # Single route - add minimal landmark note
            origin = state.get("origin_text", "")
            dest = state.get("destination_text", "")
            resolved_dest = state.get("resolved_destination", "")
            
            if resolved_dest and resolved_dest != dest:
                final = f"{dest.title()} is at {resolved_dest}.\n\n{planner_resp}"
            else:
                final = planner_resp
            
            logger.info("✓ Minimal combination")
            return {**state, "final_response": final, "should_end": True}
        
        # ================================================================
        # FALLBACK
        # ================================================================
        logger.info("✓ Direct return")
        return {**state, "final_response": "\n\n".join(responses), "should_end": True}


# ============================================================================
# ROUTING
# ============================================================================

def route_after_discovery(state: AgentState) -> Literal["routing", "synthesize"]:
    return "routing" if state.get("matched_agents") else "synthesize"


def route_after_routing(state: AgentState) -> Literal["execute", "synthesize"]:
    return "execute" if state.get("matched_agents") else "synthesize"


def route_after_execute(state: AgentState) -> Literal["synthesize"]:
    return "synthesize"


# ============================================================================
# GRAPH
# ============================================================================

def build_graph() -> StateGraph:
    wf = StateGraph(AgentState)
    
    wf.add_node("discovery", discovery_node)
    wf.add_node("routing", routing_node)
    wf.add_node("execute", execute_agents_node)
    wf.add_node("synthesize", synthesize_node)
    
    wf.set_entry_point("discovery")
    
    wf.add_conditional_edges("discovery", route_after_discovery,
                            {"routing": "routing", "synthesize": "synthesize"})
    wf.add_conditional_edges("routing", route_after_routing,
                            {"execute": "execute", "synthesize": "synthesize"})
    wf.add_conditional_edges("execute", route_after_execute,
                            {"synthesize": "synthesize"})
    wf.add_edge("synthesize", END)
    
    return wf.compile()


# ============================================================================
# ORCHESTRATOR
# ============================================================================

class StateGraphOrchestrator:
    """
    Production Orchestrator v4.5 - Switchboard Federation
    - Switchboard-backed federated discovery across registries
    - Capability-based candidate narrowing before LLM selection
    - Skips StopFinder for known stations
    - Extracts domain analysis from Alerts Agent
    - Passes alerts context to Planner Agent
    """
    
    def __init__(self):
        global _current_orchestrator
        
        logger.info("=" * 80)
        logger.info("🚀 StateGraph Orchestrator v4.5 - Switchboard Federation")
        logger.info("   ✅ Smart StopFinder skipping")
        logger.info("   ✅ Domain analysis extraction")
        logger.info("   ✅ Context passing between agents")
        if USE_SWITCHBOARD:
            logger.info("   ✅ Switchboard federated discovery")
        else:
            logger.info("   ⚠️  Legacy single-registry discovery")
        logger.info("   ✅ SLIM transport")
        logger.info("=" * 80)
        
        self.graph = build_graph()
        self.use_slim = os.getenv("USE_SLIM", "false").lower() == "true"
        self.slim_client = None
        
        if self.use_slim and SLIM_AVAILABLE:
            try:
                self.slim_client = SlimAgentClient()
                logger.info("✅ SLIM enabled")
            except Exception as e:
                logger.warning(f"SLIM failed: {e}")
                self.use_slim = False
        
        _current_orchestrator = self
        logger.info("✅ Orchestrator v4.5 ready")
    
    async def startup_validation(self):
        """Validate registry and switchboard connectivity"""
        logger.info("🔍 Validating...")
        
        if not await validate_registry():
            raise RuntimeError("Registry not accessible")
        
        if USE_SWITCHBOARD:
            try:
                candidates = await get_switchboard_candidates()
                logger.info(f"📚 Switchboard: {len(candidates)} candidates across federated registries")
            except Exception as e:
                logger.warning(f"⚠️ Switchboard validation failed: {e}, will retry at query time")
        else:
            agents = await get_agent_catalog()
            logger.info(f"📚 {len(agents)} agents (legacy catalog)")
        
        if self.use_slim and self.slim_client:
            try:
                await self.slim_client.initialize()
                logger.info("✅ SLIM ready")
            except:
                self.use_slim = False
        
        logger.info("✅ Startup complete")
    
    async def process_message(self, user_message: str, conversation_id: str) -> Dict[str, Any]:
        """Process message through domain expertise coordination"""
        with tracer.start_as_current_span("stategraph_v44"):
            initial = {
                "user_message": user_message,
                "conversation_id": conversation_id,
                "intent": "",
                "confidence": 0.0,
                "matched_agents": [],
                "agent_queries": {},
                "llm_matching_decision": {},
                "messages": [],
                "agents_called": [],
                "agent_responses": [],
                "origin_text": "",
                "destination_text": "",
                "resolved_origin": "",
                "resolved_destination": "",
                "has_disruptions": False,
                "affected_routes": [],
                "severity_level": "none",
                "alerts_domain_analysis": {},  # NEW
                "final_response": "",
                "should_end": False,
                "routing_decision": ""
            }
            
            final = await self.graph.ainvoke(initial)
            
            return {
                "response": final["final_response"],
                "intent": final["intent"],
                "confidence": final["confidence"],
                "matched_agents": final.get("matched_agents", []),
                "agents_called": final["agents_called"],
                "metadata": {
                    "conversation_id": conversation_id,
                    "discovery": "semantic",
                    "transport": "slim" if self.use_slim else "http",
                    "optimization": {
                        "stopfinder_skipped": not final.get("resolved_origin") and not final.get("resolved_destination"),
                    },
                    "domain_expertise": {
                        "alerts_analysis_available": bool(final.get("alerts_domain_analysis")),
                        "context_passed_to_planner": bool(final.get("alerts_domain_analysis"))
                    },
                    "context": {
                        "origin": final.get("origin_text", ""),
                        "destination": final.get("destination_text", ""),
                        "resolved_origin": final.get("resolved_origin", ""),
                        "resolved_dest": final.get("resolved_destination", ""),
                        "disruptions": final.get("has_disruptions", False),
                        "affected": final.get("affected_routes", []),
                        "severity": final.get("severity_level", "none")
                    },
                    "registry_url": REGISTRY_URL
                }
            }
