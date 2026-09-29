"""Tests for otel_metrics.telemetry."""

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)

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

    def test_headers_forwarded(self):
        exporter = build_span_exporter(_config(headers={"x-api-key": "secret"}))
        assert exporter._session.headers["x-api-key"] == "secret"

    def test_multiple_headers_forwarded(self):
        exporter = build_span_exporter(
            _config(headers="Authorization=Bearer tok, x-bt-parent=project_id:123")
        )
        assert exporter._session.headers["authorization"] == "Bearer tok"
        assert exporter._session.headers["x-bt-parent"] == "project_id:123"

    def test_no_headers_is_accepted(self):
        exporter = build_span_exporter(_config())
        assert "x-api-key" not in exporter._session.headers


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
