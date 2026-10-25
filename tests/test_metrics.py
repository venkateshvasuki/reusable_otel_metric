"""Tests for otel_metrics.metrics."""

from __future__ import annotations

import gzip
import threading
import time
from enum import auto
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, call, patch

import pytest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

import otel_metrics.metrics as metrics_module
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
    UpDownCounter,
    build_test_metrics,
    setup_metrics,
)


# --- Test fixtures -----------------------------------------------------------


class SampleMetrics(MetricName):
    REQUEST_COUNT = auto()
    REQUEST_DURATION_MS = auto()
    QUEUE_DEPTH = auto()
    PAYLOAD_SIZE = auto()
    ACTIVE_CONNECTIONS = auto()


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


@pytest.fixture
def metric_collector():
    """A local OTLP/HTTP receiver that records exported metrics."""
    received: list[tuple[dict[str, str], bytes]] = []
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            received.append((dict(self.headers), body))
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.received = received
    server.paths = paths
    yield server
    server.shutdown()


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
        counter = metrics.counter(
            SampleMetrics.REQUEST_COUNT, description="Total requests"
        )
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

    def test_negative_values_rejected(self):
        mock_counter = MagicMock()
        counter = Counter(mock_counter)
        with pytest.raises(ValueError, match="non-negative"):
            counter.inc(-1)
        assert mock_counter.add.call_args_list == []


# --- UpDownCounter -----------------------------------------------------------


