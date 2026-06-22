"""Tests for otel_metrics.metrics."""

from __future__ import annotations

import time
from enum import auto
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.metrics import MeterProvider

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


# --- Test fixtures -----------------------------------------------------------


class SampleMetrics(MetricName):
    REQUEST_COUNT = auto()
    REQUEST_DURATION_MS = auto()
    QUEUE_DEPTH = auto()
    PAYLOAD_SIZE = auto()


@pytest.fixture
def meter():
    provider = MeterProvider()
    return provider.get_meter("test-meter")


@pytest.fixture
def metrics(meter):
    return Metrics(meter)


@pytest.fixture
def prefixed_metrics(meter):
    return Metrics(meter, prefix="my-service")


# --- MetricName --------------------------------------------------------------


class TestMetricName:
    def test_subclass_values(self):
        assert SampleMetrics.REQUEST_COUNT == "request_count"
        assert SampleMetrics.REQUEST_DURATION_MS == "request_duration_ms"

    def test_is_str(self):
        assert isinstance(SampleMetrics.REQUEST_COUNT, str)


# --- MetricUnit --------------------------------------------------------------


class TestMetricUnit:
    def test_values(self):
        assert MetricUnit.SECONDS == "s"
        assert MetricUnit.MILLISECONDS == "ms"
        assert MetricUnit.BYTES == "By"
        assert MetricUnit.PERCENT == "%"


# --- Counter -----------------------------------------------------------------


class TestCounter:
    def test_inc_default(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT, description="Total requests")
        # Should not raise
        counter.inc()

    def test_inc_with_value(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT)
        counter.inc(5)

    def test_inc_with_attributes(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT)
        counter.inc(1, attributes={"method": "GET", "status": "200"})

    def test_inc_float_value(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT)
        counter.inc(1.5)

    def test_wraps_otel_counter(self):
        mock_counter = MagicMock()
        counter = Counter(mock_counter)
        counter.inc(3, attributes={"key": "val"})
        mock_counter.add.assert_called_once_with(3, attributes={"key": "val"})


# --- Histogram ---------------------------------------------------------------


class TestHistogram:
    def test_record(self, metrics):
        hist = metrics.histogram(
            SampleMetrics.PAYLOAD_SIZE, description="Payload bytes", unit=MetricUnit.BYTES
        )
        hist.record(1024)

    def test_record_with_attributes(self, metrics):
        hist = metrics.histogram(SampleMetrics.PAYLOAD_SIZE)
        hist.record(512, attributes={"content_type": "json"})

    def test_wraps_otel_histogram(self):
        mock_hist = MagicMock()
        hist = Histogram(mock_hist)
        hist.record(42, attributes={"a": "b"})
        mock_hist.record.assert_called_once_with(42, attributes={"a": "b"})


# --- Gauge -------------------------------------------------------------------


