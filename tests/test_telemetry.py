"""Tests for otel_metrics.telemetry."""

import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

from otel_metrics.config import OtelConfig, OtelProtocol
from otel_metrics.telemetry import build_span_exporter, build_test_tracer


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
