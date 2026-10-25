"""Tests for otel_metrics.telemetry."""

import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from opentelemetry.instrumentation.instrumentor import BaseInstrumentor

import otel_metrics.telemetry as telemetry_module
from otel_metrics.config import OtelConfig, OtelProtocol
from otel_metrics.telemetry import (
    build_span_exporter,
    build_test_tracer,
    force_flush_telemetry,
    setup_telemetry,
    shutdown_telemetry,
)


def _config(**overrides) -> OtelConfig:
    return OtelConfig(
        **{
            "endpoint": "http://localhost:4318",
            "service_name": "trace-svc",
            "protocol": OtelProtocol.HTTP_PROTOBUF,
            **overrides,
        }
    )


class TestBuildSpanExporter:
    def test_http_protocol_selects_http_exporter(self):
        assert isinstance(build_span_exporter(_config()), HttpSpanExporter)

    def test_grpc_protocol_selects_grpc_exporter(self):
        config = _config(protocol=OtelProtocol.GRPC, endpoint="http://localhost:4317")
        assert isinstance(build_span_exporter(config), GrpcSpanExporter)

    def test_http_uses_traces_endpoint(self):
        exporter = build_span_exporter(_config())
        assert exporter._endpoint == "http://localhost:4318/v1/traces"


@pytest.fixture
def collector():
    """A local OTLP/HTTP receiver that records what the exporter sent."""
    received: list[tuple[dict[str, str], bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            received.append((dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.received = received
    yield server
    server.shutdown()


def _export_one_span(collector, **config_overrides) -> tuple[dict[str, str], bytes]:
    """Send a single span through a real exporter and return what arrived."""
    config = _config(
        endpoint=f"http://127.0.0.1:{collector.server_address[1]}", **config_overrides
    )
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: config.service_name})
    )
    provider.add_span_processor(SimpleSpanProcessor(build_span_exporter(config)))
    provider.get_tracer("test").start_span("unit").end()
    provider.force_flush()
    assert collector.received, "nothing reached the collector"
    return collector.received[0]


class TestExportedRequest:
    def test_headers_reach_the_collector(self, collector):
        headers, _ = _export_one_span(collector, headers={"x-api-key": "secret"})
        assert headers["x-api-key"] == "secret"

    def test_multiple_headers_reach_the_collector(self, collector):
        headers, _ = _export_one_span(
            collector, headers="Authorization=Bearer tok, x-bt-parent=project_id:123"
        )
        assert headers["authorization"] == "Bearer tok"
        assert headers["x-bt-parent"] == "project_id:123"

    def test_no_headers_configured_still_exports(self, collector):
        headers, _ = _export_one_span(collector)
        assert "x-api-key" not in headers

    def test_resource_carries_service_name(self, collector):
        _, body = _export_one_span(collector)
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        attributes = request.resource_spans[0].resource.attributes
        assert any(
            kv.key == SERVICE_NAME and kv.value.string_value == "trace-svc"
            for kv in attributes
        )


class TestBuildTestTracer:
    def test_returns_recording_tracer(self):
        tracer = build_test_tracer()
        with tracer.start_as_current_span("unit") as span:
            assert span.is_recording()

    def test_does_not_touch_the_global_provider(self):
        from opentelemetry import trace

        before = trace.get_tracer_provider()
        build_test_tracer()
        assert trace.get_tracer_provider() is before


class _RecordingInstrumentor(BaseInstrumentor):
    """Captures the provider it was handed. BaseInstrumentor is a per-class
    singleton, so each test needs its own subclass."""

    calls: list = []

    def instrumentation_dependencies(self):
        return ()

    def _instrument(self, **kwargs):
        type(self).calls.append(kwargs.get("tracer_provider"))

    def _uninstrument(self, **kwargs):
        pass


def _fresh(name: str) -> BaseInstrumentor:
    """A one-shot instrumentor class -- sidesteps the singleton and the
    already-instrumented guard that would skip a second call."""
    cls = type(name, (_RecordingInstrumentor,), {"calls": []})
    return cls()


class TestResourceTags:
    def test_tags_reach_the_provider_resource(self, collector):
        instrumentor = _fresh("TagInstrumentor")
        config = _config(
            endpoint=f"http://127.0.0.1:{collector.server_address[1]}",
            tags="env=prod,region=eu",
        )

        setup_telemetry(config, instrumentor, set_global=False)

        provider = type(instrumentor).calls[0]
        try:
            attributes = provider.resource.attributes
            assert attributes["env"] == "prod"
            assert attributes["region"] == "eu"
        finally:
            provider.shutdown()

    def test_explicit_service_name_wins_over_a_tag(self, collector):
        instrumentor = _fresh("OverrideInstrumentor")
        config = _config(
            endpoint=f"http://127.0.0.1:{collector.server_address[1]}",
            tags="service.name=sneaky",
        )

        setup_telemetry(config, instrumentor, set_global=False)

        provider = type(instrumentor).calls[0]
        try:
            assert provider.resource.attributes[SERVICE_NAME] == "trace-svc"
        finally:
            provider.shutdown()


class TestInstrumentors:
    def test_instrumentor_receives_the_provider_built_here(self, collector):
        instrumentor = _fresh("OneInstrumentor")
        config = _config(endpoint=f"http://127.0.0.1:{collector.server_address[1]}")

        setup_telemetry(config, instrumentor, set_global=False)

        provider = type(instrumentor).calls[0]
        try:
            assert provider is not None
            assert provider.resource.attributes[SERVICE_NAME] == "trace-svc"
        finally:
            provider.shutdown()

    def test_every_instrumentor_is_attached(self, collector):
        first, second = _fresh("FirstInstrumentor"), _fresh("SecondInstrumentor")
        config = _config(endpoint=f"http://127.0.0.1:{collector.server_address[1]}")

        setup_telemetry(config, first, second, set_global=False)

        provider = type(first).calls[0]
        try:
            assert len(type(first).calls) == 1
            assert len(type(second).calls) == 1
        finally:
            provider.shutdown()

    def test_default_attaches_nothing(self, collector):
        instrumentor = _fresh("UnusedInstrumentor")
        config = _config(endpoint=f"http://127.0.0.1:{collector.server_address[1]}")

        setup_telemetry(config, set_global=False)

        assert type(instrumentor).calls == []


class TestTelemetryLifecycle:
    def test_force_flush_without_provider(self, monkeypatch):
        monkeypatch.setattr(telemetry_module, "_tracer_provider", None)
        assert force_flush_telemetry() is True

    def test_shutdown_uses_active_provider(self, monkeypatch):
        provider = MagicMock()
        monkeypatch.setattr(telemetry_module, "_tracer_provider", provider)

        shutdown_telemetry()

        provider.force_flush.assert_called_once()
        provider.shutdown.assert_called_once()
        assert telemetry_module._tracer_provider is None

    def test_reuses_active_provider(self, monkeypatch):
        provider = MagicMock()
        monkeypatch.setattr(telemetry_module, "_tracer_provider", provider)

        setup_telemetry(_config())

        provider.get_tracer.assert_called_once_with("trace-svc")
