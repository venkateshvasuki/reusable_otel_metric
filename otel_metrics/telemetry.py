"""OpenTelemetry tracing pipeline setup."""

from __future__ import annotations

import atexit
import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

from otel_metrics.config import OtelConfig, OtelProtocol

logger = logging.getLogger(__name__)

__all__ = ["build_span_exporter", "build_test_tracer", "setup_telemetry"]


def build_span_exporter(config: OtelConfig) -> SpanExporter:
    """Build the OTLP span exporter described by the config."""
    endpoint = config.resolved_traces_endpoint()
    logger.info("OTel: exporting traces via %s to %s", config.protocol, endpoint)

    if config.protocol == OtelProtocol.GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcSpanExporter,
        )

        return GrpcSpanExporter(endpoint=endpoint, headers=config.headers)

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpSpanExporter,
    )

    return HttpSpanExporter(endpoint=endpoint, headers=config.headers)


def setup_telemetry(config: OtelConfig) -> trace.Tracer:
    """Bootstrap the OTel tracing pipeline and return a Tracer.

    Registers an atexit hook to flush pending spans on shutdown.
    """
    exporter = build_span_exporter(config)
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: config.service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))

    if logger.isEnabledFor(logging.DEBUG):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    atexit.register(provider.shutdown)
    trace.set_tracer_provider(provider)
    return trace.get_tracer(config.service_name)


def build_test_tracer() -> trace.Tracer:
    """Return a non-exporting Tracer for unit testing."""
    return TracerProvider().get_tracer("test")
