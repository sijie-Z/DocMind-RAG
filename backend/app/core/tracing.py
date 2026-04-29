import logging
import os
from fastapi import FastAPI

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)

def setup_opentelemetry(app: FastAPI, service_name: str = "rag_backend"):
    """
    配置并启动 OpenTelemetry 链路追踪
    """
    enable_tracing = os.getenv("ENABLE_TRACING", "false").lower() == "true"
    if not enable_tracing:
        logger.info("OpenTelemetry tracing is disabled.")
        return

    otlp_endpoint = os.getenv("OTLP_ENDPOINT", "http://localhost:4317")
    logger.info(f"Initializing OpenTelemetry tracing (endpoint: {otlp_endpoint})...")

    # 配置 Resource
    resource = Resource.create({"service.name": service_name})

    # 配置 Provider
    provider = TracerProvider(resource=resource)
    
    # 配置 Exporter
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # 设置全局 TracerProvider
    trace.set_tracer_provider(provider)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
    logger.info("OpenTelemetry tracing setup complete.")
