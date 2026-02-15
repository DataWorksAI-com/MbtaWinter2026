from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.semconv.resource import ResourceAttributes
import logging
import os

logger = logging.getLogger(__name__)

_tracer_provider = None
_meter_provider = None

def setup_otel(service_name: str, otel_endpoint: str = None):
    """Setup OpenTelemetry tracing and metrics"""
    global _tracer_provider, _meter_provider
    
    if otel_endpoint is None:
        otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    
    try:
        resource = Resource.create({
            ResourceAttributes.SERVICE_NAME: service_name,
            ResourceAttributes.SERVICE_VERSION: "1.0.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "production"
        })
        
        # Setup Tracing
        _tracer_provider = TracerProvider(resource=resource)
        otlp_span_exporter = OTLPSpanExporter(endpoint=otel_endpoint, insecure=True)
        
        # Use SimpleSpanProcessor for immediate export (better for debugging)
        span_processor = SimpleSpanProcessor(otlp_span_exporter)
        _tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(_tracer_provider)
        
        # Setup Metrics
        otlp_metric_exporter = OTLPMetricExporter(endpoint=otel_endpoint, insecure=True)
        metric_reader = PeriodicExportingMetricReader(
            otlp_metric_exporter, 
            export_interval_millis=10000
        )
        _meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(_meter_provider)
        
        logger.info(f"✅ OpenTelemetry initialized for {service_name}")
        logger.info(f"   Tracer provider: {_tracer_provider}")
        logger.info(f"   OTLP endpoint: {otel_endpoint}")
        
    except Exception as e:
        logger.error(f"⚠️ OpenTelemetry setup failed: {e}", exc_info=True)
        _tracer_provider = None
        _meter_provider = None

def get_tracer(name: str):
    """Get a tracer instance"""
    if _tracer_provider is None:
        logger.warning(f"⚠️ Tracer requested but provider is None!")
        return None
    tracer = trace.get_tracer(name)
    logger.info(f"✅ Tracer created for {name}: {tracer}")
    return tracer

def get_meter(name: str):
    """Get a meter instance"""
    if _meter_provider is None:
        return None
    return metrics.get_meter(name)