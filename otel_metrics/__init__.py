"""otel_metrics - A reusable OpenTelemetry metrics and tracing library.

Provides simplified wrappers around OpenTelemetry metric instruments
(Counter, Histogram, Gauge, Timer) with a factory pattern, an OTLP
tracing pipeline, and configuration via pydantic-settings.

Usage:
    from otel_metrics import OtelConfig, setup_metrics, MetricName

    class MyMetrics(MetricName):
        REQUEST_COUNT = auto()
        REQUEST_DURATION_MS = auto()

    config = OtelConfig(endpoint="http://localhost:4317", service_name="my-service")
    metrics = setup_metrics(config)

    counter = metrics.counter(MyMetrics.REQUEST_COUNT, description="Total requests")
    counter.inc()

    tracer = setup_telemetry(config)
"""

from otel_metrics.config import OtelConfig, OtelProtocol
from otel_metrics.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricName,
    MetricUnit,
    Metrics,
    Timer,
    TimestampNS,
    build_test_metrics,
    setup_metrics,
)
from otel_metrics.telemetry import build_test_tracer, setup_telemetry

__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricName",
    "MetricUnit",
    "Metrics",
    "OtelConfig",
    "OtelProtocol",
    "Timer",
    "TimestampNS",
    "build_test_metrics",
    "build_test_tracer",
    "setup_metrics",
    "setup_telemetry",
]
