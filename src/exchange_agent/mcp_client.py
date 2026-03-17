# src/exchange_agent/mcp_client.py

"""
MCP Client for Exchange Agent
Connects to mbta-mcp server via stdio subprocess

Simplified client - all tool calls go through call_tool(tool_name, arguments).
No legacy typed wrappers needed.
"""

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from opentelemetry import trace
import logging
import json
import sys
from typing import Optional, Dict, Any

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
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "mbta_mcp.server"],
                env=None
            )

            logger.info(f"Starting mbta-mcp server subprocess...")
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
        """Ensure client is initialized before use"""
        if not self._initialized:
            await self.initialize()

    async def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call any MCP tool with per-tool tracing based on the tool name."""
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
                span.set_attribute("result_size", len(str(data)))
                logger.info(f"✓ {tool_name} completed")
                return data
            except Exception as e:
                logger.error(f"❌ MCP tool failed: {tool_name} - {e}", exc_info=True)
                span.record_exception(e)
                span.set_attribute("success", False)
                raise

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
