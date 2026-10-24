"""OpenTelemetry 链路追踪接线（ENABLE_TRACING 显式开关，默认关闭）。

Deprecated candidate（issue #510 第三步待仓库所有者批准）：保留为可选接入点，
未获批前不删除、不改默认行为。当前无 collector/查询端，启用需要自担运维成本。
"""

import logging
import os

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def setup_opentelemetry(app: FastAPI, service_name: str = "rag_backend"):
    """
    配置并启动 OpenTelemetry 链路追踪
    """
    enable_tracing = os.getenv("ENABLE_TRACING", "false").lower() == "true"
    if not enable_tracing:
        logger.info("OpenTelemetry tracing is disabled.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry packages not installed; tracing disabled.")
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
