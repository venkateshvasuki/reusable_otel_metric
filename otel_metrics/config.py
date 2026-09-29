"""OpenTelemetry exporter configuration."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated

from opentelemetry.util.re import parse_env_headers
from pydantic import AfterValidator, AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _validate_non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


def _parse_headers(value: object) -> object:
    """Accept a mapping, or the OTLP `key=value,key2=value2` env-var form."""
    if not isinstance(value, str):
        return value
    # liberal: tolerate un-encoded values, else a pasted `Bearer <key>` is
    # dropped and the exporter fails auth with no local error.
    parsed = dict(parse_env_headers(value, liberal=True))
    return parsed or None


NonBlankStr = Annotated[str, AfterValidator(_validate_non_blank)]
# validator sits on the optional union so a blank value can resolve to None
HeaderMap = Annotated[dict[str, str] | None, NoDecode, BeforeValidator(_parse_headers)]


class OtelProtocol(StrEnum):
    """OTLP export protocol."""

    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        return name.replace("_", "/").lower()

    HTTP_PROTOBUF = auto()
    GRPC = auto()


class OtelConfig(BaseSettings):
    """Configuration for the OpenTelemetry exporters.

    Values are resolved from constructor args, YAML fields, or environment variables.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    endpoint: NonBlankStr = Field(
        validation_alias=AliasChoices("endpoint", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    service_name: NonBlankStr = Field(
        validation_alias=AliasChoices(
            "serviceName", "service_name", "OTEL_SERVICE_NAME"
        ),
    )
    protocol: OtelProtocol = Field(
        default=OtelProtocol.GRPC,
        validation_alias=AliasChoices("protocol", "OTEL_EXPORTER_OTLP_PROTOCOL"),
    )
    headers: HeaderMap = Field(
        default=None,
        validation_alias=AliasChoices("headers", "OTEL_EXPORTER_OTLP_HEADERS"),
    )

    def resolved_metrics_endpoint(self) -> str:
        """Return the full metrics endpoint URL based on protocol."""
        base = self.endpoint.rstrip("/")
        if self.protocol == OtelProtocol.GRPC:
            return base
        return f"{base}/v1/metrics"

    def resolved_traces_endpoint(self) -> str:
        """Return the full traces endpoint URL based on protocol."""
        base = self.endpoint.rstrip("/")
        if self.protocol == OtelProtocol.GRPC:
            return base
        return f"{base}/v1/traces"

    def resolved_resource_attributes(self) -> dict[str, str]:
        """Return the OTel resource attributes identifying this service."""
        return {"service.name": self.service_name}
