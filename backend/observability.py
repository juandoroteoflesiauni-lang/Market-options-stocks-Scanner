from __future__ import annotations
from typing import Any
"""Observability primitives for API metrics, tracing, and periodic profiling."""


import os
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

try:  # pragma: no cover - exercised only when prometheus_client is installed.
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback.
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    _PROMETHEUS_AVAILABLE = False

    class _NoopMetric:
        def labels(self, *args: Any, **kwargs: Any) -> _NoopMetric:
            return self

        def inc(self, amount: float = 1.0) -> None:
            return None

        def dec(self, amount: float = 1.0) -> None:
            return None

        def set(self, value: float) -> None:
            return None

        def observe(self, value: float) -> None:
            return None

    Counter = Gauge = Histogram = lambda *args, **kwargs: _NoopMetric()


    def generate_latest() -> bytes:
        return b"# prometheus_client is not installed\n"


try:  # pragma: no cover - depends on optional OpenTelemetry packages.
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback.
    trace = None

    FastAPIInstrumentor = None

    HTTPXClientInstrumentor = None

    TracerProvider = None

    BatchSpanProcessor = None

    ConsoleSpanExporter = None

    _OTEL_AVAILABLE = False


_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_PROVIDER_BUCKETS = (0.001, 0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 45.0)
_DB_BUCKETS = (0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0)
_QUANTILES = (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
_ROLLING_MAX = int(os.getenv("OBSERVABILITY_ROLLING_LATENCY_SAMPLES", "512"))

HTTP_REQUESTS = Counter(
    "qa_http_requests_total",
    "Total HTTP requests.",
    ["method", "endpoint", "status"],
)
HTTP_LATENCY = Histogram(
    "qa_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "endpoint", "status"],
    buckets=_HTTP_BUCKETS,
)
HTTP_LATENCY_QUANTILE = Gauge(
    "qa_http_request_latency_seconds_quantile",
    "Rolling in-process HTTP latency quantiles.",
    ["method", "endpoint", "quantile"],
)

PROVIDER_REQUESTS = Counter(
    "qa_provider_requests_total",
    "External provider requests.",
    ["provider", "operation", "cache_hit", "status"],
)
PROVIDER_LATENCY = Histogram(
    "qa_provider_request_duration_seconds",
    "External provider request duration in seconds.",
    ["provider", "operation", "cache_hit", "status"],
    buckets=_PROVIDER_BUCKETS,
)
PROVIDER_LATENCY_QUANTILE = Gauge(
    "qa_provider_request_latency_seconds_quantile",
    "Rolling in-process provider latency quantiles.",
    ["provider", "operation", "quantile"],
)

CACHE_LOOKUPS = Counter(
    "qa_cache_lookups_total",
    "Cache lookups by cache tier and provider.",
    ["cache", "provider", "result"],
)

DB_LATENCY = Histogram(
    "qa_db_query_duration_seconds",
    "DuckDB operation duration in seconds.",
    ["engine", "operation", "status"],
    buckets=_DB_BUCKETS,
)
DB_LATENCY_QUANTILE = Gauge(
    "qa_db_query_latency_seconds_quantile",
    "Rolling in-process DuckDB latency quantiles.",
    ["engine", "operation", "quantile"],
)

WS_CLIENTS_ACTIVE = Gauge(
    "qa_ws_clients_active",
    "Active WebSocket clients.",
    ["endpoint", "symbol"],
)

PROFILING_RUNS = Counter(
    "qa_profile_runs_total",
    "Periodic pyinstrument profile runs.",
    ["target", "status"],
)
PROFILING_DURATION = Histogram(
    "qa_profile_request_duration_seconds",
    "Duration of requests sampled by pyinstrument.",
    ["target", "status"],
    buckets=_HTTP_BUCKETS,
)
FRONTEND_WEB_VITAL_LATEST = Gauge(
    "qa_frontend_web_vital_latest",
    "Latest frontend Web Vital value reported by the browser.",
    ["metric", "route", "rating"],
)
FRONTEND_CHART_RENDERS = Counter(
    "qa_frontend_chart_renders_total",
    "Frontend chart render count reports.",
    ["component", "route"],
)
FRONTEND_CHART_RENDER_LATEST = Gauge(
    "qa_frontend_chart_render_latest",
    "Latest render count observed for a frontend chart component.",
    ["component", "route"],
)

CONSUMPTION_CALLS = Counter(
    "qa_consumption_calls_total",
    "API consumption calls by provider and endpoint.",
    ["provider", "endpoint", "status"],
)
CONSUMPTION_COST = Counter(
    "qa_consumption_cost_usd_total",
    "Estimated API cost in USD by provider.",
    ["provider"],
)
CONSUMPTION_CACHE_HITS = Counter(
    "qa_consumption_cache_hits_total",
    "Cache hits by provider.",
    ["provider"],
)
CONSUMPTION_CACHE_MISSES = Counter(
    "qa_consumption_cache_misses_total",
    "Cache misses by provider.",
    ["provider"],
)
CONSUMPTION_RATE_LIMITED = Counter(
    "qa_consumption_rate_limited_total",
    "Rate limited requests by provider.",
    ["provider"],
)


_http_samples: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=_ROLLING_MAX))
_provider_samples: dict[tuple[str, str], deque[float]] = defaultdict(
    lambda: deque(maxlen=_ROLLING_MAX)
)
_db_samples: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=_ROLLING_MAX))
_sample_lock = threading.Lock()
_tracing_configured = False
_httpx_instrumented = False


