# src/exchange_agent/mcp_client.py (UPDATED with correct tool names)

"""
MCP Client for Exchange Agent
Connects to mbta-mcp server via stdio subprocess
"""

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from opentelemetry import trace
import asyncio
import logging
import json
import sys
import os
from typing import Optional, Dict, Any, List

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


class MCPClient:
    """
    MCP client for communicating with mbta-mcp server
    Uses stdio transport - starts server as subprocess
    """
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self._client_context = None
        self._session_context = None
        self._initialized = False
        self._available_tools = []
    
    async def initialize(self):
        """Start mbta-mcp server as subprocess and establish connection"""
        
        if self._initialized:
            logger.info("MCP client already initialized")
            return
        
        logger.info("=" * 60)
        logger.info("Initializing MCP Client")
        logger.info("=" * 60)
        
        try:
            # Start mbta-mcp server
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mbta_mcp.server"],
                env=None
            )
            
            logger.info(f"Starting mbta-mcp server subprocess...")
            logger.info(f"  Command: {server_params.command} {' '.join(server_params.args)}")
            
            # Start server and get stdio streams
            self._client_context = stdio_client(server_params)
            read_stream, write_stream = await self._client_context.__aenter__()
            
            logger.info("✓ Server subprocess started")
            
            # Create MCP session
            self.session = ClientSession(read_stream, write_stream)
            self._session_context = self.session
            await self._session_context.__aenter__()
            
            logger.info("✓ MCP session created")
            
            # Initialize session
            await self.session.initialize()
            
            logger.info("✓ MCP session initialized")
            
            # List available tools
            response = await self.session.list_tools()
            self._available_tools = response.tools
            
            logger.info(f"✓ Server has {len(self._available_tools)} tools available")
            
            self._initialized = True
            
            logger.info("=" * 60)
            logger.info("✅ MCP Client initialized successfully")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize MCP client: {e}", exc_info=True)
            await self.cleanup()
            raise
    
    async def ensure_initialized(self):
        """Ensure client is initialized before use"""
        if not self._initialized:
            await self.initialize()
    

    
    @tracer.start_as_current_span("mcp_get_alerts")
    async def get_alerts(self, 
                         route_id: Optional[str] = None,
                         activity: Optional[List[str]] = None,
                         datetime: Optional[str] = None) -> Dict[str, Any]:
        """
        Get MBTA service alerts
        Tool name: mbta_get_alerts
        """
        await self.ensure_initialized()
        
        arguments = {}
        if route_id:
            arguments["route_id"] = route_id
        if activity:
            arguments["activity"] = activity
        if datetime:
            arguments["datetime"] = datetime
        
        logger.info(f"📞 MCP call: mbta_get_alerts({arguments})")
        
        result = await self.session.call_tool("mbta_get_alerts", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_alerts completed - {len(data.get('data', []))} alerts")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_routes")
    async def get_routes(self, 
                        route_id: Optional[str] = None,
                        route_type: Optional[int] = None) -> Dict[str, Any]:
        """
        Get MBTA routes
        Tool name: mbta_get_routes
        """
        await self.ensure_initialized()
        
        arguments = {}
        if route_id:
            arguments["route_id"] = route_id
        if route_type is not None:
            arguments["route_type"] = route_type
        
        logger.info(f"📞 MCP call: mbta_get_routes({arguments})")
        
        result = await self.session.call_tool("mbta_get_routes", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_routes completed - {len(data.get('data', []))} routes")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_stops")
    async def get_stops(self, 
                        stop_id: Optional[str] = None,
                        route_id: Optional[str] = None,
                        location_type: Optional[int] = None) -> Dict[str, Any]:
        """
        Get MBTA stops
        Tool name: mbta_get_stops
        """
        await self.ensure_initialized()
        
        arguments = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id:
            arguments["route_id"] = route_id
        if location_type is not None:
            arguments["location_type"] = location_type
        
        logger.info(f"📞 MCP call: mbta_get_stops({arguments})")
        
        result = await self.session.call_tool("mbta_get_stops", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_stops completed - {len(data.get('data', []))} stops")
        
        return data
    
    @tracer.start_as_current_span("mcp_search_stops")
    async def search_stops(self, query: str) -> Dict[str, Any]:
        """
        Search for stops by name
        Tool name: mbta_search_stops
        """
        await self.ensure_initialized()
        
        logger.info(f"📞 MCP call: mbta_search_stops(query='{query}')")
        
        result = await self.session.call_tool("mbta_search_stops", {"query": query})
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_search_stops completed - {len(data.get('data', []))} stops")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_predictions")
    async def get_predictions(self, 
                             stop_id: Optional[str] = None,
                             route_id: Optional[str] = None,
                             direction_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get real-time predictions
        Tool name: mbta_get_predictions
        """
        await self.ensure_initialized()
        
        arguments = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        
        logger.info(f"📞 MCP call: mbta_get_predictions({arguments})")
        
        result = await self.session.call_tool("mbta_get_predictions", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_predictions completed - {len(data.get('data', []))} predictions")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_predictions_for_stop")
    async def get_predictions_for_stop(self, stop_id: str) -> Dict[str, Any]:
        """
        Get all predictions for a specific stop
        Tool name: mbta_get_predictions_for_stop
        """
        await self.ensure_initialized()
        
        logger.info(f"📞 MCP call: mbta_get_predictions_for_stop(stop_id='{stop_id}')")
        
        result = await self.session.call_tool("mbta_get_predictions_for_stop", {"stop_id": stop_id})
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_predictions_for_stop completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_schedules")
    async def get_schedules(self,
                           stop_id: Optional[str] = None,
                           route_id: Optional[str] = None,
                           direction_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get MBTA schedules
        Tool name: mbta_get_schedules
        """
        await self.ensure_initialized()
        
        arguments = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        
        logger.info(f"📞 MCP call: mbta_get_schedules({arguments})")
        
        result = await self.session.call_tool("mbta_get_schedules", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_schedules completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_trips")
    async def get_trips(self,
                       route_id: Optional[str] = None,
                       direction_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get MBTA trips
        Tool name: mbta_get_trips
        """
        await self.ensure_initialized()
        
        arguments = {}
        if route_id:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        
        logger.info(f"📞 MCP call: mbta_get_trips({arguments})")
        
        result = await self.session.call_tool("mbta_get_trips", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_trips completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_vehicles")
    async def get_vehicles(self,
                          route_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get real-time vehicle positions
        Tool name: mbta_get_vehicles
        """
        await self.ensure_initialized()
        
        arguments = {}
        if route_id:
            arguments["route_id"] = route_id
        
        logger.info(f"📞 MCP call: mbta_get_vehicles({arguments})")
        
        result = await self.session.call_tool("mbta_get_vehicles", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_vehicles completed - {len(data.get('data', []))} vehicles")
        
        return data
    
    @tracer.start_as_current_span("mcp_get_nearby_stops")
    async def get_nearby_stops(self, 
                              latitude: float,
                              longitude: float,
                              radius: float = 0.5) -> Dict[str, Any]:
        """
        Get stops near a location
        Tool name: mbta_get_nearby_stops
        """
        await self.ensure_initialized()
        
        arguments = {
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius
        }
        
        logger.info(f"📞 MCP call: mbta_get_nearby_stops({arguments})")
        
        result = await self.session.call_tool("mbta_get_nearby_stops", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_get_nearby_stops completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_plan_trip")
    async def plan_trip(self,
                       from_location: str,
                       to_location: str,
                       datetime: Optional[str] = None,
                       arrive_by: bool = False) -> Dict[str, Any]:
        """
        Plan a trip between two locations
        Tool name: mbta_plan_trip
        """
        await self.ensure_initialized()
        
        arguments = {
            "from": from_location,
            "to": to_location
        }
        if datetime:
            arguments["datetime"] = datetime
        if arrive_by:
            arguments["arrive_by"] = arrive_by
        
        logger.info(f"📞 MCP call: mbta_plan_trip({arguments})")
        
        result = await self.session.call_tool("mbta_plan_trip", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_plan_trip completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_list_all_routes")
    async def list_all_routes(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        List all routes with optional fuzzy filtering
        Tool name: mbta_list_all_routes
        """
        await self.ensure_initialized()
        
        arguments = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        
        logger.info(f"📞 MCP call: mbta_list_all_routes({arguments})")
        
        result = await self.session.call_tool("mbta_list_all_routes", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_list_all_routes completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_list_all_stops")
    async def list_all_stops(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        List all stops with optional fuzzy filtering
        Tool name: mbta_list_all_stops
        """
        await self.ensure_initialized()
        
        arguments = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        
        logger.info(f"📞 MCP call: mbta_list_all_stops({arguments})")
        
        result = await self.session.call_tool("mbta_list_all_stops", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_list_all_stops completed")
        
        return data
    
    @tracer.start_as_current_span("mcp_list_all_alerts")
    async def list_all_alerts(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        List all alerts with optional fuzzy filtering
        Tool name: mbta_list_all_alerts
        """
        await self.ensure_initialized()
        
        arguments = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        
        logger.info(f"📞 MCP call: mbta_list_all_alerts({arguments})")
        
        result = await self.session.call_tool("mbta_list_all_alerts", arguments)
        data = self._parse_result(result)
        
        logger.info(f"✓ mbta_list_all_alerts completed")
        
        return data
    
    def _parse_result(self, result) -> Dict[str, Any]:
        """Parse MCP tool result"""
        try:
            if hasattr(result, 'content') and result.content:
                text_content = result.content[0].text
                return json.loads(text_content)
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MCP result as JSON: {e}")
            if 'text_content' in locals():
                logger.error(f"Raw content: {text_content[:200]}...")
            return {"error": "Invalid JSON response"}
        except Exception as e:
            logger.error(f"Failed to parse MCP result: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def cleanup(self):
        """Close MCP connection and stop server subprocess"""
        
        if not self._initialized:
            return
        
        logger.info("Cleaning up MCP client...")
        
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
                logger.info("✓ MCP session closed")
            
            if self._client_context:
                await self._client_context.__aexit__(None, None, None)
                logger.info("✓ MCP server subprocess stopped")
                
        except Exception as e:
            logger.error(f"Error during MCP cleanup: {e}", exc_info=True)
        
        finally:
            self._initialized = False
            self.session = None
            self._client_context = None
            self._session_context = None
            
        logger.info("✓ MCP client cleaned up")
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.cleanup()