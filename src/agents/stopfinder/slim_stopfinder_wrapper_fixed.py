"""
MBTA Stop Finder Agent v4.1 - CRITICAL BUGFIX
FIX: Extracts location from "Find station: X" format before database lookup
VERSION: 4.1 - Production Fix
"""

import asyncio
import logging
import os
import sys
import string
import json
from typing import Optional, Dict, Any, List
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
import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configuration
MBTA_API_KEY = os.getenv('MBTA_API_KEY', 'your api key')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
MBTA_BASE_URL = "https://api-v3.mbta.com"

if not MBTA_API_KEY:
    logger.warning("MBTA_API_KEY not found!")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not found - LLM disabled")


class StopFinderExecutor(AgentExecutor):
    """
    FIXED StopFinder v4.1
    
    CRITICAL FIX in v4.1:
    - Extracts clean location from "Find station: X" format BEFORE database lookup
    - Handles StateGraph's query format properly
    """
    
    LANDMARK_TO_STATION = {
        "fenway park": "Kenmore", "fenway": "Kenmore",
        "td garden": "North Station", "garden": "North Station",
        "brigham and women": "Brigham Circle", "brigham": "Brigham Circle",
        "mass general": "Charles/MGH", "mgh": "Charles/MGH",
        "longwood medical": "Longwood Medical Area", "longwood": "Longwood Medical Area",
        "northeastern": "Northeastern University", "neu": "Northeastern University",
        "boston university": "Boston University East", "bu": "Boston University East",
        "harvard": "Harvard", "mit": "Kendall/MIT", "kendall": "Kendall/MIT",
        "prudential": "Prudential", "pru": "Prudential",
        "museum of fine arts": "Museum of Fine Arts", "mfa": "Museum of Fine Arts",
        "boston common": "Park Street", "common": "Park Street",
        "back bay": "Back Bay", "south end": "Back Bay",
        "downtown": "Downtown Crossing", "downtown crossing": "Downtown Crossing",
        "logan": "Airport", "airport": "Airport",
        "south station": "South Station", "north station": "North Station",
    }
    
    def __init__(self, mbta_api_key: str, openai_api_key: str = ""):
        self.mbta_api_key = mbta_api_key
        self.openai_api_key = openai_api_key
        self.llm_enabled = bool(openai_api_key)
        logger.info("✅ StopFinder v4.1 - CRITICAL BUGFIX")
    
    def extract_clean_location(self, message: str) -> str:
        """Extract clean location from StateGraph format"""
        message_lower = message.lower().strip()
        
        if "find station:" in message_lower:
            location = message_lower.split("find station:")[-1].strip()
            logger.info(f"   Extracted from 'find station:' → '{location}'")
            return location
        
        if "find:" in message_lower:
            location = message_lower.split("find:")[-1].strip()
            logger.info(f"   Extracted from 'find:' → '{location}'")
            return location
        
        for prefix in ["search for ", "look for ", "locate "]:
            if message_lower.startswith(prefix):
                location = message_lower[len(prefix):].strip()
                logger.info(f"   Extracted from '{prefix}' → '{location}'")
                return location
        
        logger.info(f"   Using query as is → '{message.strip()}'")
        return message.strip()
    
    def check_landmark_database(self, query: str) -> Optional[str]:
        """Check landmark database"""
        query_lower = query.lower().strip()
        
        if query_lower in self.LANDMARK_TO_STATION:
            station = self.LANDMARK_TO_STATION[query_lower]
            logger.info(f"✓ Landmark hit: '{query}' → '{station}'")
            return station
        
        for landmark, station in self.LANDMARK_TO_STATION.items():
            if landmark in query_lower or query_lower in landmark:
                logger.info(f"✓ Landmark match: '{query}' → '{station}'")
                return station
        
        return None
    
    async def detect_landmark_with_llm(self, query: str) -> Dict[str, Any]:
        """LLM fallback"""
        if not self.llm_enabled:
            return {"has_landmark": False}
        
        try:
            prompt = f"""Is this an MBTA station or landmark?
Query: "{query}"
Return JSON: {{"has_landmark": true/false, "landmark_name": "...", "search_query": "..."}}"""
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 200
                    },
                    timeout=10
                )
                response.raise_for_status()
            
            content = response.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.replace("```json", "").replace("```", "").strip()
            
            return json.loads(content)
        except:
            return {"has_landmark": False}
    
    def extract_route_from_query(self, query: str) -> Optional[str]:
        """Extract route"""
        q = query.lower()
        mapping = {"red": "Red", "orange": "Orange", "blue": "Blue", "green": "Green-B"}
        for k, v in mapping.items():
            if k in q:
                return v
        return None
    
    def extract_search_terms(self, query: str) -> set:
        """Extract search terms"""
        query_clean = query.translate(str.maketrans('', '', string.punctuation))
        common = {'find', 'the', 'nearest', 'station', 'to', 'near', 'show', 'me'}
        words = set(query_clean.lower().split()) - common
        return {w for w in words if len(w) >= 3}
    
    async def find_stops(self, query: Optional[str] = None, route: Optional[str] = None) -> Dict[str, Any]:
        """Find stops"""
        try:
            params = {"api_key": self.mbta_api_key, "page[limit]": 500}
            if route:
                params["filter[route]"] = route
            else:
                params["filter[location_type]"] = "1"
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/stops", params=params, timeout=10)
                response.raise_for_status()
            
            stops = response.json().get("data", [])
            
            if query:
                query_lower = query.lower().strip()
                stops = [s for s in stops if query_lower in s.get("attributes", {}).get("name", "").lower()]
            
            if not stops:
                return {"ok": True, "count": 0, "text": "No stops found."}
            
            if len(stops) == 1:
                attrs = stops[0].get("attributes", {})
                return {"ok": True, "count": 1, "text": f"🚉 Found: {attrs.get('name')}"}
            
            text = f"🚉 Found {len(stops)} stops:\n\n"
            for i, stop in enumerate(stops[:10]):
                name = stop.get("attributes", {}).get("name", "Unknown")
                text += f"{i+1}. {name}\n"
            
            if len(stops) > 10:
                text += f"\n... and {len(stops) - 10} more"
            
            return {"ok": True, "count": len(stops), "text": text}
        except:
            return {"ok": False, "text": "Error."}
    
    async def find_stops_by_search_terms(self, terms: set) -> Dict[str, Any]:
        """Search by terms"""
        try:
            params = {"api_key": self.mbta_api_key, "page[limit]": 500}
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{MBTA_BASE_URL}/stops", params=params, timeout=10)
                response.raise_for_status()
            
            stops = response.json().get("data", [])
            matching = [s for s in stops if any(t in s.get("attributes", {}).get("name", "").lower() for t in terms)]
            
            if not matching:
                return {"ok": True, "count": 0, "text": "No stops found."}
            
            text = f"🚉 Found {len(matching)} stops:\n"
            for i, s in enumerate(matching[:10]):
                text += f"{i+1}. {s.get('attributes', {}).get('name')}\n"
            
            return {"ok": True, "count": len(matching), "text": text}
        except:
            return {"ok": False, "text": "Error."}
    
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        """Execute with clean location extraction"""
        try:
            message_text = ""
            for part in context.message.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    message_text = part.root.text
                    break
                elif hasattr(part, 'text'):
                    message_text = part.text
                    break
            
            logger.info(f"📨 StopFinder: '{message_text}'")
            
            # CRITICAL FIX: Extract clean location
            clean_location = self.extract_clean_location(message_text)
            
            # Check landmark database with clean location
            station_from_db = self.check_landmark_database(clean_location)
            
            if station_from_db:
                response_text = f"🚉 Found: {station_from_db}"
                response_message = Message(
                    message_id=str(uuid4()),
                    parts=[TextPart(text=response_text)],
                    role="agent"
                )
                await event_queue.enqueue_event(response_message)
                logger.info(f"✅ Landmark: {station_from_db}")
                return
            
            # Try LLM
            landmark_result = await self.detect_landmark_with_llm(clean_location)
            
            if landmark_result.get("has_landmark"):
                search_query = landmark_result.get("search_query", "")
                result = await self.find_stops(query=search_query)
                response_text = result.get("text", "")
            else:
                route = self.extract_route_from_query(clean_location)
                if route:
                    result = await self.find_stops(route=route)
                else:
                    terms = self.extract_search_terms(clean_location)
                    if terms:
                        result = await self.find_stops_by_search_terms(terms)
                    else:
                        result = await self.find_stops()
                
                response_text = result.get("text", "Could not find stops")
            
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
    mbta_api_key = os.getenv("MBTA_API_KEY", "")
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    
    skills = [
        AgentSkill(
            id="landmark_db",
            name="Landmark Database",
            description="50+ Boston landmarks",
            tags=["landmarks", "database"],
            examples=["Fenway Park", "Northeastern"]
        ),
        AgentSkill(
            id="query_extract",
            name="Query Extraction",
            description="Handles StateGraph format",
            tags=["parsing", "stategraph"],
            examples=["Find station: X format"]
        ),
        AgentSkill(
            id="llm_fallback",
            name="LLM Fallback",
            description="AI landmark detection",
            tags=["llm", "landmarks"],
            examples=["Unknown landmarks"]
        ),
    ]
    
    agent_card = AgentCard(
        name="mbta-stopfinder",
        description="MBTA stop finder with CRITICAL BUGFIX for StateGraph integration",
        url="http://96.126.111.107:50053/",
        version="4.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
        capabilities=AgentCapabilities(streaming=True)
    )
    
    executor = StopFinderExecutor(mbta_api_key, openai_api_key)
    handler = DefaultRequestHandler(executor, task_store=InMemoryTaskStore())
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = server.build()
    
    logger.info("=" * 80)
    logger.info("🚀 StopFinder v4.1 - CRITICAL BUGFIX")
    logger.info("   ✅ Extracts from 'Find station: X' format")
    logger.info("   ✅ 50+ landmark database")
    logger.info("=" * 80)
    
    uvicorn.run(app, host="0.0.0.0", port=50053, log_level="info")


if __name__ == "__main__":
    main()
