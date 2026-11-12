"""OpenTelemetry exporter configuration."""

from __future__ import annotations

import logging
from abc import abstractmethod
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from opentelemetry.util.re import parse_env_headers
from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricReader


def _validate_non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


def _parse_env_map(value: object) -> object:
    """Accept a mapping, or the OTLP `key=value,key2=value2` env-var form."""
    if not isinstance(value, str):
        return value
    # liberal: tolerate un-encoded values, else a pasted `Bearer <key>` is
    # dropped and the exporter fails auth with no local error.
    parsed = dict(parse_env_headers(value, liberal=True))
    return parsed or None


NonBlankStr = Annotated[str, AfterValidator(_validate_non_blank)]
V = TypeVar("V")
# validator sits on the optional union so a blank value can resolve to None
# SecretStr values keep API keys out of repr() and model_dump_json()
Map = Annotated[dict[str, V] | None, NoDecode, BeforeValidator(_parse_env_map)]

logger = logging.getLogger(__name__)


class OtelProtocol(StrEnum):
    """OTLP export protocol."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        return name.replace("_", "/").lower()

    HTTP_PROTOBUF = auto()
    GRPC = auto()


class MetricsExporter(StrEnum):
    """Which exporter a metrics config describes; the union discriminator."""

    OTLP = auto()
    PROMETHEUS = auto()


class MetricsConfig(BaseSettings):
    """Configuration shared by every metrics exporter.

    Construct a subclass directly: :class:`OtlpMetricsConfig` pushes over OTLP
    and requires an endpoint; :class:`PrometheusMetricsConfig` serves the
    default ``prometheus_client`` registry for a scraper to pull and has no
    endpoint at all.
    """

    model_config = SettingsConfigDict(
        env_prefix="OTEL_",
        extra="ignore",
        populate_by_name=True,
    )

    service_name: NonBlankStr
    tags: Map[str] = Field(
        default=None,
        validation_alias="OTEL_RESOURCE_ATTRIBUTES",
    )
    metric_prefix: str | None = None

    @abstractmethod
    def build_metric_reader(self) -> MetricReader:
        """Return the metric reader this export mode describes."""

    @field_validator("metric_prefix", mode="after")
    @classmethod
    def _normalise_metric_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class OtlpMetricsConfig(MetricsConfig):
    """Push metrics over OTLP. The endpoint is required."""

    # Fields without a validation_alias resolve to <env_prefix><FIELD_NAME>, so
    # `service_name` picks up OTEL_SERVICE_NAME; the OTLP names that don't fit
    # the prefix keep explicit env-var aliases.
    endpoint: NonBlankStr = Field(
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    protocol: OtelProtocol = Field(
        default=OtelProtocol.GRPC,
        validation_alias="OTEL_EXPORTER_OTLP_PROTOCOL",
    )
    headers: Map[SecretStr] = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_HEADERS",
    )
    exporter: Literal[MetricsExporter.OTLP] = MetricsExporter.OTLP

    def build_metric_reader(self) -> MetricReader:
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        endpoint = self.resolved_metrics_endpoint()
        logger.info("OTel: exporting metrics via %s to %s", self.protocol, endpoint)
        return PeriodicExportingMetricReader(self._build_metric_exporter())

    def _build_metric_exporter(self) -> MetricExporter:
        endpoint = self.resolved_metrics_endpoint()

        if self.protocol == OtelProtocol.GRPC:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter as GrpcExporter,
            )

            return GrpcExporter(endpoint=endpoint, headers=self.resolved_headers())

        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HttpExporter,
        )

        return HttpExporter(endpoint=endpoint, headers=self.resolved_headers())

    def resolved_metrics_endpoint(self) -> str:
        """Return the full metrics endpoint URL based on protocol."""
        base = self.endpoint.rstrip("/")
        if self.protocol == OtelProtocol.GRPC:
            return base
        return f"{base}/v1/metrics"

    def resolved_headers(self) -> dict[str, str] | None:
        """Return the headers with their real values, for the exporter."""
        if self.headers is None:
            return None
        return {key: value.get_secret_value() for key, value in self.headers.items()}


class PrometheusMetricsConfig(MetricsConfig):
    """Serve metrics on the default prometheus_client registry for a scraper.

    There is no endpoint field: the scraper addresses the pod, not the other
    way around, so a pull-only service configures no OTLP settings at all.
    """

    exporter: Literal[MetricsExporter.PROMETHEUS] = MetricsExporter.PROMETHEUS

    def build_metric_reader(self) -> MetricReader:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        logger.info("OTel: exposing metrics on the Prometheus registry for pull")
        return PrometheusMetricReader()


AnyMetricsConfig = Annotated[
    OtlpMetricsConfig | PrometheusMetricsConfig,
    Field(discriminator="exporter"),
]
"""Discriminated union of the metrics configs; parse with ``TypeAdapter``."""


class OtelConfig(OtlpMetricsConfig):
    """Full OTLP configuration: OTLP metrics plus the OTLP tracing pipeline.

    Reuse one instance for both ``setup_metrics`` and ``setup_telemetry``. For
    pull-based metrics, build a :class:`PrometheusMetricsConfig` instead --
    traces still need this class, because OTLP spans require an endpoint.
    """

    def resolved_traces_endpoint(self) -> str:
        """Return the full traces endpoint URL based on protocol."""
        base = self.endpoint.rstrip("/")
        if self.protocol == OtelProtocol.GRPC:
            return base
        return f"{base}/v1/traces"