def configure_observability(app: FastAPI) -> None:
    """Attach metrics, tracing, WebSocket gauges, and profiling middleware once."""
    if getattr(app.state, "observability_configured", False):
        return

    app.state.observability_configured = True
    _configure_tracing()

    app.middleware("http")(_http_metrics_middleware)
    app.add_middleware(WebSocketMetricsMiddleware)
    app.add_middleware(PeriodicProfilerMiddleware)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/observability/web-vitals", include_in_schema=False)
    async def web_vitals(request: Request) -> dict[str, bool]:
        payload = await request.json()
        metric = str(payload.get("name") or "unknown")
        route = str(payload.get("route") or "unknown")[:160]
        rating = str(payload.get("rating") or "unknown")
        try:
            value = float(payload.get("value"))
        except (TypeError, ValueError):
            value = 0.0
        FRONTEND_WEB_VITAL_LATEST.labels(metric, route, rating).set(value)
        return {"ok": True}

    @app.post("/api/observability/render-count", include_in_schema=False)
    async def render_count(request: Request) -> dict[str, bool]:
        payload = await request.json()
        component = str(payload.get("component") or "unknown")[:120]
        route = str(payload.get("route") or "unknown")[:160]
        try:
            count = float(payload.get("count"))
        except (TypeError, ValueError):
            count = 1.0
        FRONTEND_CHART_RENDERS.labels(component, route).inc()
        FRONTEND_CHART_RENDER_LATEST.labels(component, route).set(count)
        return {"ok": True}

    if _OTEL_AVAILABLE and FastAPIInstrumentor is not None:
        try:
            FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics")
        except Exception:
            pass


async def _http_metrics_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    start = time.perf_counter()
    status = "500"
    response: Response | None = None
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        endpoint = _http_endpoint_from_scope(request.scope, request.url.path)
        if endpoint != "/metrics":
            duration = time.perf_counter() - start
            record_http_request(request.method, endpoint, status, duration)


