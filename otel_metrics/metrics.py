"""OpenTelemetry metrics instruments and factory."""

from __future__ import annotations

import atexit
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.util.types import Attributes

from otel_metrics.config import OtelConfig, OtelProtocol

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter as OtelCounter, Histogram as OtelHistogram, Meter

logger = logging.getLogger(__name__)


class MetricUnit(StrEnum):
    """Standard metric units."""

    SECONDS = "s"
    MILLISECONDS = "ms"
    BYTES = "By"
    PERCENT = "%"


class MetricName(StrEnum):
    """Base class for metric name enumerations.

    Subclass this in your application to define concrete metric names:

        class MyMetrics(MetricName):
            REQUEST_COUNT = auto()
            REQUEST_DURATION_MS = auto()
    """


class Counter:
    """Wrapper around an OpenTelemetry Counter instrument."""

    def __init__(self, counter: OtelCounter) -> None:
        self._counter = counter

    def inc(self, value: int | float = 1, attributes: Attributes = None) -> None:
        self._counter.add(value, attributes=attributes)


class Histogram:
    """Wrapper around an OpenTelemetry Histogram instrument."""

    def __init__(self, histogram: OtelHistogram) -> None:
        self._histogram = histogram

    def record(self, value: int | float, attributes: Attributes = None) -> None:
        self._histogram.record(value, attributes=attributes)


class Gauge:
    """Observable gauge backed by a simple set/get interface."""

    def __init__(self, meter: Meter, name: str, description: str, unit: str) -> None:
        self._value: float = 0.0
        self._attributes: Attributes = None
        meter.create_observable_gauge(
            name=name,
            description=description,
            unit=unit,
            callbacks=[self._observe],
        )

    def _observe(self, _options: CallbackOptions) -> list[Observation]:
        return [Observation(self._value, self._attributes)]

    def set(self, value: int | float, attributes: Attributes = None) -> None:
        self._value = float(value)
        self._attributes = attributes

    def get(self) -> float:
        return self._value


class TimestampNS:
    """Monotonic nanosecond timestamp for measuring elapsed time."""

    def __init__(self, ns: int) -> None:
        self.ns = ns

    @classmethod
    def now(cls) -> TimestampNS:
        return cls(time.monotonic_ns())

    def elapsed_ms(self) -> float:
        return (time.monotonic_ns() - self.ns) / 1_000_000


class Timer:
    """Records elapsed time (in milliseconds) into a Histogram."""

    def __init__(self, histogram: Histogram) -> None:
        self._histogram = histogram

    def record(self, start_time: TimestampNS, attributes: Attributes = None) -> None:
        self._histogram.record(start_time.elapsed_ms(), attributes)


class Metrics:
    """Factory for creating metric instruments with an optional name prefix."""

    def __init__(self, meter: Meter, prefix: str = "") -> None:
        self._meter = meter
        self._prefix = prefix

    def _full_name(self, name: MetricName) -> str:
        return f"{self._prefix}.{name}" if self._prefix else str(name)

    def counter(self, name: MetricName, description: str = "") -> Counter:
        return Counter(
            self._meter.create_counter(name=self._full_name(name), description=description)
        )

    def histogram(
        self,
        name: MetricName,
        description: str = "",
        unit: MetricUnit | str | None = None,
    ) -> Histogram:
        return Histogram(
            self._meter.create_histogram(
                name=self._full_name(name),
                description=description,
                unit=str(unit) if unit else "",
            )
        )

    def gauge(
        self,
        name: MetricName,
        description: str = "",
        unit: MetricUnit | str | None = None,
    ) -> Gauge:
        return Gauge(
            self._meter, self._full_name(name), description, str(unit) if unit else ""
        )

    def timer(self, name: MetricName, description: str = "") -> Timer:
        return Timer(self.histogram(name, description=description, unit=MetricUnit.MILLISECONDS))


def setup_metrics(config: OtelConfig) -> Metrics:
    """Bootstrap the OTel metrics pipeline and return a Metrics factory.

    Registers an atexit hook to flush metrics on shutdown.
    """
    endpoint = config.resolved_metrics_endpoint()
    logger.info("OTel: exporting metrics via %s to %s", config.protocol, endpoint)

    exporter: MetricExporter
    if config.protocol == OtelProtocol.GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter as GrpcExporter,
        )

        exporter = GrpcExporter(endpoint=endpoint)
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HttpExporter,
        )

        exporter = HttpExporter(endpoint=endpoint)

    reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(metric_readers=[reader])
    atexit.register(provider.shutdown)
    otel_metrics.set_meter_provider(provider)

    meter = otel_metrics.get_meter(config.service_name)
    return Metrics(meter, prefix=config.service_name)


def build_test_metrics() -> Metrics:
    """Return a no-op Metrics instance for unit testing."""
    provider = MeterProvider()
    meter = provider.get_meter("test")
    return Metrics(meter)
