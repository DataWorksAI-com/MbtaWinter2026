"""
SLIM-enabled Planner Agent Server
Uses A2A SDK + SLIM transport from agntcy-app-sdk
"""

import asyncio
import logging
import os
import sys
from typing import Dict, Any

sys.path.insert(0, '/opt/mbta-agents')

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCard, AgentSkill, AgentCapabilities, Message
from dotenv import load_dotenv
import uvicorn
from openai import OpenAI
import json

logger = logging.getLogger(__name__)


class PlannerAgentExecutor(AgentExecutor):
    """Executor that handles route planning requests"""
    
    def __init__(self, openai_api_key: str):
        self.openai_client = OpenAI(api_key=openai_api_key)
    
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        """Handle incoming route planning requests"""
        try:
            # Get message text - handle Part(root=TextPart) structure
            message_text = ""
            for part in context.message.parts:
                # Check if it's a Part with root attribute
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    message_text = part.root.text
                    break
                # Or direct TextPart
                elif hasattr(part, 'text'):
                    message_text = part.text
                    break
            
            logger.info(f"📨 Planner Agent received: {message_text}")
            
            # Use LLM to extract origin and destination
            extraction_prompt = f"""
Extract the origin and destination from this transit query.

Query: "{message_text}"

IMPORTANT: Convert locations to actual MBTA station names.

Common Boston landmark → T station mappings:
- MIT, MIT campus → "Kendall/MIT"
- Harvard, Harvard University → "Harvard"  
- Northeastern University, NEU → "Ruggles" or "Northeastern"
- Akamai Technologies (Cambridge) → "Kendall/MIT"
- Boston Common → "Park Street"
- Fenway Park → "Kenmore"
- TD Garden → "North Station"
- Prudential Center → "Prudential"
- Copley Square → "Copley"
- South Station → "South Station"
- Logan Airport → "Airport"

If you don't know the nearest station, keep the original name.

Return ONLY valid JSON:
{{"origin": "station name or null", "destination": "station name or null"}}

Examples:
- "Park Street to MIT" → {{"origin": "Park Street", "destination": "Kendall/MIT"}}
- "From downtown to Northeastern" → {{"origin": "Downtown Crossing", "destination": "Ruggles"}}
- "Park to Akamai" → {{"origin": "Park Street", "destination": "Kendall/MIT"}}
"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a Boston transit expert. Convert landmarks/addresses to their nearest MBTA station names. Use the provided mappings."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0,
                response_format={"type": "json_object"}
            )
            
            locations = json.loads(response.choices[0].message.content)
            origin = locations.get("origin")
            destination = locations.get("destination")
            
            if not origin or not destination:
                text = "I need both an origin and destination to plan a route. Where are you starting from and where do you want to go?"
            else:
                text = f"🚇 Planning route from {origin} to {destination}...\n"
                text += f"Route: Take the Red Line from {origin} to {destination}"
            
            # Send response
            from a2a.types import TextPart
            from uuid import uuid4
            response_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=text)],
                role="agent"
            )
            await event_queue.enqueue_event(response_message)
            
            logger.info(f"✅ Route plan sent via SLIM")
            
        except Exception as e:
            logger.error(f"❌ Error in planner executor: {e}", exc_info=True)
            from a2a.types import TextPart
            from uuid import uuid4
            error_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=f"Error: {str(e)}")],
                role="agent"
            )
            await event_queue.enqueue_event(error_message)
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        """Handle cancellation"""
        raise NotImplementedError("Cancellation not supported for planner agent")


def main():
    """Start Planner A2A server with SLIM support"""
    load_dotenv()
    
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    
    # Define skill
    skill = AgentSkill(
        id="mbta_route_planning",
        name="MBTA Route Planning",
        description="Plans optimal routes and trips on Boston MBTA transit network",
        tags=["routing", "directions", "trip-planning", "mbta"],
        examples=["How do I get to Harvard?", "Park Street to MIT", "Route from downtown to airport"]
    )
    
    # Define agent card
    agent_card = AgentCard(
        name="mbta-route-planner",
        description="Plans optimal routes and trips on Boston MBTA transit network. Provides step-by-step directions.",
        url="http://96.126.111.107:50052/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[skill],
        capabilities=AgentCapabilities(streaming=True)
    )
    
    # Create agent executor
    agent_executor = PlannerAgentExecutor(openai_api_key)
    
    # Create request handler
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore()
    )
    
    # Create A2A server
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler
    )
    
    # Build ASGI app
    app = server.build()
    
    logger.info("🚀 Starting Planner Agent with A2A+SLIM on port 50052")
    
    # Run with uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=50052,
        log_level="info"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    main()