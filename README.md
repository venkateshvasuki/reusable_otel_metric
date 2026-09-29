# otel-metrics

Reusable OpenTelemetry metrics library providing simplified wrappers around OTel metric instruments.

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

Supported protocols: `grpc`, `http/protobuf`.

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
