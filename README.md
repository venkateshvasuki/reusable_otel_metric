# otel-metrics

Reusable OpenTelemetry library: simplified wrappers around OTel metric instruments, plus an OTLP tracing pipeline.

## Install

```bash
pip install otel-metrics
```

Or from a built wheel:

```bash
pip install dist/otel_metrics-0.1.0-py3-none-any.whl
```

## Usage

```python
from enum import auto

from otel_metrics import MetricName, OtelConfig, setup_metrics

# Define your metric names
class MyMetrics(MetricName):
    REQUEST_COUNT = auto()
    REQUEST_DURATION_MS = auto()

# Configure and initialize
config = OtelConfig(endpoint="http://localhost:4317", service_name="my-service")
metrics = setup_metrics(config)

# Create instruments
counter = metrics.counter(MyMetrics.REQUEST_COUNT, description="Total requests")
timer = metrics.timer(MyMetrics.REQUEST_DURATION_MS, description="Request latency")

# Record
counter.inc()
```

### Timing operations

```python
from otel_metrics import TimestampNS

start = TimestampNS.now()
# ... do work ...
timer.record(start)
```

### Configuration

`OtelConfig` is a pydantic-settings model. Values resolve from constructor args or environment variables:

| Field | Env var | Default |
|-------|---------|---------|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | *(required)* |
| `service_name` | `OTEL_SERVICE_NAME` | *(required)* |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | `None` |

Supported protocols: `grpc`, `http/protobuf`.

`headers` accepts either a mapping or the OTLP `key=value,key2=value2` string form,
and is forwarded to both the metrics and traces exporters. Values may be URL-encoded
per the OTLP spec, but plain values are accepted too.

Header values are held as `pydantic.SecretStr`, because they usually carry an API
key. Printing or dumping the config shows the header names but masks the values:

```python
config = OtelConfig(
    endpoint="https://api.braintrust.dev/otel",
    service_name="my-service",
    headers="Authorization=Bearer sk-live-123",
)

print(config.model_dump_json())
# {"endpoint":"...","headers":{"authorization":"**********"}}

config.resolved_headers()
# {'authorization': 'Bearer sk-live-123'}
```

Call `resolved_headers()` when you need the real values. Reading `config.headers`
gives `SecretStr` objects, not plain strings.

### Tracing

```python
from otel_metrics import OtelConfig, setup_telemetry

config = OtelConfig(
    endpoint="https://api.braintrust.dev/otel",
    service_name="my-service",
    protocol="http/protobuf",
    headers={"Authorization": "Bearer <key>", "x-bt-parent": "project_id:<id>"},
)
tracer = setup_telemetry(config)

with tracer.start_as_current_span("my-operation"):
    ...
```

`setup_telemetry` sets the process-global tracer provider, so call it once at
startup. Use `build_test_tracer()` for a non-exporting tracer in unit tests.

#### Instrumentors

Pass any `BaseInstrumentor` as a trailing argument and it is attached to the
provider built here -- no need to reach for the global one:

```python
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

setup_telemetry(config, LangchainInstrumentor())
```

Several are fine, and an existing list can be splatted:

```python
setup_telemetry(config, LangchainInstrumentor(), RequestsInstrumentor())
setup_telemetry(config, *instrumentors)
```

### Testing

Use `build_test_metrics()` to get a no-op `Metrics` instance for unit tests:

```python
from otel_metrics import build_test_metrics

metrics = build_test_metrics()
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

## Build

```bash
uv build
```

Produces a wheel and an sdist in `dist/`.

## Releasing

Publishing happens on merge to `main`. The workflow runs the tests and builds on
every merge, but only uploads when the `version` in `pyproject.toml` is one PyPI
has not seen — so **bumping the version is what cuts a release**. Merging
anything else is a no-op for the index.

To release: bump `version`, open a PR, merge it.

Uploads use [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/),
so no API token lives in this repo. It needs a one-time setup on each index:

1. On PyPI, go to *Your projects → Publishing* (or *Account → Publishing* for a
   name that has never been published) and add a GitHub publisher:

   | Field | Value |
   |-------|-------|
   | Owner | `venkateshvasuki` |
   | Repository | `reusable_otel_metric` |
   | Workflow | `publish.yml` |
   | Environment | `pypi` |

2. In this repo's *Settings → Environments*, create an environment named `pypi`.
   Add required reviewers there if releases should need a human approval.

Repeat with an environment named `testpypi` on
[test.pypi.org](https://test.pypi.org) to use the manual *Run workflow* button,
which defaults to TestPyPI.