class TestGauge:
    def test_set_and_get(self, metrics):
        gauge = metrics.gauge(SampleMetrics.QUEUE_DEPTH, description="Queue depth")
        assert gauge.get() == 0.0
        gauge.set(10)
        assert gauge.get() == 10.0

    def test_set_float(self, metrics):
        gauge = metrics.gauge(SampleMetrics.QUEUE_DEPTH)
        gauge.set(3.14)
        assert gauge.get() == pytest.approx(3.14)

    def test_set_with_attributes(self, metrics):
        gauge = metrics.gauge(SampleMetrics.QUEUE_DEPTH)
        gauge.set(5, attributes={"queue": "default"})
        assert gauge.get() == 5.0

    def test_observe_callback(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        gauge.set(99, attributes={"env": "prod"})
        observations = gauge._observe(None)
        assert len(observations) == 1
        assert observations[0].value == 99
        assert observations[0].attributes == {"env": "prod"}


# --- TimestampNS -------------------------------------------------------------


class TestTimestampNS:
    def test_now_returns_instance(self):
        ts = TimestampNS.now()
        assert isinstance(ts, TimestampNS)
        assert ts.ns > 0

    def test_elapsed_ms_positive(self):
        ts = TimestampNS.now()
        time.sleep(0.01)  # 10ms
        elapsed = ts.elapsed_ms()
        assert elapsed >= 5  # at least ~5ms accounting for scheduler jitter

    def test_elapsed_ms_ordering(self):
        ts1 = TimestampNS.now()
        time.sleep(0.005)
        ts2 = TimestampNS.now()
        assert ts1.elapsed_ms() > ts2.elapsed_ms()


# --- Timer -------------------------------------------------------------------


class TestTimer:
    def test_record(self, metrics):
        timer = metrics.timer(SampleMetrics.REQUEST_DURATION_MS, description="Latency")
        start = TimestampNS.now()
        time.sleep(0.005)
        # Should not raise
        timer.record(start)

    def test_record_with_attributes(self, metrics):
        timer = metrics.timer(SampleMetrics.REQUEST_DURATION_MS)
        start = TimestampNS.now()
        timer.record(start, attributes={"endpoint": "/api"})

    def test_delegates_to_histogram(self):
        mock_hist = MagicMock()
        timer = Timer(mock_hist)
        start = TimestampNS(time.monotonic_ns() - 10_000_000)  # 10ms ago
        timer.record(start, attributes={"k": "v"})
        mock_hist.record.assert_called_once()
        args = mock_hist.record.call_args
        assert args[0][0] >= 5  # at least ~5ms
        assert args[0][1] == {"k": "v"}


# --- Metrics factory ---------------------------------------------------------


class TestMetricsFactory:
    def test_counter_creation(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT, description="desc")
        assert isinstance(counter, Counter)

    def test_histogram_creation(self, metrics):
        hist = metrics.histogram(SampleMetrics.PAYLOAD_SIZE, unit=MetricUnit.BYTES)
        assert isinstance(hist, Histogram)

    def test_gauge_creation(self, metrics):
        gauge = metrics.gauge(SampleMetrics.QUEUE_DEPTH, unit=MetricUnit.PERCENT)
        assert isinstance(gauge, Gauge)

    def test_timer_creation(self, metrics):
        timer = metrics.timer(SampleMetrics.REQUEST_DURATION_MS)
        assert isinstance(timer, Timer)

    def test_prefix_applied(self, prefixed_metrics):
        assert prefixed_metrics._full_name(SampleMetrics.REQUEST_COUNT) == "my-service.request_count"

    def test_no_prefix(self, metrics):
        assert metrics._full_name(SampleMetrics.REQUEST_COUNT) == "request_count"


# --- build_test_metrics ------------------------------------------------------


class TestBuildTestMetrics:
    def test_returns_metrics_instance(self):
        m = build_test_metrics()
        assert isinstance(m, Metrics)

    def test_instruments_work(self):
        m = build_test_metrics()
        counter = m.counter(SampleMetrics.REQUEST_COUNT)
        counter.inc()
        gauge = m.gauge(SampleMetrics.QUEUE_DEPTH)
        gauge.set(5)
        assert gauge.get() == 5.0


# --- setup_metrics -----------------------------------------------------------


class TestSetupMetrics:
    def test_grpc_setup(self):
        config = OtelConfig(
            endpoint="http://localhost:4317",
            service_name="test-svc",
            protocol=OtelProtocol.GRPC,
        )
        with patch("otel_metrics.metrics.atexit.register"):
            result = setup_metrics(config)
        assert isinstance(result, Metrics)
        assert result._prefix == "test-svc"

    def test_http_setup(self):
        config = OtelConfig(
            endpoint="http://localhost:4318",
            service_name="test-svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        with patch("otel_metrics.metrics.atexit.register"):
            result = setup_metrics(config)
        assert isinstance(result, Metrics)
        assert result._prefix == "test-svc"
