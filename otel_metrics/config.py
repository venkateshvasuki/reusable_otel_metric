"""OpenTelemetry exporter configuration."""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated

from pydantic import AfterValidator, AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


NonBlankStr = Annotated[str, AfterValidator(_validate_non_blank)]


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
    """Configuration for the OpenTelemetry metrics exporter.

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

    def resolved_metrics_endpoint(self) -> str:
        """Return the full metrics endpoint URL based on protocol."""
        base = self.endpoint.rstrip("/")
        if self.protocol == OtelProtocol.GRPC:
            return base
        return f"{base}/v1/metrics"