class TestUpDownCounter:
    def test_inc_default(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.inc()

    def test_dec_default(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.dec()

    def test_inc_with_attributes(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.inc(2, attributes={"pool": "db"})

    def test_dec_with_attributes(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.dec(1, attributes={"pool": "db"})

    def test_inc_and_dec_float(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.inc(0.5)
        counter.dec(0.5)

    def test_zero_allowed(self, metrics):
        counter = metrics.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS)
        counter.inc(0)
        counter.dec(0)

    def test_negative_values_rejected(self):
        mock_counter = MagicMock()
        counter = UpDownCounter(mock_counter)
        with pytest.raises(ValueError, match="non-negative"):
            counter.inc(-1)
        with pytest.raises(ValueError, match="non-negative"):
            counter.dec(-1)
        assert mock_counter.add.call_args_list == []

    def test_wraps_otel_up_down_counter(self):
        mock_counter = MagicMock()
        counter = UpDownCounter(mock_counter)
        counter.inc(2, attributes={"key": "val"})
        counter.dec(3)
        assert mock_counter.add.call_args_list == [
            call(2, attributes={"key": "val"}),
            call(-3, attributes=None),
        ]


# --- Histogram ---------------------------------------------------------------


class TestHistogram:
    def test_record(self, metrics):
        hist = metrics.histogram(
            SampleMetrics.PAYLOAD_SIZE,
            description="Payload bytes",
            unit=MetricUnit.BYTES,
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

    def test_negative_values_rejected(self):
        mock_hist = MagicMock()
        hist = Histogram(mock_hist)
        with pytest.raises(ValueError, match="non-negative"):
            hist.record(-1)
        assert mock_hist.record.call_args_list == []


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
        assert gauge.get(attributes={"queue": "default"}) == 5.0
        assert gauge.get() == 0.0

    def test_observe_callback(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        gauge.set(99, attributes={"env": "prod"})
        observations = gauge._observe(None)
        assert len(observations) == 1
        assert observations[0].value == 99
        assert observations[0].attributes == {"env": "prod"}

    def test_emits_nothing_before_set(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        assert gauge._observe(None) == []

    def test_multiple_attribute_sets(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        gauge.set(1, attributes={"queue": "a"})
        gauge.set(2, attributes={"queue": "b"})
        observations = {
            observation.attributes["queue"]: observation.value
            for observation in gauge._observe(None)
        }
        assert observations == {"a": 1, "b": 2}

    def test_remove(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        gauge.set(1, attributes={"queue": "a"})
        assert gauge.remove(attributes={"queue": "a"}) is True
        assert gauge.remove(attributes={"queue": "a"}) is False
        assert gauge._observe(None) == []

    def test_clear(self, meter):
        gauge = Gauge(meter, "test.gauge", "desc", "")
        gauge.set(1)
        gauge.set(2, attributes={"queue": "a"})
        gauge.clear()
        assert gauge._observe(None) == []


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

    def test_time_context_manager_records(self):
        mock_hist = MagicMock()
        timer = Timer(mock_hist)
        with timer.time(attributes={"route": "/orders"}):
            pass
        mock_hist.record.assert_called_once()
        value, attributes = mock_hist.record.call_args.args
        assert value >= 0
        assert attributes == {"route": "/orders"}

    def test_time_context_manager_records_on_exception(self):
        mock_hist = MagicMock()
        timer = Timer(mock_hist)
        with pytest.raises(RuntimeError, match="boom"):
            with timer.time():
                raise RuntimeError("boom")
        mock_hist.record.assert_called_once()


# --- Metrics factory ---------------------------------------------------------


class TestMetricsFactory:
    def test_counter_creation(self, metrics):
        counter = metrics.counter(SampleMetrics.REQUEST_COUNT, description="desc")
        assert isinstance(counter, Counter)

    def test_up_down_counter_creation(self, metrics):
        counter = metrics.up_down_counter(
            SampleMetrics.ACTIVE_CONNECTIONS, description="Active"
        )
        assert isinstance(counter, UpDownCounter)

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
        assert (
            prefixed_metrics._full_name(SampleMetrics.REQUEST_COUNT)
            == "my-service.request_count"
        )

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
        m.up_down_counter(SampleMetrics.ACTIVE_CONNECTIONS).dec()


# --- setup_metrics -----------------------------------------------------------


class TestMetricsLifecycle:
    def test_context_manager_shuts_down(self):
        provider = MagicMock()
        metrics = Metrics(MagicMock(), provider=provider, owns_provider=True)
        with metrics as entered:
            assert entered is metrics
        provider.force_flush.assert_called_once()
        provider.shutdown.assert_called_once()

    def test_exception_still_shuts_down(self):
        provider = MagicMock()
        metrics = Metrics(MagicMock(), provider=provider, owns_provider=True)
        with pytest.raises(RuntimeError, match="boom"):
            with metrics:
                raise RuntimeError("boom")
        provider.shutdown.assert_called_once()

    def test_shutdown_is_idempotent(self):
        provider = MagicMock()
        metrics = Metrics(MagicMock(), provider=provider, owns_provider=True)
        metrics.shutdown()
        metrics.shutdown()
        provider.shutdown.assert_called_once()

    def test_externally_supplied_provider_is_flushed_but_not_shut_down(self):
        provider = MagicMock()
        metrics = Metrics(MagicMock(), provider=provider, owns_provider=False)
        with metrics:
            pass
        provider.force_flush.assert_called_once()
        provider.shutdown.assert_not_called()

    def test_force_flush(self):
        provider = MagicMock()
        provider.force_flush.return_value = True
        metrics = Metrics(MagicMock(), provider=provider, owns_provider=True)
        assert metrics.force_flush(500) is True
        provider.force_flush.assert_called_once_with(500)

    def test_force_flush_without_provider(self):
        assert Metrics(MagicMock()).force_flush() is True


class TestSetupMetrics:
    @staticmethod
    def _config(**overrides):
        fields = {
            "endpoint": "http://localhost:4318",
            "service_name": "test-svc",
            **overrides,
        }
        return OtelConfig(**fields)

    def test_grpc_setup(self):
        config = self._config(
            endpoint="http://localhost:4317", protocol=OtelProtocol.GRPC
        )
        with setup_metrics(config, set_global=False) as result:
            assert isinstance(result, Metrics)
            assert result._prefix == ""
        assert result._closed is True

    def test_http_setup(self):
        with setup_metrics(self._config(), set_global=False) as result:
            assert isinstance(result, Metrics)
            assert result._prefix == ""
        assert result._closed is True

    def test_metric_prefix_applied(self):
        with setup_metrics(
            self._config(metric_prefix="myapp"), set_global=False
        ) as result:
            assert result._prefix == "myapp"
            assert (
                result._full_name(SampleMetrics.REQUEST_COUNT) == "myapp.request_count"
            )

    def test_tags_included_in_resource(self):
        config = self._config(tags="env=prod,region=eu")
        with (
            patch(
                "otel_metrics.metrics.Resource.create",
                side_effect=Resource.create,
            ) as create,
            setup_metrics(config, set_global=False),
        ):
            pass
        assert create.call_args.args[0] == {
            "env": "prod",
            "region": "eu",
            "service.name": "test-svc",
        }

    def test_reuses_active_metrics(self, monkeypatch):
        active = MagicMock(spec=Metrics)
        monkeypatch.setattr(metrics_module, "_active_metrics", active)
        assert setup_metrics(self._config()) is active

    def test_set_global_false_builds_a_new_runtime(self, monkeypatch):
        active = MagicMock(spec=Metrics)
        monkeypatch.setattr(metrics_module, "_active_metrics", active)
        with setup_metrics(self._config(), set_global=False) as result:
            assert result is not active

    def test_injected_provider_is_not_owned(self):
        provider = MeterProvider()
        metrics = setup_metrics(
            self._config(), meter_provider=provider, set_global=False
        )
        assert metrics._provider is provider
        assert metrics._owns_provider is False
        metrics.shutdown()
        provider.shutdown()


# --- exported metrics --------------------------------------------------------


class TestMetricsExport:
    def test_counter_reaches_collector(self, metric_collector):
        config = OtelConfig(
            endpoint=f"http://127.0.0.1:{metric_collector.server_address[1]}",
            service_name="metric-svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
            metric_prefix="myapp",
        )

        with setup_metrics(config, set_global=False) as metrics:
            metrics.counter(SampleMetrics.REQUEST_COUNT).inc(
                3, attributes={"route": "/orders"}
            )
            assert metrics.force_flush()

        assert metric_collector.received
        assert metric_collector.paths[0] == "/v1/metrics"

        request = ExportMetricsServiceRequest()
        request.ParseFromString(metric_collector.received[0][1])
        resource_metrics = request.resource_metrics[0]
        assert any(
            attribute.key == "service.name"
            and attribute.value.string_value == "metric-svc"
            for attribute in resource_metrics.resource.attributes
        )

        metric = resource_metrics.scope_metrics[0].metrics[0]
        assert metric.name == "myapp.request_count"
        assert metric.sum.data_points[0].as_int == 3
        assert metric.sum.data_points[0].attributes[0].key == "route"
