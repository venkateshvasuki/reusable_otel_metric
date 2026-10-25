"""OpenTelemetry tracing pipeline setup."""

from __future__ import annotations

import atexit
import logging
from typing import Protocol

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

__all__ = [
    "Instrumentor",
    "build_span_exporter",
    "build_test_tracer",
    "force_flush_telemetry",
    "setup_telemetry",
    "shutdown_telemetry",
]


class Instrumentor(Protocol):
    """Structural type for the instrumentors attached to the provider."""

    def instrument(self, *, tracer_provider: TracerProvider) -> None: ...


def build_span_exporter(config: OtelConfig) -> SpanExporter:
    """Build the OTLP span exporter described by the config."""
    endpoint = config.resolved_traces_endpoint()
    logger.info("OTel: exporting traces via %s to %s", config.protocol, endpoint)

    if config.protocol == OtelProtocol.GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcSpanExporter,
        )

        return GrpcSpanExporter(endpoint=endpoint, headers=config.resolved_headers())

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpSpanExporter,
    )

    return HttpSpanExporter(endpoint=endpoint, headers=config.resolved_headers())


_tracer_provider: TracerProvider | None = None


def force_flush_telemetry(timeout_millis: int = 30_000) -> bool:
    """Flush pending spans on the provider configured by this module."""
    if _tracer_provider is None:
        return True
    return _tracer_provider.force_flush(timeout_millis)


def shutdown_telemetry() -> None:
    """Flush and shut down the provider configured by this module."""
    global _tracer_provider

    provider = _tracer_provider
    _tracer_provider = None
    if provider is None:
        return
    provider.force_flush()
    provider.shutdown()


def setup_telemetry(
    config: OtelConfig,
    *instrumentors: Instrumentor,
    tracer_provider: TracerProvider | None = None,
    set_global: bool = True,
    console_spans: bool = False,
) -> trace.Tracer:
    """Bootstrap the OTel tracing pipeline and return a Tracer.

    Reuses an existing provider when one was already configured with
    ``set_global=True``. Pass ``set_global=False`` for isolated setups, or
    inject ``tracer_provider`` to manage the provider yourself.

    Instrumentors are attached to the provider built or supplied here. The
    ``console_spans`` option adds a console exporter for local debugging.
    """
    global _tracer_provider

    if tracer_provider is None and set_global and _tracer_provider is not None:
        logger.warning("OTel tracing already configured; reusing the existing provider")
        provider = _tracer_provider
    elif tracer_provider is not None:
        provider = tracer_provider
    else:
        provider = TracerProvider(
            resource=Resource.create(
                {**(config.tags or {}), SERVICE_NAME: config.service_name}
            )
        )
        provider.add_span_processor(BatchSpanProcessor(build_span_exporter(config)))
        if console_spans:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        atexit.register(provider.shutdown)

    if set_global:
        if provider is not _tracer_provider:
            trace.set_tracer_provider(provider)
        _tracer_provider = provider

    for instrumentor in instrumentors:
        instrumentor.instrument(tracer_provider=provider)

    return provider.get_tracer(config.service_name)


def build_test_tracer() -> trace.Tracer:
    """Return a non-exporting Tracer for unit testing."""
    return TracerProvider().get_tracer("test")
