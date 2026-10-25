"""OpenTelemetry metrics instruments and factory."""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Self

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.util.types import AttributeValue, Attributes

from otel_metrics.config import OtelConfig, OtelProtocol

if TYPE_CHECKING:
    from opentelemetry.metrics import (
        Counter as OtelCounter,
        Histogram as OtelHistogram,
        Meter,
        UpDownCounter as OtelUpDownCounter,
    )

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


def _require_non_negative(value: int | float, instrument: str) -> None:
    if value < 0:
        raise ValueError(f"{instrument} values must be non-negative")


class Counter:
    """Wrapper around an OpenTelemetry Counter instrument."""

    def __init__(self, counter: OtelCounter) -> None:
        self._counter = counter

    def inc(self, value: int | float = 1, attributes: Attributes = None) -> None:
        _require_non_negative(value, "Counter")
        self._counter.add(value, attributes=attributes)


class UpDownCounter:
    """Wrapper around an OpenTelemetry UpDownCounter instrument."""

    def __init__(self, counter: OtelUpDownCounter) -> None:
        self._counter = counter

    def inc(self, value: int | float = 1, attributes: Attributes = None) -> None:
        _require_non_negative(value, "UpDownCounter")
        self._counter.add(value, attributes=attributes)

    def dec(self, value: int | float = 1, attributes: Attributes = None) -> None:
        _require_non_negative(value, "UpDownCounter")
        self._counter.add(-value, attributes=attributes)


class Histogram:
    """Wrapper around an OpenTelemetry Histogram instrument."""

    def __init__(self, histogram: OtelHistogram) -> None:
        self._histogram = histogram

    def record(self, value: int | float, attributes: Attributes = None) -> None:
        _require_non_negative(value, "Histogram")
        self._histogram.record(value, attributes=attributes)


AttributeKey = tuple[tuple[str, object], ...]


def _freeze_attribute(value: AttributeValue) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_attribute(item) for item in value)
    return value


def _attribute_key(attributes: Attributes) -> AttributeKey:
    if not attributes:
        return ()
    return tuple(
        sorted((key, _freeze_attribute(value)) for key, value in attributes.items())
    )


class Gauge:
    """Observable gauge holding one value per attribute set."""

    def __init__(self, meter: Meter, name: str, description: str, unit: str) -> None:
        self._lock = threading.Lock()
        self._observations: dict[
            AttributeKey, tuple[float, dict[str, AttributeValue] | None]
        ] = {}
        meter.create_observable_gauge(
            name=name,
            description=description,
            unit=unit,
            callbacks=[self._observe],
        )

    def _observe(self, _options: CallbackOptions) -> list[Observation]:
        with self._lock:
            snapshot = list(self._observations.values())
        return [
            Observation(value, dict(attributes) if attributes is not None else None)
            for value, attributes in snapshot
        ]

    def set(self, value: int | float, attributes: Attributes = None) -> None:
        key = _attribute_key(attributes)
        stored = dict(attributes) if attributes else None
        with self._lock:
            self._observations[key] = (float(value), stored)

    def get(self, attributes: Attributes = None) -> float:
        key = _attribute_key(attributes)
        with self._lock:
            observation = self._observations.get(key)
        return observation[0] if observation is not None else 0.0

    def remove(self, attributes: Attributes = None) -> bool:
        key = _attribute_key(attributes)
        with self._lock:
            return self._observations.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._observations.clear()


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

    @contextmanager
    def time(self, attributes: Attributes = None) -> Iterator[None]:
        start_time = TimestampNS.now()
        try:
            yield
        finally:
            self.record(start_time, attributes)


class Metrics:
    """Factory for creating metric instruments with an optional name prefix.

    The instance returned by :func:`setup_metrics` can be used as a context
    manager so its provider is flushed and shut down on exit.
    """

    def __init__(
        self,
        meter: Meter,
        prefix: str = "",
        *,
        provider: MeterProvider | None = None,
        owns_provider: bool = False,
    ) -> None:
        self._meter = meter
        self._prefix = prefix
        self._provider = provider
        self._owns_provider = owns_provider
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        if self._provider is None or self._closed:
            return True
        return self._provider.force_flush(timeout_millis)

    def shutdown(self) -> None:
        """Flush and shut down the owned provider. Safe to call more than once."""
        global _active_metrics

        if self._closed:
            return
        self._closed = True
        try:
            if self._provider is not None:
                try:
                    self._provider.force_flush()
                finally:
                    if self._owns_provider:
                        self._provider.shutdown()
        finally:
            if _active_metrics is self:
                _active_metrics = None

    def _full_name(self, name: MetricName) -> str:
        return f"{self._prefix}.{name}" if self._prefix else str(name)

    def counter(self, name: MetricName, description: str = "") -> Counter:
        return Counter(
            self._meter.create_counter(
                name=self._full_name(name), description=description
            )
        )

    def up_down_counter(
        self,
        name: MetricName,
        description: str = "",
        unit: MetricUnit | str | None = None,
    ) -> UpDownCounter:
        return UpDownCounter(
            self._meter.create_up_down_counter(
                name=self._full_name(name),
                description=description,
                unit=str(unit) if unit else "",
            )
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
        return Timer(
            self.histogram(name, description=description, unit=MetricUnit.MILLISECONDS)
        )


_active_metrics: Metrics | None = None


def _build_metric_exporter(config: OtelConfig) -> MetricExporter:
    endpoint = config.resolved_metrics_endpoint()
    logger.info("OTel: exporting metrics via %s to %s", config.protocol, endpoint)

    if config.protocol == OtelProtocol.GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter as GrpcExporter,
        )

        return GrpcExporter(endpoint=endpoint, headers=config.resolved_headers())

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HttpExporter,
    )

    return HttpExporter(endpoint=endpoint, headers=config.resolved_headers())


def setup_metrics(
    config: OtelConfig,
    *,
    meter_provider: MeterProvider | None = None,
    set_global: bool = True,
) -> Metrics:
    """Bootstrap the OTel metrics pipeline and return a Metrics factory.

    Reuses an existing provider when one was already configured with
    ``set_global=True``. Pass ``set_global=False`` for isolated or repeatable
    setups, or inject ``meter_provider`` to manage the provider yourself.
    """
    global _active_metrics

    if meter_provider is None and set_global and _active_metrics is not None:
        logger.warning("OTel metrics already configured; reusing the existing provider")
        return _active_metrics

    owns_provider = meter_provider is None
    if meter_provider is None:
        reader = PeriodicExportingMetricReader(_build_metric_exporter(config))
        meter_provider = MeterProvider(
            metric_readers=[reader],
            resource=Resource.create(
                {**(config.tags or {}), SERVICE_NAME: config.service_name}
            ),
        )

    if set_global:
        otel_metrics.set_meter_provider(meter_provider)

    metrics = Metrics(
        meter_provider.get_meter(config.service_name),
        prefix=config.metric_prefix or "",
        provider=meter_provider,
        owns_provider=owns_provider,
    )
    if owns_provider:
        atexit.register(metrics.shutdown)
    if set_global:
        _active_metrics = metrics
    return metrics


def build_test_metrics() -> Metrics:
    """Return a no-op Metrics instance for unit testing."""
    provider = MeterProvider()
    meter = provider.get_meter("test")
    return Metrics(meter)
