"""
SLIM Client for Exchange Agent
Calls agents via SLIM transport using agntcy-app-sdk
"""

import logging
from typing import Dict, Any
from agntcy_app_sdk.factory import AgntcyFactory

logger = logging.getLogger(__name__)


class SlimAgentClient:
    """Client for calling agents via SLIM transport"""
    
    def __init__(self):
        self.factory = AgntcyFactory()
        self.clients: Dict[str, Any] = {}
        self._initialized = False
        
    async def initialize(self):
        """Initialize SLIM clients for all agents"""
        if self._initialized:
            return
        
        try:
            logger.info("🚀 Initializing SLIM agent clients...")
            
            self.clients["alerts"] = await self.factory.create_client(
                protocol="A2A",
                agent_url="http://96.126.111.107:50051/"
            )
            logger.info("✅ Alerts SLIM client ready")
            
            self.clients["planner"] = await self.factory.create_client(
                protocol="A2A",
                agent_url="http://96.126.111.107:50052/"
            )
            logger.info("✅ Planner SLIM client ready")
            
            self.clients["stopfinder"] = await self.factory.create_client(
                protocol="A2A",
                agent_url="http://96.126.111.107:50053/"
            )
            logger.info("✅ StopFinder SLIM client ready")
            
            self._initialized = True
            logger.info("✅ All SLIM clients initialized")
            
        except Exception as e:
            logger.error(f"❌ SLIM client initialization failed: {e}", exc_info=True)
            raise
    
    async def call_agent(self, agent_name: str, message: str) -> Dict[str, Any]:
        """Call agent via SLIM transport"""
        if not self._initialized:
            await self.initialize()
        
        client = self.clients.get(agent_name)
        if not client:
            raise ValueError(f"Unknown agent: {agent_name}")
        
        try:
            logger.info(f"📤 Calling {agent_name} via SLIM...")
            
            from a2a.types import SendMessageRequest, MessageSendParams, Message, TextPart
            from uuid import uuid4
            
            text_part = TextPart(text=message)
            msg = Message(
                message_id=str(uuid4()),
                parts=[text_part],
                role="user"
            )
            
            message_params = MessageSendParams(message=msg)
            
            request = SendMessageRequest(
                id=str(uuid4()),
                params=message_params
            )
            
            response = await client.send_message(request)
            
            # Extract text from SendMessageResponse
            response_text = ""
            
            # SendMessageResponse has 'root' which is SendMessageSuccessResponse or Error
            if hasattr(response, 'root') and response.root:
                root = response.root
                
                # Check if it's a success response
                if hasattr(root, 'result') and root.result:
                    message_result = root.result
                    
                    # message_result is a Message with parts
                    if hasattr(message_result, 'parts') and message_result.parts:
                        for part in message_result.parts:
                            # part is Part with root=TextPart
                            if hasattr(part, 'root') and hasattr(part.root, 'text'):
                                response_text = part.root.text
                                break
            
            if not response_text:
                response_text = str(response)
            
            logger.info(f"✅ SLIM SUCCESS for {agent_name}: {len(response_text)} chars")
            
            return {
                "response": response_text,
                "metadata": {
                    "transport": "slim",
                    "agent": agent_name
                }
            }
            
        except Exception as e:
            logger.error(f"❌ SLIM call to {agent_name} failed: {e}", exc_info=True)
            raise
    
    async def cleanup(self):
        """Close all SLIM client connections"""
        logger.info("🔄 Closing SLIM clients...")
        
        for agent_name, client in self.clients.items():
            try:
                if hasattr(client, 'close'):
                    await client.close()
                logger.info(f"✅ Closed {agent_name} client")
            except Exception as e:
                logger.warning(f"⚠️  Error closing {agent_name} client: {e}")
        
        self.clients.clear()
        self._initialized = False
        logger.info("✅ All SLIM clients closed")
