from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from typing import Optional, Dict, Any
import logging
from functools import wraps
import time

logger = logging.getLogger(__name__)

def traced(span_name: Optional[str] = None):
    """Decorator to automatically trace functions"""
    
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            name = span_name or f"{func.__module__}.{func.__name__}"
            
            with tracer.start_as_current_span(name) as span:
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            name = span_name or f"{func.__module__}.{func.__name__}"
            
            with tracer.start_as_current_span(name) as span:
                try:
                    result = func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
        
        # Return appropriate wrapper
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator

class SpanHelper:
    """Helper for working with spans"""
    
    @staticmethod
    def add_event(span_name: str, attributes: Dict[str, Any] = None):
        """Add an event to the current span"""
        current_span = trace.get_current_span()
        current_span.add_event(span_name, attributes=attributes or {})
    
    @staticmethod
    def set_attributes(attributes: Dict[str, Any]):
        """Set attributes on the current span"""
        current_span = trace.get_current_span()
        for key, value in attributes.items():
            current_span.set_attribute(key, value)
    
    @staticmethod
    def record_exception(exception: Exception):
        """Record an exception on the current span"""
        current_span = trace.get_current_span()
        current_span.record_exception(exception)