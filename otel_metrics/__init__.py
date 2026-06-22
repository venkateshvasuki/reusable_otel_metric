"""otel_metrics - A reusable OpenTelemetry metrics library.

Provides simplified wrappers around OpenTelemetry metric instruments
(Counter, Histogram, Gauge, Timer) with a factory pattern and
configuration via pydantic-settings.

Usage:
    from otel_metrics import OtelConfig, setup_metrics, MetricName

    class MyMetrics(MetricName):
        REQUEST_COUNT = auto()
        REQUEST_DURATION_MS = auto()

    config = OtelConfig(endpoint="http://localhost:4317", service_name="my-service")
    metrics = setup_metrics(config)

    counter = metrics.counter(MyMetrics.REQUEST_COUNT, description="Total requests")
    counter.inc()
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
    "setup_metrics",
]
