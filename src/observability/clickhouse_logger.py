import clickhouse_connect
from typing import Dict, Any, Optional
from datetime import datetime
import logging
import os
import json

logger = logging.getLogger(__name__)

class ClickHouseLogger:
    """Logs events to ClickHouse for analytics"""
    
    def __init__(self):
        self.enabled = os.getenv("CLICKHOUSE_ENABLED", "true").lower() == "true"
        
        if self.enabled:
            try:
                self.client = clickhouse_connect.get_client(
                    host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
                    port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
                    username=os.getenv("CLICKHOUSE_USER", "default"),
                    password=os.getenv("CLICKHOUSE_PASSWORD", "clickhouse"),
                    database=os.getenv("CLICKHOUSE_DB", "mbta_logs")
                )
                logger.info("✅ ClickHouse logger initialized")
            except Exception as e:
                logger.warning(f"⚠️  ClickHouse connection failed: {e}. Logging disabled.")
                self.enabled = False
        else:
            logger.info("ClickHouse logging disabled via env var")
    
    def log_conversation(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        intent: str = "",
        routed_to_orchestrator: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Log a conversation message"""
        if not self.enabled:
            return
        
        try:
            self.client.insert('conversations', [[
                conversation_id,
                user_id,
                datetime.now(),
                role,
                content[:1000],  # Truncate long messages
                intent,
                1 if routed_to_orchestrator else 0,
                json.dumps(metadata or {})
            ]], column_names=[
                'conversation_id', 'user_id', 'timestamp', 'message_role',
                'message_content', 'intent', 'routed_to_orchestrator', 'metadata'
            ])
            logger.debug(f"Logged conversation: {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to log conversation: {e}")
    
    def log_agent_invocation(
        self,
        invocation_id: str,
        conversation_id: str,
        agent_name: str,
        duration_ms: float,
        status: str,
        error_message: str = "",
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None
    ):
        """Log an agent invocation"""
        if not self.enabled:
            return
        
        try:
            self.client.insert('agent_invocations', [[
                invocation_id,
                conversation_id,
                agent_name,
                datetime.now(),
                duration_ms,
                status,
                error_message[:500] if error_message else "",
                json.dumps(request_payload or {})[:2000],
                json.dumps(response_payload or {})[:2000]
            ]], column_names=[
                'invocation_id', 'conversation_id', 'agent_name', 'timestamp',
                'duration_ms', 'status', 'error_message', 'request_payload', 'response_payload'
            ])
            logger.debug(f"Logged agent invocation: {agent_name}")
        except Exception as e:
            logger.error(f"Failed to log agent invocation: {e}")
    
    def log_llm_call(
        self,
        call_id: str,
        conversation_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
        intent: str = "",
        confidence: float = 0.0
    ):
        """Log an LLM call"""
        if not self.enabled:
            return
        
        try:
            self.client.insert('llm_calls', [[
                call_id,
                conversation_id,
                datetime.now(),
                model,
                prompt_tokens,
                completion_tokens,
                prompt_tokens + completion_tokens,
                duration_ms,
                intent,
                confidence
            ]], column_names=[
                'call_id', 'conversation_id', 'timestamp', 'model',
                'prompt_tokens', 'completion_tokens', 'total_tokens',
                'duration_ms', 'intent', 'confidence'
            ])
            logger.debug(f"Logged LLM call: {call_id}")
        except Exception as e:
            logger.error(f"Failed to log LLM call: {e}")

# Global instance
_clickhouse_logger: Optional[ClickHouseLogger] = None

def get_clickhouse_logger() -> ClickHouseLogger:
    """Get or create ClickHouse logger instance"""
    global _clickhouse_logger
    if _clickhouse_logger is None:
        _clickhouse_logger = ClickHouseLogger()
    return _clickhouse_logger