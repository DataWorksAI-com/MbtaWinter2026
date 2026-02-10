"""
SLIM-enabled Alerts Agent Server
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
import httpx
from uuid import uuid4

logger = logging.getLogger(__name__)


class AlertsAgentExecutor(AgentExecutor):
    """Executor that handles alert requests"""
    
    def __init__(self, mbta_api_key: str):
        self.mbta_api_key = mbta_api_key
    
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        """Handle incoming requests"""
        # Import at function level to avoid scope issues
        from a2a.types import TextPart
        from uuid import uuid4
        
        try:
            # Get the message from context - handle Part(root=TextPart) structure
            message_text = ""
            for part in context.message.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    message_text = part.root.text
                    break
                elif hasattr(part, 'text'):
                    message_text = part.text
                    break
            
            logger.info(f"📨 Alerts Agent received: {message_text}")
            
            # Fetch alerts from MBTA API
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api-v3.mbta.com/alerts",
                    params={"api_key": self.mbta_api_key},
                    timeout=10.0
                )
                alerts_data = response.json().get("data", [])
            
            # Format response
            if not alerts_data:
                text = "✅ No active alerts found"
            else:
                text = f"🚨 Found {len(alerts_data)} active MBTA alerts\n"
                for i, alert in enumerate(alerts_data[:3], 1):
                    header = alert.get("attributes", {}).get("header", "Unknown")
                    text += f"{i}. {header}\n"
            
            # Send response via event queue
            # from a2a.types import TextPart  # Already imported at top
            response_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=text)],
                role="agent"  # Must be 'agent' or 'user', not 'assistant'
            )
            await event_queue.enqueue_event(response_message)
            
        except Exception as e:
            logger.error(f"❌ Error in alerts executor: {e}", exc_info=True)
            from a2a.types import TextPart
            from uuid import uuid4
            error_message = Message(
                message_id=str(uuid4()),
                parts=[TextPart(text=f"Error: {str(e)}")],
                role="agent"
            )
            await event_queue.enqueue_event(error_message)
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        """Handle cancellation (not implemented for simple alerts)"""
        raise NotImplementedError("Cancellation not supported for alerts agent")


def main():
    """Start Alerts A2A server with SLIM support"""
    load_dotenv()
    
    mbta_api_key = os.getenv("MBTA_API_KEY", "")
    
    # Define skill
    skill = AgentSkill(
        id="mbta_alerts",
        name="MBTA Service Alerts",
        description="Provides real-time service alerts, delays, and disruptions for Boston MBTA",
        tags=["alerts", "delays", "disruptions", "mbta"],
        examples=["Red Line delays?", "Any service alerts?", "Is the subway running?"]
    )
    
    # Define agent card
    agent_card = AgentCard(
        name="mbta-alerts",
        description="Provides real-time service alerts, delays, and disruptions for Boston MBTA trains and buses",
        url="http://96.126.111.107:50051/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[skill],
        capabilities=AgentCapabilities(streaming=True)
    )
    
    # Create agent executor
    agent_executor = AlertsAgentExecutor(mbta_api_key)
    
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
    
    logger.info("🚀 Starting Alerts Agent with A2A+SLIM on port 50051")
    
    # Run with uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=50051,
        log_level="info"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    main()