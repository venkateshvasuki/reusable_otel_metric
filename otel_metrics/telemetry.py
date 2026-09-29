import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter

from otel_metrics.config import OtelConfig, OtelProtocol

logger = logging.getLogger(__name__)

__all__ = ["setup_telemetry"]


def setup_telemetry(config: OtelConfig) -> trace.Tracer:
    import atexit

    provider = TracerProvider()
    atexit.register(provider.shutdown)

    endpoint = config.resolved_traces_endpoint()
    logger.info("OTel: exporting traces via %s to %s", config.protocol, endpoint)
    if config.protocol == OtelProtocol.GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcSpanExporter,
        )

        exporter: SpanExporter = GrpcSpanExporter(endpoint=endpoint)
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpSpanExporter,
        )

        exporter = HttpSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    if logger.isEnabledFor(logging.DEBUG):
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    return trace.get_tracer(config.service_name)
