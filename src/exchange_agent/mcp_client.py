# src/exchange_agent/mcp_client.py

"""
MCP Client for Exchange Agent
Connects to mbta-mcp server via stdio subprocess

This version is compatible with:
- generic tool execution via call_tool(...)
- forced MCP smart mode
- auto MCP path
- legacy typed wrapper fallback calls from exchange_server.py
"""

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from opentelemetry import trace
import logging
import json
import sys
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
        """Start mbta-mcp server as subprocess and establish connection."""
        if self._initialized:
            logger.info("MCP client already initialized")
            return

        logger.info("=" * 60)
        logger.info("Initializing MCP Client")
        logger.info("=" * 60)

        try:
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mbta_mcp.server"],
                env=None
            )

            logger.info("Starting mbta-mcp server subprocess...")
            logger.info(f"  Command: {server_params.command} {' '.join(server_params.args)}")

            self._client_context = stdio_client(server_params)
            read_stream, write_stream = await self._client_context.__aenter__()

            logger.info("✓ Server subprocess started")

            self.session = ClientSession(read_stream, write_stream)
            self._session_context = self.session
            await self._session_context.__aenter__()

            logger.info("✓ MCP session created")

            await self.session.initialize()
            logger.info("✓ MCP session initialized")

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
        """Ensure client is initialized before use."""
        if not self._initialized:
            await self.initialize()

    async def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Generic MCP tool caller.
        Preferred execution path for current exchange server.
        """
        await self.ensure_initialized()

        if not tool_name:
            raise ValueError("tool_name is required")

        arguments = arguments or {}
        span_name = f"mcp_tool.{tool_name}"

        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("tool_name", tool_name)
            span.set_attribute("arguments", json.dumps(arguments, default=str))

            logger.info(f"📞 MCP call: {tool_name}({arguments})")

            try:
                result = await self.session.call_tool(tool_name, arguments)
                data = self._parse_result(result)
                span.set_attribute("success", True)
                span.set_attribute("result_size", len(str(data)))
                logger.info(f"✓ {tool_name} completed")
                return data
            except Exception as e:
                logger.error(f"❌ MCP tool failed: {tool_name} - {e}", exc_info=True)
                span.record_exception(e)
                span.set_attribute("success", False)
                raise

    # ---------------------------------------------------------------------
    # Legacy typed wrappers
    # These keep compatibility with exchange_server code paths that still use:
    # mcp_client.get_alerts, mcp_client.search_stops, mcp_client.plan_trip, etc.
    # Internally they all delegate to call_tool(...)
    # ---------------------------------------------------------------------

    @tracer.start_as_current_span("mcp_get_alerts")
    async def get_alerts(
        self,
        route_id: Optional[Any] = None,
        activity: Optional[List[str]] = None,
        datetime: Optional[str] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if route_id is not None:
            arguments["route_id"] = route_id
        if activity is not None:
            arguments["activity"] = activity
        if datetime:
            arguments["datetime"] = datetime
        return await self.call_tool("mbta_get_alerts", arguments)

    @tracer.start_as_current_span("mcp_get_routes")
    async def get_routes(
        self,
        route_id: Optional[str] = None,
        route_type: Optional[int] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if route_id:
            arguments["route_id"] = route_id
        if route_type is not None:
            arguments["route_type"] = route_type
        return await self.call_tool("mbta_get_routes", arguments)

    @tracer.start_as_current_span("mcp_get_stops")
    async def get_stops(
        self,
        stop_id: Optional[str] = None,
        route_id: Optional[str] = None,
        location_type: Optional[int] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id:
            arguments["route_id"] = route_id
        if location_type is not None:
            arguments["location_type"] = location_type
        return await self.call_tool("mbta_get_stops", arguments)

    @tracer.start_as_current_span("mcp_search_stops")
    async def search_stops(self, query: str) -> Dict[str, Any]:
        return await self.call_tool("mbta_search_stops", {"query": query})

    @tracer.start_as_current_span("mcp_get_predictions")
    async def get_predictions(
        self,
        stop_id: Optional[str] = None,
        route_id: Optional[Any] = None,
        direction_id: Optional[int] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id is not None:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        return await self.call_tool("mbta_get_predictions", arguments)

    @tracer.start_as_current_span("mcp_get_predictions_for_stop")
    async def get_predictions_for_stop(self, stop_id: str) -> Dict[str, Any]:
        return await self.call_tool("mbta_get_predictions_for_stop", {"stop_id": stop_id})

    @tracer.start_as_current_span("mcp_get_schedules")
    async def get_schedules(
        self,
        stop_id: Optional[str] = None,
        route_id: Optional[str] = None,
        direction_id: Optional[int] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if stop_id:
            arguments["stop_id"] = stop_id
        if route_id:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        return await self.call_tool("mbta_get_schedules", arguments)

    @tracer.start_as_current_span("mcp_get_trips")
    async def get_trips(
        self,
        route_id: Optional[str] = None,
        direction_id: Optional[int] = None
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if route_id:
            arguments["route_id"] = route_id
        if direction_id is not None:
            arguments["direction_id"] = direction_id
        return await self.call_tool("mbta_get_trips", arguments)

    @tracer.start_as_current_span("mcp_get_vehicles")
    async def get_vehicles(self, route_id: Optional[Any] = None) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if route_id is not None:
            arguments["route_id"] = route_id
        return await self.call_tool("mbta_get_vehicles", arguments)

    @tracer.start_as_current_span("mcp_get_nearby_stops")
    async def get_nearby_stops(
        self,
        latitude: float,
        longitude: float,
        radius: float = 0.5
    ) -> Dict[str, Any]:
        arguments = {
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius
        }
        return await self.call_tool("mbta_get_nearby_stops", arguments)

    @tracer.start_as_current_span("mcp_plan_trip")
    async def plan_trip(
        self,
        from_location: Optional[str] = None,
        to_location: Optional[str] = None,
        datetime: Optional[str] = None,
        arrive_by: bool = False,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Supports both:
        - typed wrapper usage: from_location / to_location
        - normalized generic compatibility via kwargs: from / to
        """
        arguments: Dict[str, Any] = {}

        origin = from_location or kwargs.get("from")
        destination = to_location or kwargs.get("to")

        if origin:
            arguments["from"] = origin
        if destination:
            arguments["to"] = destination
        if datetime:
            arguments["datetime"] = datetime
        if arrive_by:
            arguments["arrive_by"] = arrive_by

        return await self.call_tool("mbta_plan_trip", arguments)

    @tracer.start_as_current_span("mcp_list_all_routes")
    async def list_all_routes(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        return await self.call_tool("mbta_list_all_routes", arguments)

    @tracer.start_as_current_span("mcp_list_all_stops")
    async def list_all_stops(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        return await self.call_tool("mbta_list_all_stops", arguments)

    @tracer.start_as_current_span("mcp_list_all_alerts")
    async def list_all_alerts(self, fuzzy_filter: Optional[str] = None) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {}
        if fuzzy_filter:
            arguments["fuzzy_filter"] = fuzzy_filter
        return await self.call_tool("mbta_list_all_alerts", arguments)

    def _parse_result(self, result: Any) -> Dict[str, Any]:
        """Parse MCP tool result."""
        try:
            if hasattr(result, "structured_content") and result.structured_content is not None:
                if isinstance(result.structured_content, dict):
                    return result.structured_content
                return {"structured_content": result.structured_content}

            if hasattr(result, "content") and result.content:
                text_content = getattr(result.content[0], "text", None)
                if isinstance(text_content, str) and text_content.strip():
                    try:
                        return json.loads(text_content)
                    except json.JSONDecodeError:
                        return {"text": text_content}
            return {}
        except Exception as e:
            logger.error(f"Failed to parse MCP result: {e}", exc_info=True)
            return {"error": str(e)}

    async def cleanup(self):
        """Close MCP connection and stop server subprocess."""
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
            self._available_tools = []

        logger.info("✓ MCP client cleaned up")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