class WebSocketMetricsMiddleware:
    """ASGI middleware that tracks active WebSocket clients by route shape."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "websocket":
            await self.app(scope, receive, send)
            return

        endpoint, symbol = _ws_endpoint_from_path(str(scope.get("path") or ""))
        WS_CLIENTS_ACTIVE.labels(endpoint, symbol).inc()
        try:
            await self.app(scope, receive, send)
        finally:
            WS_CLIENTS_ACTIVE.labels(endpoint, symbol).dec()


class PeriodicProfilerMiddleware:
    """Sample expensive API paths with pyinstrument at a bounded interval."""

    _last_profile_by_target: dict[str, float] = {}
    _profile_lock = threading.Lock()

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.enabled = _env_bool("OBS_PROFILE_ENABLED", default=True)
        self.interval_seconds = float(os.getenv("OBS_PROFILE_INTERVAL_SECONDS", "600"))
        self.output_dir = Path(os.getenv("OBS_PROFILE_DIR", "artifacts/profiles"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        target = _profile_target(path)
        if target is None or not self._claim_profile_slot(target):
            await self.app(scope, receive, send)
            return

        try:
            from pyinstrument import Profiler
        except Exception:
            PROFILING_RUNS.labels(target, "unavailable").inc()
            await self.app(scope, receive, send)
            return

        profiler = Profiler(async_mode="enabled")
        start = time.perf_counter()
        status = "ok"
        profiler.start()
        try:
            await self.app(scope, receive, send)
        except Exception:
            status = "error"
            raise
        finally:
            duration = time.perf_counter() - start
            profiler.stop()
            PROFILING_RUNS.labels(target, status).inc()
            PROFILING_DURATION.labels(target, status).observe(duration)
            self._write_profile(profiler, target, status)

    def _claim_profile_slot(self, target: str) -> bool:
        now = time.monotonic()
        with self._profile_lock:
            last = self._last_profile_by_target.get(target, 0.0)
            if (now - last) < self.interval_seconds:
                return False
            self._last_profile_by_target[target] = now
            return True

    def _write_profile(self, profiler: Any, target: str, status: str) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", target).strip("_")
            html_path = self.output_dir / f"{stamp}-{safe_target}-{status}.html"
            html_path.write_text(profiler.output_html(), encoding="utf-8")
        except Exception:
            PROFILING_RUNS.labels(target, "write_error").inc()


def record_http_request(method: str, endpoint: str, status: str, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(method, endpoint, status).inc()
    HTTP_LATENCY.labels(method, endpoint, status).observe(duration_seconds)
    _record_quantiles(
        _http_samples,
        HTTP_LATENCY_QUANTILE,
        (method, endpoint),
        {"method": method, "endpoint": endpoint},
        duration_seconds,
    )


def record_provider_request(
    provider: str,
    operation: str,
    duration_seconds: float,
    *,
    status: str = "ok",
    cache_hit: bool = False,
) -> None:
    operation = _safe_operation(operation)
    hit_label = "true" if cache_hit else "false"
    PROVIDER_REQUESTS.labels(provider, operation, hit_label, status).inc()
    PROVIDER_LATENCY.labels(provider, operation, hit_label, status).observe(duration_seconds)
    _record_quantiles(
        _provider_samples,
        PROVIDER_LATENCY_QUANTILE,
        (provider, operation),
        {"provider": provider, "operation": operation},
        duration_seconds,
    )


def record_cache_lookup(cache: str, provider: str, hit: bool) -> None:
    CACHE_LOOKUPS.labels(cache, provider, "hit" if hit else "miss").inc()


def record_db_query(operation: str, duration_seconds: float, *, status: str = "ok") -> None:
    operation = _safe_operation(operation)
    DB_LATENCY.labels("duckdb", operation, status).observe(duration_seconds)
    _record_quantiles(
        _db_samples,
        DB_LATENCY_QUANTILE,
        ("duckdb", operation),
        {"engine": "duckdb", "operation": operation},
        duration_seconds,
    )


@contextmanager
def provider_observation(
    provider: str,
    operation: str,
    *,
    cache_hit: bool = False,
    attributes: dict[str, Any] | None = None,
) -> Iterator[None]:
    start = time.perf_counter()
    status = "ok"
    attrs = {"provider": provider, "operation": operation, "cache_hit": cache_hit}
    if attributes:
        attrs.update(attributes)
    with span(f"provider.{provider}", attrs):
        try:
            yield
        except Exception as exc:
            status = exc.__class__.__name__
            raise
        finally:
            record_provider_request(
                provider, operation, time.perf_counter() - start, status=status, cache_hit=cache_hit
            )


@contextmanager
def db_observation(operation: str, *, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    start = time.perf_counter()
    status = "ok"
    attrs = {"db.system": "duckdb", "db.operation": operation}
    if attributes:
        attrs.update(attributes)
    with span("db.duckdb", attrs):
        try:
            yield
        except Exception as exc:
            status = exc.__class__.__name__
            raise
        finally:
            record_db_query(operation, time.perf_counter() - start, status=status)


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    if not _OTEL_AVAILABLE or trace is None:
        with nullcontext():
            yield
        return

    tracer = trace.get_tracer("quantum-analyzer")
    with tracer.start_as_current_span(name) as active_span:
        if attributes:
            for key, value in attributes.items():
                if value is None:
                    continue
                try:
                    active_span.set_attribute(key, value)
                except Exception:
                    pass
        yield


def provider_operation_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.path or "/"
    except Exception:
        return url


def _record_quantiles(
    sample_store: dict[tuple[str, str], deque[float]],
    gauge: Any,
    sample_key: tuple[str, str],
    labels: dict[str, str],
    value: float,
) -> None:
    if value < 0:
        return
    with _sample_lock:
        samples = sample_store[sample_key]
        samples.append(value)
        ordered = sorted(samples)
    if not ordered:
        return
    n = len(ordered)
    for label, q in _QUANTILES:
        idx = min(n - 1, max(0, int(round((n - 1) * q))))
        gauge.labels(**labels, quantile=label).set(ordered[idx])


def _configure_tracing() -> None:
    global _tracing_configured, _httpx_instrumented
    if not _OTEL_AVAILABLE or trace is None or TracerProvider is None or _tracing_configured:
        return

    provider = TracerProvider()
    exporter = None
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        except Exception:
            exporter = None
    elif _env_bool("OTEL_CONSOLE_EXPORTER", default=False) and ConsoleSpanExporter is not None:
        exporter = ConsoleSpanExporter()

    if exporter is not None and BatchSpanProcessor is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _tracing_configured = True

    if not _httpx_instrumented and HTTPXClientInstrumentor is not None:
        try:
            HTTPXClientInstrumentor().instrument()
            _httpx_instrumented = True
        except Exception:
            pass


def _http_endpoint_from_scope(scope: Scope, path: str) -> str:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return _normalize_dynamic_path(path)


def _normalize_dynamic_path(path: str) -> str:
    if path.startswith("/api/v1/options/snapshot/"):
        return "/api/v1/options/snapshot/{symbol}"
    if path.startswith("/api/v1/probabilistic/analysis/"):
        return "/api/v1/probabilistic/analysis/{symbol}"
    if path.startswith("/api/fundamental/"):
        return "/api/fundamental/{symbol}"
    if path.startswith("/api/transcripts/"):
        return "/api/transcripts/{symbol}/{year}/{quarter}"
    return path


def _ws_endpoint_from_path(path: str) -> tuple[str, str]:
    for prefix, endpoint in (
        ("/ws/chart_massive/", "/ws/chart_massive/{symbol}"),
        ("/ws/options/", "/ws/options/{symbol}"),
        ("/ws/chart/", "/ws/chart/{symbol}"),
    ):
        if path.startswith(prefix):
            symbol = path[len(prefix) :].split("/", 1)[0].upper() or "unknown"
            return endpoint, symbol
    return path or "unknown", "unknown"


def _profile_target(path: str) -> str | None:
    if path.startswith("/api/v1/options/snapshot/"):
        return "options_snapshot"
    if path.startswith("/api/v1/probabilistic/analysis/"):
        return "probabilistic_analysis"
    if re.match(r"^/api/v1/probabilistic/[^/]+$", path):
        return "probabilistic_symbol"
    return None


def _safe_operation(operation: str) -> str:
    op = operation or "unknown"
    if op.startswith("http://") or op.startswith("https://"):
        op = provider_operation_from_url(op)
    return re.sub(r"/[A-Z]{1,6}(?:\.[A-Z]{1,4})?(?=/|$)", "/{symbol}", op)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def record_consumption_metric(
    provider: str,
    endpoint: str,
    status: str = "success",
    cost_usd: float = 0.0,
    cache_hit: bool = False,
    rate_limited: bool = False,
) -> None:
    """Records Prometheus metrics from the consumption monitor."""
    safe_endpoint = _safe_operation(endpoint)[:120]
    CONSUMPTION_CALLS.labels(provider, safe_endpoint, status).inc()
    if cost_usd > 0:
        CONSUMPTION_COST.labels(provider).inc(cost_usd)
    if cache_hit:
        CONSUMPTION_CACHE_HITS.labels(provider).inc()
    elif not rate_limited:
        CONSUMPTION_CACHE_MISSES.labels(provider).inc()
    if rate_limited:
        CONSUMPTION_RATE_LIMITED.labels(provider).inc()


__all__ = [
    "configure_observability",
    "db_observation",
    "provider_observation",
    "provider_operation_from_url",
    "record_cache_lookup",
    "record_consumption_metric",
    "record_db_query",
    "record_provider_request",
    "span",
]
