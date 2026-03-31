"""
MBTA Route Planner Agent v5.1 - COMPLETE MERGED VERSION
Combines: Domain context awareness + Crowding estimation + All existing features
Version: 5.1 FINAL
"""

import asyncio
import logging
import os
import sys
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from uuid import uuid4

sys.path.insert(0, '/opt/mbta-agents')

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message, TextPart

from dotenv import load_dotenv
import uvicorn
from openai import OpenAI
import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY')
MBTA_BASE_URL = "https://api-v3.mbta.com"
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _is_valid_api_key(value: Optional[str]) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return normalized not in {"", "your api key", "your_api_key", "changeme", "replace-me"}

if not _is_valid_api_key(MBTA_API_KEY):
    logger.warning("MBTA_API_KEY not found!")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not found - LLM disabled!")


class PlannerExecutor(AgentExecutor):
    """
    COMPLETE Planner v5.1 with ALL features:
    
    From v5.0 (Domain Context):
    - Extracts alerts context from StateGraph messages
    - Avoids disrupted routes automatically
    - Checks MBTA API for direct routes FIRST
    - LLM fallback for transfers
    
    NEW in v5.1 (Crowding):
    - Real-time crowding estimation
    - Crowding-aware route ranking
    - Detects user crowding preferences
    - Includes crowding in multi-route comparisons
    """
    
    # Occupancy scoring
    OCCUPANCY_SCORES = {
        "EMPTY": 0,
        "MANY_SEATS_AVAILABLE": 20,
        "FEW_SEATS_AVAILABLE": 50,
        "STANDING_ROOM_ONLY": 75,
        "CRUSHED_STANDING_ROOM_ONLY": 90,
        "FULL": 100,
        "NOT_ACCEPTING_PASSENGERS": 100,
        None: 50
    }
    
    def __init__(self, openai_api_key: str, mbta_api_key: str):
        self.openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None
        self.mbta_api_key = mbta_api_key
        self.session = None
        logger.info("✅ Planner v5.1 - Complete (Context + Crowding)")
    
    # ========================================================================
    # CONTEXT EXTRACTION (From v5.0)
    # ========================================================================
    
    def extract_alerts_context(self, message: str) -> Dict[str, Any]:
        """
        Extract alerts context from StateGraph message.
        
        StateGraph passes:
        "ALERTS ANALYSIS CONTEXT:
         - Overall recommendation: take_alternative
         - Severity: major
         - AVOID these routes: Red Line"
        """
        context = {
            "has_context": False,
            "recommendation": "unknown",
            "severity": "unknown",
            "avoid_routes": []
        }
        
        if "ALERTS ANALYSIS CONTEXT" not in message and "ALERTS CONTEXT" not in message:
            return context
        
        context["has_context"] = True
        logger.info("🧠 Detected alerts context")
        
        # Extract recommendation
        rec_match = re.search(r"recommendation:\s*(\w+)", message, re.IGNORECASE)
        if rec_match:
            context["recommendation"] = rec_match.group(1).lower()
        
        # Extract severity
        sev_match = re.search(r"severity:\s*(\w+)", message, re.IGNORECASE)
        if sev_match:
            context["severity"] = sev_match.group(1).lower()
        
        # Extract avoid routes
        avoid_match = re.search(r"AVOID.*?:\s*([^\n]+)", message, re.IGNORECASE)
        if avoid_match:
            avoid_text = avoid_match.group(1)
            for route in ["Red Line", "Orange Line", "Blue Line", "Green Line"]:
                if route in avoid_text:
                    context["avoid_routes"].append(route.split()[0])
        
        logger.info(f"  Context: {context['recommendation']}, avoid={context['avoid_routes']}")
        
        return context
    
    def generate_context_explanation(self, context: Dict[str, Any]) -> str:
        """Explain routing decision based on context"""
        if not context.get("has_context"):
            return ""
        
        explanation = "🧠 **Routing Based on Current Conditions:**\n"
        
        recommendation = context.get("recommendation", "unknown")
        severity = context.get("severity", "unknown")
        avoid = context.get("avoid_routes", [])
        
        if recommendation == "take_alternative":
            explanation += f"⚠️ Major disruptions ({severity}) on {', '.join(avoid)}.\n"
            explanation += "Alternative routes provided.\n"
        elif recommendation == "monitor":
            explanation += f"ℹ️ Some disruptions ({severity}). Routes optimized.\n"
        
        return explanation
    
    # ========================================================================
    # CROWDING FEATURES (NEW in v5.1)
    # ========================================================================
    
    def wants_crowding_info(self, query: str) -> bool:
        """Detect if user cares about crowding"""
        q = query.lower()
        crowding_keywords = [
            "not crowded", "avoid crowd", "least busy", "most comfortable",
            "seat", "space", "not packed", "less crowded", "least crowded"
        ]
        detected = any(kw in q for kw in crowding_keywords)
        if detected:
            logger.info("✓ User wants crowding info")
        return detected
    
    async def get_route_crowding(self, route_id: str) -> Dict[str, Any]:
        """
        Get real-time crowding for a route.
        
        NEW in v5.1: Fetches vehicle occupancy from MBTA API.
        """
        try:
            params = {
                "api_key": self.mbta_api_key,
                "filter[route]": route_id
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{MBTA_BASE_URL}/vehicles",
                    params=params,
                    timeout=10
                )
                response.raise_for_status()
            
            vehicles = response.json().get('data', [])
            
            if not vehicles:
                return {"level": "unknown", "score": 50}
            
            # Calculate average occupancy
            scores = []
            for v in vehicles:
                status = v.get('attributes', {}).get('occupancy_status')
                score = self.OCCUPANCY_SCORES.get(status, 50)
                scores.append(score)
            
            avg_score = sum(scores) / len(scores)
            
            # Classify
            if avg_score < 30:
                level = "low"
            elif avg_score < 60:
                level = "moderate"
            else:
                level = "high"
            
            logger.info(f"  {route_id}: {level} ({avg_score:.0f}%)")
            
            return {
                "level": level,
                "score": round(avg_score, 1),
                "vehicles_analyzed": len(scores)
            }
            
        except Exception as e:
            logger.error(f"Crowding error for {route_id}: {e}")
            return {"level": "unknown", "score": 50}
    
    # ========================================================================
    # ROUTE GENERATION (Enhanced with crowding)
    # ========================================================================
    
    def detect_multiple_routes_request(self, query: str) -> bool:
        """Detect if user wants multiple options"""
        q = query.lower()
        keywords = [
            "two route", "multiple route", "give me two", "give me multiple",
            "show me options", "compare routes", "route options"
        ]
        detected = any(kw in q for kw in keywords)
        if detected:
            logger.info("✓ Multiple routes requested")
        return detected
    
    async def generate_multiple_routes_with_llm(
        self,
        origin: str,
        destination: str,
        avoid_routes: List[str] = None,
        crowding_data: Dict[str, Dict] = None,
        num_options: int = 2
    ) -> str:
        """
        Generate multiple routes with LLM.
        
        MERGED: Includes both avoid_routes (from alerts) AND crowding_data.
        """
        if not self.openai_client:
            return "LLM not available."
        
        try:
            # Build avoid instruction
            avoid_instruction = ""
            if avoid_routes:
                avoid_instruction = f"\nIMPORTANT: AVOID {', '.join(avoid_routes)} due to service disruptions."
                avoid_instruction += "\nProvide alternative routes that don't use these lines."
            
            # Build crowding context
            crowding_context = ""
            if crowding_data:
                crowding_context = "\nCurrent crowding levels:\n"
                for line, data in crowding_data.items():
                    crowding_context += f"- {line} Line: {data['level']} ({data['score']}% occupancy)\n"
                crowding_context += "\nConsider both time AND comfort when ranking routes."
            
            prompt = f"""Generate {num_options} DIFFERENT route options from {origin} to {destination}.
{avoid_instruction}
{crowding_context}

MBTA System:
- Red Line: Alewife ↔ Ashmont/Braintree (via Park St, South Station)
- Orange Line: Oak Grove ↔ Forest Hills (via North Station, Downtown, Back Bay)
- Blue Line: Wonderland ↔ Bowdoin (via Airport, Aquarium, Government Center)
- Green Line: B,C,D,E branches (all through Park Street, Kenmore)

Transfer Stations:
- Park Street: Red + Green
- Downtown Crossing: Red + Orange
- Government Center: Blue + Green
- North Station: Orange + Green

For EACH route:
- Option number
- Lines used
- Transfers
- Stops + Time
{"- Crowding level (if data provided)" if crowding_data else ""}

Make them ACTUALLY DIFFERENT (different lines or transfers).

Format:

**Option 1:**
- Lines: Red + Green
- Transfers: at Park Street
- Stops: 5, Time: ~15 min
{"- Crowding: Moderate (Red 45%, Green 35%)" if crowding_data else ""}
- From {origin}, Red to Park, Green to {destination}

**Option 2:**
- Lines: Orange + Green
- Transfers: at Downtown
- Stops: 6, Time: ~18 min  
{"- Crowding: Low (Orange 25%, Green 35%)" if crowding_data else ""}
- From {origin}, Orange to Downtown, Green to {destination}

{"Recommend the less crowded route if significant difference." if crowding_data else ""}"""
            
            logger.info(f"🤖 Generating routes (avoid={avoid_routes}, crowding={bool(crowding_data)})")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=700
            )
            
            routes_text = response.choices[0].message.content
            
            # Add notes
            if avoid_routes:
                routes_text += f"\n\n💡 Alternative routes provided to avoid disruptions on {', '.join(avoid_routes)}."
            
            logger.info("✅ Generated routes")
            return routes_text
            
        except Exception as e:
            logger.error(f"Error: {e}")
            return f"Error generating routes."
    
    async def generate_single_route_with_llm(
        self,
        origin: str,
        destination: str,
        avoid_routes: List[str] = None
    ) -> str:
        """Generate single route (from v5.0)"""
        if not self.openai_client:
            return f"Route from {origin} to {destination}"
        
        try:
            avoid_instruction = ""
            if avoid_routes:
                avoid_instruction = f"\nIMPORTANT: Avoid {', '.join(avoid_routes)} due to disruptions."
            
            prompt = f"""Boston MBTA route from {origin} to {destination}.
{avoid_instruction}

Include: lines, transfers, stops, time.
One natural sentence.

Example: "From Park Street, take Red towards Alewife to Harvard (3 stops, ~8 min)."
"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            
            route_text = response.choices[0].message.content.strip()
            
            if avoid_routes:
                route_text += f" (Avoiding {', '.join(avoid_routes)})"
            
            return route_text
            
        except Exception as e:
            logger.error(f"Error: {e}")
            return f"Route from {origin} to {destination}"
    
    # ========================================================================
    # LOCATION EXTRACTION (From v5.0)
    # ========================================================================
    
    async def extract_locations_with_llm(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract origin and destination using LLM"""
        if not self.openai_client:
            return await self.extract_locations_basic(query)
        
        prompt = f"""Extract origin and destination from: "{query}"

Return ONLY: origin|destination

If only destination: none|destination

Examples:
- "from park street to harvard" → park street|harvard
- "to harvard" → none|harvard

Response:"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=50
            )
            
            result = response.choices[0].message.content.strip()
            
            if "|" in result:
                parts = result.split("|")
                origin = parts[0].strip() if parts[0].strip().lower() != "none" else None
                destination = parts[1].strip() if len(parts) > 1 and parts[1].strip().lower() != "none" else None
                
                logger.info(f"✓ LLM extracted: '{origin}' → '{destination}'")
                return origin, destination
            
            return await self.extract_locations_basic(query)
            
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            return await self.extract_locations_basic(query)
    
    async def extract_locations_basic(self, query: str) -> Tuple[Optional[str], Optional[str]]:
        """Basic parsing fallback"""
        query_lower = query.lower()
        origin = None
        destination = None
        
        if " from " in query_lower and " to " in query_lower:
            parts = query_lower.split(" from ")
            if len(parts) > 1:
                from_part = parts[1]
                to_parts = from_part.split(" to ")
                if len(to_parts) >= 2:
                    origin = to_parts[0].strip()
                    destination = to_parts[1].strip()
        
        elif " to " in query_lower:
            parts = query_lower.split(" to ")
            if len(parts) >= 2:
                origin_part = parts[0].strip()
                destination = parts[1].strip()
                
                for word in ["how", "do", "i", "get", "go", "wanna", "want"]:
                    origin_part = origin_part.replace(f" {word} ", " ").strip()
                
                origin = origin_part if origin_part else None
        
        if origin:
            origin = origin.strip("?.,!").strip()
        if destination:
            destination = destination.strip("?.,!").strip()
        
        logger.info(f"✓ Basic parsing: '{origin}' → '{destination}'")
        return origin, destination
    
    # ========================================================================
    # MBTA API INTEGRATION (From v5.0)
    # ========================================================================
    
    async def find_stop_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find stop by name with fuzzy matching - FIXED: strips punctuation"""
        if not name:
            return None
        
        try:
            params = {
                "api_key": self.mbta_api_key,
                "page[limit]": 500,
                "filter[location_type]": "1"
            }
            
            # CRITICAL FIX: Strip punctuation before searching
            name_clean = name.strip("?.,!;:\"'").lower().strip()
            
            logger.info(f"Searching stop: '{name}' (cleaned: '{name_clean}')")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/stops", params=params, timeout=10)
                response.raise_for_status()
            
            stops = response.json().get("data", [])
            
            for stop in stops:
                stop_name = stop.get("attributes", {}).get("name", "").lower()
                if name_clean in stop_name or stop_name in name_clean:
                    attributes = stop.get("attributes", {})
                    result = {
                        "id": stop.get("id"),
                        "name": attributes.get("name"),
                        "latitude": attributes.get("latitude"),
                        "longitude": attributes.get("longitude"),
                        "wheelchair_boarding": attributes.get("wheelchair_boarding")
                    }
                    logger.info(f"Found: {result['name']}")
                    return result
            
            logger.warning(f"No stop matching '{name_clean}'")
            return None
        
        except Exception as e:
            logger.error(f"Error finding stop: {e}")
            return None
    
    async def get_routes_between_stops(self, origin_id: str, destination_id: str) -> List[Dict[str, Any]]:
        """
        Find routes serving both stops.
        
        CRITICAL: Always check API first before hallucinating!
        """
        if not origin_id or not destination_id:
            return []
        
        try:
            # Get routes at origin
            params = {"api_key": self.mbta_api_key, "filter[stop]": origin_id}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/routes", params=params, timeout=10)
                response.raise_for_status()
            
            origin_routes = response.json().get("data", [])
            origin_route_ids = {r.get("id") for r in origin_routes}
            
            # Get routes at destination
            params["filter[stop]"] = destination_id
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/routes", params=params, timeout=10)
                response.raise_for_status()
            
            dest_routes = response.json().get("data", [])
            dest_route_ids = {r.get("id") for r in dest_routes}
            
            # Find common routes
            common_route_ids = origin_route_ids.intersection(dest_route_ids)
            
            common_routes = []
            for route in origin_routes:
                if route.get("id") in common_route_ids:
                    attributes = route.get("attributes", {})
                    common_routes.append({
                        "id": route.get("id"),
                        "short_name": attributes.get("short_name", ""),
                        "long_name": attributes.get("long_name", "Unknown"),
                        "type": attributes.get("type"),
                        "color": attributes.get("color"),
                    })
            
            logger.info(f"Found {len(common_routes)} routes between stops")
            return common_routes
        
        except Exception as e:
            logger.error(f"Error finding routes: {e}")
            return []
    
    async def get_predictions(self, stop_id: str, route_id: Optional[str] = None) -> List[Dict]:
        """Get real-time predictions"""
        if not stop_id:
            return []
        
        try:
            params = {
                "api_key": self.mbta_api_key,
                "filter[stop]": stop_id,
                "page[limit]": 5
            }
            
            if route_id:
                params["filter[route]"] = route_id
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/predictions", params=params, timeout=10)
                response.raise_for_status()
            
            predictions = response.json().get("data", [])
            
            return [
                {
                    "arrival_time": p.get("attributes", {}).get("arrival_time"),
                    "departure_time": p.get("attributes", {}).get("departure_time"),
                }
                for p in predictions
            ]
        
        except Exception as e:
            logger.error(f"Error getting predictions: {e}")
            return []
    
    # ========================================================================
    # MAIN ROUTE PLANNING (COMPLETE with context + crowding)
    # ========================================================================
    
    async def plan_route_complete(
        self,
        origin: str,
        destination: str,
        alerts_context: Dict[str, Any],
        wants_multiple: bool = False,
        check_crowding: bool = False
    ) -> Dict[str, Any]:
        """
        COMPLETE route planning with all intelligence.
        
        Steps:
        1. Find stops
        2. Check MBTA API for direct routes FIRST
        3. If crowding requested, check crowding for relevant lines
        4. Generate routes (single or multiple)
        5. Consider alerts context (avoid disrupted routes)
        6. Add explanations based on context
        """
        try:
            logger.info(f"Planning: '{origin}' → '{destination}'")
            logger.info(f"  Multiple: {wants_multiple}, Crowding: {check_crowding}")
            
            # Step 1: Find stops
            origin_stop = await self.find_stop_by_name(origin)
            if not origin_stop:
                return {"ok": False, "text": f"Couldn't find '{origin}'."}
            
            dest_stop = await self.find_stop_by_name(destination)
            if not dest_stop:
                return {"ok": False, "text": f"Couldn't find '{destination}'."}
            
            logger.info(f"Stops: {origin_stop['name']} → {dest_stop['name']}")
            
            # Get context
            avoid_routes = alerts_context.get("avoid_routes", []) if alerts_context.get("has_context") else []
            
            # Step 2: Check MBTA API for direct routes FIRST (don't hallucinate!)
            logger.info("🔍 Checking API for direct routes...")
            api_routes = await self.get_routes_between_stops(origin_stop["id"], dest_stop["id"])
            
            # Step 3: Check crowding if requested
            crowding_data = None
            if check_crowding:
                logger.info("📊 Checking crowding levels...")
                crowding_data = {}
                
                # Check common lines
                for line in ["Red", "Orange", "Green-B"]:
                    crowding = await self.get_route_crowding(line)
                    crowding_data[line] = crowding
            
            # Step 4: Generate routes
            if api_routes:
                # Direct route exists
                route = api_routes[0]
                route_id = route["id"]
                route_name = route_id.split("-")[0] if "-" in route_id else route_id
                
                should_avoid = avoid_routes and route_name in avoid_routes
                
                if should_avoid:
                    logger.info(f"⚠️ Direct route {route_name} should be avoided")
                    
                    routes_text = await self.generate_multiple_routes_with_llm(
                        origin_stop['name'],
                        dest_stop['name'],
                        avoid_routes=avoid_routes,
                        crowding_data=crowding_data,
                        num_options=2
                    )
                    
                    if alerts_context.get("has_context"):
                        explanation = self.generate_context_explanation(alerts_context)
                        routes_text = explanation + "\n\n" + routes_text
                    
                    return {"ok": True, "text": routes_text, "avoided_direct": True}
                
                else:
                    # Direct route is good
                    if wants_multiple:
                        # User wants options
                        routes_text = await self.generate_multiple_routes_with_llm(
                            origin_stop['name'],
                            dest_stop['name'],
                            avoid_routes=None,
                            crowding_data=crowding_data,
                            num_options=2
                        )
                        return {"ok": True, "text": routes_text}
                    else:
                        # Single route
                        text = f"Take the {route['long_name']} from {origin_stop['name']} to {dest_stop['name']}."
                        
                        # Add crowding if checked
                        if check_crowding and route_name in crowding_data:
                            crowding = crowding_data[route_name]
                            text += f"\n\n🚇 {route_name} Line crowding: {crowding['level'].upper()} ({crowding['score']}% occupancy)"
                        
                        # Add predictions
                        predictions = await self.get_predictions(origin_stop["id"], route.get("id"))
                        if predictions:
                            text += "\n\n⏰ Next arrivals:\n"
                            for i, pred in enumerate(predictions[:3], 1):
                                arrival = pred.get("arrival_time") or pred.get("departure_time")
                                if arrival:
                                    text += f"  {i}. {arrival}\n"
                        
                        # Add context note
                        if alerts_context.get("has_context"):
                            rec = alerts_context.get("recommendation", "").replace('_', ' ').title()
                            text += f"\n\nℹ️ Current service status: {rec}"
                        
                        return {"ok": True, "text": text}
            
            else:
                # No direct route - use LLM
                logger.info("❌ No direct route via API - using LLM")
                
                if wants_multiple:
                    routes_text = await self.generate_multiple_routes_with_llm(
                        origin_stop['name'],
                        dest_stop['name'],
                        avoid_routes=avoid_routes,
                        crowding_data=crowding_data,
                        num_options=2
                    )
                else:
                    routes_text = await self.generate_single_route_with_llm(
                        origin_stop['name'],
                        dest_stop['name'],
                        avoid_routes=avoid_routes
                    )
                
                if alerts_context.get("has_context"):
                    explanation = self.generate_context_explanation(alerts_context)
                    routes_text = explanation + "\n\n" + routes_text
                
                return {"ok": True, "text": routes_text}
            
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            return {"ok": False, "text": "Error planning route."}
    
    # ========================================================================
    # MAIN EXECUTION
    # ========================================================================
    
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        """Execute with full intelligence"""
        try:
            # Extract message
            message_text = ""
            for part in context.message.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    message_text = part.root.text
                    break
                elif hasattr(part, 'text'):
                    message_text = part.text
                    break
            
            logger.info(f"📨 Planner: '{message_text[:150]}'")
            
            # Extract alerts context
            alerts_context = self.extract_alerts_context(message_text)
            
            # Detect user preferences
            wants_multiple = self.detect_multiple_routes_request(message_text)
            check_crowding = self.wants_crowding_info(message_text)
            
            # ================================================================
            # CRITICAL FIX: Check if StateGraph already provided stations
            # ================================================================
            origin = None
            destination = None
            
            if "IMPORTANT: Plan route using these EXACT station names" in message_text:
                # StateGraph provided the stations - extract from IMPORTANT format
                logger.info("✓ Extracting from StateGraph IMPORTANT format")
                
                origin_match = re.search(r"Origin:\s*(.+?)(?:\n|$)", message_text, re.IGNORECASE)
                dest_match = re.search(r"Destination:\s*(.+?)(?:\n|Plan|ALERTS|$)", message_text, re.IGNORECASE)
                
                if origin_match:
                    origin = origin_match.group(1).strip()
                if dest_match:
                    destination = dest_match.group(1).strip()
                
                logger.info(f"✓ From IMPORTANT format: '{origin}' → '{destination}'")
            
            else:
                # No IMPORTANT format - extract from original query
                # Clean message (remove context markers)
                clean_message = message_text.split("ALERTS")[0].strip()
                
                # DON'T split on "IMPORTANT:" if there's no IMPORTANT format
                # (That was the bug - splitting on non-existent text created empty string)
                
                # Extract locations from query
                origin, destination = await self.extract_locations_with_llm(clean_message)
                
                logger.info(f"✓ From query: '{origin}' → '{destination}'")
            
            # ================================================================
            # Validation
            # ================================================================
            if not destination:
                response_text = "Please specify destination."
                response_message = Message(
                    message_id=str(uuid4()),
                    parts=[TextPart(text=response_text)],
                    role="agent"
                )
                await event_queue.enqueue_event(response_message)
                logger.warning(f"⚠️ No destination extracted from: {message_text[:100]}")
                return
            
            if not origin:
                response_text = f"Where are you starting from? Example: 'From Park Street to {destination}'"
                response_message = Message(
                    message_id=str(uuid4()),
                    parts=[TextPart(text=response_text)],
                    role="agent"
                )
                await event_queue.enqueue_event(response_message)
                logger.warning(f"⚠️ No origin extracted from: {message_text[:100]}")
                return
            
            # Plan route with ALL intelligence
            result = await self.plan_route_complete(
                origin,
                destination,
                alerts_context,
                wants_multiple,
                check_crowding
            )
            
            response_text = result.get("text", "Could not plan route")
            
            if result.get("ok"):
                response_text += "\n\n✅ Route planning complete"
            
            # Send
            response_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=response_text)],
                role="agent"
            )
            
            await event_queue.enqueue_event(response_message)
            logger.info("✅ Response sent")
            
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            error_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=f"Error: {str(e)}")],
                role="agent"
            )
            await event_queue.enqueue_event(error_message)
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise NotImplementedError()


