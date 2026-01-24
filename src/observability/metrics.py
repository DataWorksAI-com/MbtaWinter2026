from opentelemetry.metrics import Meter
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Collects application metrics"""
    
    def __init__(self, meter: Meter):
        self.meter = meter
        
        # Create metrics
        self.request_counter = meter.create_counter(
            name="requests_total",
            description="Total number of requests",
            unit="1"
        )
        
        self.error_counter = meter.create_counter(
            name="errors_total",
            description="Total number of errors",
            unit="1"
        )
        
        self.agent_invocation_counter = meter.create_counter(
            name="agent_invocations_total",
            description="Total number of agent invocations",
            unit="1"
        )
        
        self.request_duration = meter.create_histogram(
            name="request_duration_seconds",
            description="Request duration in seconds",
            unit="s"
        )
        
        self.llm_token_counter = meter.create_counter(
            name="llm_tokens_total",
            description="Total LLM tokens used",
            unit="1"
        )
        
        logger.info("MetricsCollector initialized")
    
    def record_request(self, attributes: Dict[str, Any] = None):
        """Record a request"""
        self.request_counter.add(1, attributes=attributes or {})
    
    def record_error(self, attributes: Dict[str, Any] = None):
        """Record an error"""
        self.error_counter.add(1, attributes=attributes or {})
    
    def record_agent_invocations(self, count: int, attributes: Dict[str, Any] = None):
        """Record agent invocations"""
        self.agent_invocation_counter.add(count, attributes=attributes or {})
    
    def record_duration(self, duration: float, attributes: Dict[str, Any] = None):
        """Record request duration"""
        self.request_duration.record(duration, attributes=attributes or {})
    
    def record_llm_tokens(self, tokens: int, attributes: Dict[str, Any] = None):
        """Record LLM token usage"""
        self.llm_token_counter.add(tokens, attributes=attributes or {})
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics snapshot"""
        return {
            "status": "metrics_exported_to_otel",
            "message": "View metrics in Grafana dashboard"
        }