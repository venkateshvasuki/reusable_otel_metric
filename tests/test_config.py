"""Tests for otel_metrics.config."""

import pytest
from pydantic import ValidationError

from otel_metrics.config import OtelConfig, OtelProtocol


class TestOtelProtocol:
    def test_grpc_value(self):
        assert OtelProtocol.GRPC == "grpc"

    def test_http_protobuf_value(self):
        assert OtelProtocol.HTTP_PROTOBUF == "http/protobuf"


class TestOtelConfigConstruction:
    def test_minimal_valid_config(self):
        config = OtelConfig(endpoint="http://localhost:4317", service_name="test-svc")
        assert config.endpoint == "http://localhost:4317"
        assert config.service_name == "test-svc"
        assert config.protocol == OtelProtocol.GRPC

    def test_explicit_protocol_http(self):
        config = OtelConfig(
            endpoint="http://localhost:4318",
            service_name="test-svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        assert config.protocol == OtelProtocol.HTTP_PROTOBUF

    def test_service_name_alias_camelcase(self):
        config = OtelConfig(serviceName="my-svc", endpoint="http://localhost:4317")
        assert config.service_name == "my-svc"

    def test_extra_fields_ignored(self):
        config = OtelConfig(
            endpoint="http://localhost:4317",
            service_name="svc",
            unknown_field="ignored",
        )
        assert config.endpoint == "http://localhost:4317"


class TestOtelConfigValidation:
    def test_blank_endpoint_rejected(self):
        with pytest.raises(ValidationError, match="must not be blank"):
            OtelConfig(endpoint="   ", service_name="svc")

    def test_blank_service_name_rejected(self):
        with pytest.raises(ValidationError, match="must not be blank"):
            OtelConfig(endpoint="http://localhost:4317", service_name="  ")

    def test_empty_endpoint_rejected(self):
        with pytest.raises(ValidationError, match="must not be blank"):
            OtelConfig(endpoint="", service_name="svc")

    def test_endpoint_stripped(self):
        config = OtelConfig(endpoint="  http://host:4317  ", service_name="svc")
        assert config.endpoint == "http://host:4317"

    def test_service_name_stripped(self):
        config = OtelConfig(endpoint="http://host:4317", service_name="  my-svc  ")
        assert config.service_name == "my-svc"


class TestOtelConfigFromEnv:
    def test_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
        monkeypatch.setenv("OTEL_SERVICE_NAME", "env-svc")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        config = OtelConfig()
        assert config.endpoint == "http://collector:4317"
        assert config.service_name == "env-svc"
        assert config.protocol == OtelProtocol.HTTP_PROTOBUF

    def test_constructor_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://from-env:4317")
        config = OtelConfig(endpoint="http://from-arg:4317", service_name="svc")
        assert config.endpoint == "http://from-arg:4317"


class TestResolvedMetricsEndpoint:
    def test_grpc_returns_base_unchanged(self):
        config = OtelConfig(
            endpoint="http://localhost:4317",
            service_name="svc",
            protocol=OtelProtocol.GRPC,
        )
        assert config.resolved_metrics_endpoint() == "http://localhost:4317"

    def test_grpc_strips_trailing_slash(self):
        config = OtelConfig(
            endpoint="http://localhost:4317/",
            service_name="svc",
            protocol=OtelProtocol.GRPC,
        )
        assert config.resolved_metrics_endpoint() == "http://localhost:4317"

    def test_http_appends_v1_metrics(self):
        config = OtelConfig(
            endpoint="http://localhost:4318",
            service_name="svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        assert config.resolved_metrics_endpoint() == "http://localhost:4318/v1/metrics"

    def test_http_strips_trailing_slash_before_appending(self):
        config = OtelConfig(
            endpoint="http://localhost:4318/",
            service_name="svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        assert config.resolved_metrics_endpoint() == "http://localhost:4318/v1/metrics"


class TestResolvedTracesEndpoint:
    def test_grpc_returns_base_unchanged(self):
        config = OtelConfig(
            endpoint="http://localhost:4317",
            service_name="svc",
            protocol=OtelProtocol.GRPC,
        )
        assert config.resolved_traces_endpoint() == "http://localhost:4317"

    def test_http_appends_v1_traces(self):
        config = OtelConfig(
            endpoint="https://api.braintrust.dev/otel",
            service_name="svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        assert (
            config.resolved_traces_endpoint()
            == "https://api.braintrust.dev/otel/v1/traces"
        )

    def test_http_strips_trailing_slash_before_appending(self):
        config = OtelConfig(
            endpoint="http://localhost:4318/",
            service_name="svc",
            protocol=OtelProtocol.HTTP_PROTOBUF,
        )
        assert config.resolved_traces_endpoint() == "http://localhost:4318/v1/traces"


class TestHeaders:
    def test_defaults_to_none(self):
        config = OtelConfig(endpoint="http://localhost:4317", service_name="svc")
        assert config.headers is None
        assert config.resolved_headers() is None

    def test_accepts_mapping(self):
        config = OtelConfig(
            endpoint="http://localhost:4317",
            service_name="svc",
            headers={"x-api-key": "secret"},
        )
        assert config.resolved_headers() == {"x-api-key": "secret"}

    def test_parses_otlp_string_form(self):
        config = OtelConfig(
            endpoint="http://localhost:4318",
            service_name="svc",
            headers="Authorization=Bearer tok, x-bt-parent=project_id:123",
        )
        assert config.resolved_headers() == {
            "authorization": "Bearer tok",
            "x-bt-parent": "project_id:123",
        }

    def test_url_encoded_values_decoded(self):
        config = OtelConfig(
            endpoint="http://localhost:4318",
            service_name="svc",
            headers="Authorization=Bearer%20tok",
        )
        assert config.resolved_headers() == {"authorization": "Bearer tok"}

    def test_blank_string_becomes_none(self):
        config = OtelConfig(
            endpoint="http://localhost:4317", service_name="svc", headers=""
        )
        assert config.headers is None

    def test_from_env_var(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=lsv2_abc")
        config = OtelConfig(endpoint="http://localhost:4317", service_name="svc")
        assert config.resolved_headers() == {"x-api-key": "lsv2_abc"}


class TestHeadersAreMasked:
    @staticmethod
    def _config() -> OtelConfig:
        return OtelConfig(
            endpoint="http://localhost:4317",
            service_name="svc",
            headers="Authorization=Bearer supersecret",
        )

    def test_repr_hides_the_value(self):
        printed = repr(self._config())
        assert "supersecret" not in printed
        assert "authorization" in printed

    def test_json_dump_hides_the_value(self):
        dumped = self._config().model_dump_json()
        assert "supersecret" not in dumped
        assert "authorization" in dumped

    def test_resolved_headers_returns_the_value(self):
        assert self._config().resolved_headers() == {
            "authorization": "Bearer supersecret"
        }