def main():
    load_dotenv()
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    mbta_api_key = os.getenv("MBTA_API_KEY", "")
    
    skills = [
        AgentSkill(
            id="context_aware_planning",
            name="Context-Aware Planning",
            description="Plans routes considering alerts context and service disruptions",
            tags=["context", "intelligent"],
            examples=["Route considering delays", "Avoid disrupted lines"]
        ),
        AgentSkill(
            id="crowding_aware_routing",
            name="Crowding-Aware Routing",
            description="Checks vehicle occupancy and ranks routes by comfort",
            tags=["crowding", "comfort"],
            examples=["Route avoiding crowds", "Least crowded option"]
        ),
        AgentSkill(
            id="multiple_routes",
            name="Multiple Route Options",
            description="Generate 2-3 different alternatives",
            tags=["options", "alternatives"],
            examples=["Give me two routes", "Show options"]
        ),
        AgentSkill(
            id="api_first_routing",
            name="API-First Routing",
            description="Checks MBTA API for direct routes before generating suggestions",
            tags=["accurate", "api"],
            examples=["Direct routes verified via MBTA API"]
        ),
    ]
    
    agent_card = AgentCard(
        name="mbta-planner",
        description="Complete MBTA route planner with context awareness, crowding intelligence, and multiple route generation",
        url="http://planner-agent:50052/",
        version="5.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
        capabilities=AgentCapabilities(streaming=True)
    )
    
    executor = PlannerExecutor(openai_api_key, mbta_api_key)
    handler = DefaultRequestHandler(executor, task_store=InMemoryTaskStore())
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = server.build()
    
    logger.info("=" * 80)
    logger.info("🚀 Planner v5.1 - COMPLETE MERGED VERSION")
    logger.info("=" * 80)
    logger.info("✅ Context awareness (alerts domain analysis)")
    logger.info("✅ Crowding intelligence (vehicle occupancy)")
    logger.info("✅ Multiple route generation")
    logger.info("✅ API-first routing (no hallucination)")
    logger.info("✅ LLM fallback for transfers")
    logger.info("=" * 80)
    
    uvicorn.run(app, host="0.0.0.0", port=50052, log_level="info")


if __name__ == "__main__":
    main()
