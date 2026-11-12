# otel-metrics

Reusable OpenTelemetry library: simplified wrappers around OTel metric instruments, plus an OTLP tracing pipeline.

## Install

```bash
pip install otel-metrics
```

Or from a built wheel:

```bash
pip install dist/otel_metrics-*.whl
```

## Usage

```python
from enum import auto

from otel_metrics import MetricName, OtelConfig, setup_metrics

# Define your metric names
class MyMetrics(MetricName):
    REQUEST_COUNT = auto()
    REQUEST_DURATION_MS = auto()
    ACTIVE_CONNECTIONS = auto()

# Configure and initialize
config = OtelConfig(endpoint="http://localhost:4317", service_name="my-service")

# setup_metrics returns a context manager that flushes and shuts down on exit
with setup_metrics(config) as metrics:
    counter = metrics.counter(MyMetrics.REQUEST_COUNT, description="Total requests")
    timer = metrics.timer(MyMetrics.REQUEST_DURATION_MS, description="Request latency")

    counter.inc()
```

Calling `setup_metrics` again in the same process reuses the provider it already
created. Pass `set_global=False` for isolated setups, or inject a provider with
`setup_metrics(config, meter_provider=provider)` and the library will not shut it
down.

### Timing operations

```python
with timer.time(attributes={"route": "/orders"}):
    do_work()
```

For explicit control, record a `TimestampNS` yourself:

```python
from otel_metrics import TimestampNS

start = TimestampNS.now()
# ... do work ...
timer.record(start)
```

### Up/down counters

Use an up/down counter for values that rise and fall, such as active connections:

```python
active = metrics.up_down_counter(
    MyMetrics.ACTIVE_CONNECTIONS, description="Open connections"
)

active.inc()
active.dec()
```

`inc` adds and `dec` subtracts, both taking an optional amount and attributes.
Negative amounts raise `ValueError` for counters and histograms.

### Units and histogram buckets

`counter`, `up_down_counter`, `histogram` and `gauge` accept a `unit`. The
Prometheus exporter appends the unit to the metric name, so setting it keeps
the exported names stable (a counter with `unit="s"` exports as
`..._seconds_total`):

```python
counter = metrics.counter(MyMetrics.JOB_SECONDS, description="CPU time", unit="s")
```

`histogram` and `timer` also accept explicit bucket boundaries, which override
the OTel default buckets (0 to 10,000):

```python
hist = metrics.histogram(
    MyMetrics.CHECKPOINT_SECONDS,
    description="Checkpoint duration",
    unit="s",
    buckets=[0.01, 0.1, 1, 10, 100],
)
timer = metrics.timer(MyMetrics.REQUEST_DURATION_MS, buckets=[1, 5, 10, 50, 100])
```

Timers always record milliseconds.

### Gauges

A gauge keeps one value per attribute set:

```python
gauge = metrics.gauge(MyMetrics.QUEUE_DEPTH, description="Queued jobs")
gauge.set(12, attributes={"queue": "default"})
gauge.set(3, attributes={"queue": "billing"})
gauge.get(attributes={"queue": "default"})  # 12.0
gauge.remove(attributes={"queue": "billing"})
```

A gauge emits nothing until its first `set`.

### Configuration

`OtelConfig` is a pydantic-settings model. Values resolve from constructor args or environment variables:

| Field | Env var | Default |
|-------|---------|---------|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | *(required unless `metrics_exporter` is `prometheus`)* |
| `service_name` | `OTEL_SERVICE_NAME` | *(required)* |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` |
| `metrics_exporter` | `OTEL_METRICS_EXPORTER` | `otlp` |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | `None` |
| `tags` | `OTEL_RESOURCE_ATTRIBUTES` | `None` |
| `metric_prefix` | `OTEL_METRIC_PREFIX` | `None` (no prefix) |

Supported protocols: `grpc`, `http/protobuf`.

`metrics_exporter` chooses how metrics leave the process:

- `otlp` (default) pushes metrics over OTLP to `endpoint` on a timer.
- `prometheus` builds a pull reader that serves the default `prometheus_client`
  registry, so a scraper (e.g. Prometheus scraping `/metrics` on the pod) can
  collect them. In this mode `endpoint` is not required — but traces still
  need it, so an endpoint-less config can only be used with `setup_metrics`.

`metric_prefix` is optional. When set, instrument names are prefixed with it
(`myapp.request_count`). It is not derived from `service_name` automatically,
because the OTel resource already carries `service.name`.

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

`tags` are optional OTel resource attributes attached to every metric and span.
They accept a mapping or the OTLP `key=value,key2=value2` string form (via
`OTEL_RESOURCE_ATTRIBUTES` or the `tags` field), parsed
like `headers`:

```python
config = OtelConfig(
    endpoint="https://api.braintrust.dev/otel",
    service_name="my-service",
    tags="env=prod,region=us-east-1",
)
```

The explicit `service_name` always wins over a `service.name` tag.

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

`setup_telemetry` sets the process-global tracer provider and reuses it on later
calls. Pass `set_global=False` for isolated setups, or inject a provider with
`tracer_provider=`. Use `shutdown_telemetry()` (or `force_flush_telemetry()`)
for explicit lifecycle control. Use `build_test_tracer()` for a non-exporting
tracer in unit tests.

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

Use `build_test_metrics()` to get a `Metrics` instance plus an in-memory reader
for unit tests:

```python
from otel_metrics import build_test_metrics

m, reader = build_test_metrics()
m.counter(MyMetrics.REQUEST_COUNT).inc(3)

data = reader.get_metrics_data()
metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
assert metric.name == "request_count"
assert metric.data.data_points[0].value == 3
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

Set the next version in `release-version.txt` and merge to `main`. The
`Release on merge` workflow creates the matching `v*` tag and dispatches the
publish workflow, which builds and uploads that version to PyPI and creates the
GitHub release.

The package version itself comes from the git tag via `hatch-vcs`; the version
file only tells the automation which tag to create. Bump it for each release.

Release tags are immutable and must not be moved. The `latest` tag is a floating
tag: every merge to `main` moves it to the new commit.
