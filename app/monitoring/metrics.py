from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from threading import Lock


_HTTP_BUCKETS_MS = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 3000.0, 10000.0)
_BINARY_BUCKETS_MS = (100.0, 250.0, 500.0, 1000.0, 3000.0, 10000.0, 30000.0)


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))


def _labels_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + body + "}"


@dataclass(slots=True)
class HistogramState:
    sum_value: float = 0.0
    count_value: int = 0
    buckets: dict[float, int] | None = None


class MonitoringMetrics:
    """Lightweight in-process collector for service monitoring."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = time.time()
        self._http_requests = Counter()
        self._job_events = Counter()
        self._http_hist = defaultdict(HistogramState)
        self._binary_requests = Counter()
        self._binary_hist = defaultdict(HistogramState)
        self._last_health: dict[str, object] = {
            "status": "unknown",
            "vram_free_gb": None,
            "last_health_check_at": None,
        }
        self._recent_failures = Counter()
        self._timeout_events: list[float] = []
        self._timeout_window_s = 600.0
        self._timeout_alert_threshold = 3

    def record_http_request(self, *, method: str, path: str, status_code: int, duration_ms: float) -> None:
        labels = _labels_key({"method": method, "path": path, "status": str(status_code)})
        with self._lock:
            self._http_requests[labels] += 1
            hist = self._http_hist[labels]
            if hist.buckets is None:
                hist.buckets = {bucket: 0 for bucket in _HTTP_BUCKETS_MS}
            hist.sum_value += duration_ms / 1000.0
            hist.count_value += 1
            for bucket in _HTTP_BUCKETS_MS:
                if duration_ms <= bucket:
                    hist.buckets[bucket] += 1

    def record_binary_request(self, *, status: str, duration_ms: float) -> None:
        labels = _labels_key({"status": status})
        with self._lock:
            self._binary_requests[labels] += 1
            hist = self._binary_hist[labels]
            if hist.buckets is None:
                hist.buckets = {bucket: 0 for bucket in _BINARY_BUCKETS_MS}
            hist.sum_value += duration_ms / 1000.0
            hist.count_value += 1
            for bucket in _BINARY_BUCKETS_MS:
                if duration_ms <= bucket:
                    hist.buckets[bucket] += 1

    def record_job_event(self, *, status: str, error_code: str | None = None) -> None:
        with self._lock:
            self._job_events[_labels_key({"status": status})] += 1
            if error_code:
                self._recent_failures[_labels_key({"error_code": error_code})] += 1
                if error_code == "comfy_timeout":
                    now = time.time()
                    self._timeout_events.append(now)
                    cutoff = now - self._timeout_window_s
                    self._timeout_events = [t for t in self._timeout_events if t >= cutoff]

    def update_comfy_health(self, *, status: str, vram_free_gb: float | None) -> None:
        with self._lock:
            self._last_health = {
                "status": status,
                "vram_free_gb": vram_free_gb,
                "last_health_check_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

    def admin_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "uptime_s": max(0.0, time.time() - self._started_at),
                "job_events": [
                    {"status": dict(labels)["status"], "count": count}
                    for labels, count in sorted(self._job_events.items())
                ],
                "recent_failures": [
                    {"error_code": dict(labels)["error_code"], "count": count}
                    for labels, count in sorted(self._recent_failures.items())
                ],
                "timeout_alert": {
                    "window_s": self._timeout_window_s,
                    "threshold": self._timeout_alert_threshold,
                    "count_in_window": len(self._timeout_events),
                    "triggered": len(self._timeout_events) >= self._timeout_alert_threshold,
                },
                "comfy_health": dict(self._last_health),
            }

    def render_prometheus(
        self,
        *,
        queue_depth: int,
        worker_concurrency: int,
        active_jobs: int,
    ) -> str:
        lines: list[str] = []
        uptime_s = max(0.0, time.time() - self._started_at)
        lines.append("# HELP imagegen_uptime_seconds Process uptime in seconds.")
        lines.append("# TYPE imagegen_uptime_seconds gauge")
        lines.append(f"imagegen_uptime_seconds {uptime_s:.3f}")
        lines.append("# HELP imagegen_http_requests_total Total HTTP requests.")
        lines.append("# TYPE imagegen_http_requests_total counter")
        with self._lock:
            http_counts = list(self._http_requests.items())
            http_hist = dict(self._http_hist)
            binary_counts = list(self._binary_requests.items())
            binary_hist = dict(self._binary_hist)
            job_counts = list(self._job_events.items())
            timeout_count = len(self._timeout_events)
        for labels, value in sorted(http_counts):
            lines.append(f"imagegen_http_requests_total{_labels_text(labels)} {value}")
        lines.append("# HELP imagegen_http_request_duration_seconds HTTP request latency.")
        lines.append("# TYPE imagegen_http_request_duration_seconds histogram")
        for labels, hist in sorted(http_hist.items()):
            buckets = hist.buckets or {}
            for bucket in _HTTP_BUCKETS_MS:
                lines.append(
                    "imagegen_http_request_duration_seconds_bucket"
                    f"{_labels_text(labels + (('le', str(bucket / 1000.0)),))} {buckets.get(bucket, 0)}"
                )
            lines.append(
                "imagegen_http_request_duration_seconds_bucket"
                f"{_labels_text(labels + (('le', '+Inf'),))} {hist.count_value}"
            )
            lines.append(
                f"imagegen_http_request_duration_seconds_sum{_labels_text(labels)} {hist.sum_value:.6f}"
            )
            lines.append(
                f"imagegen_http_request_duration_seconds_count{_labels_text(labels)} {hist.count_value}"
            )
        lines.append("# HELP imagegen_binary_requests_total Total binary endpoint requests.")
        lines.append("# TYPE imagegen_binary_requests_total counter")
        for labels, value in sorted(binary_counts):
            lines.append(f"imagegen_binary_requests_total{_labels_text(labels)} {value}")
        lines.append("# HELP imagegen_binary_request_duration_seconds Binary endpoint latency.")
        lines.append("# TYPE imagegen_binary_request_duration_seconds histogram")
        for labels, hist in sorted(binary_hist.items()):
            buckets = hist.buckets or {}
            for bucket in _BINARY_BUCKETS_MS:
                lines.append(
                    "imagegen_binary_request_duration_seconds_bucket"
                    f"{_labels_text(labels + (('le', str(bucket / 1000.0)),))} {buckets.get(bucket, 0)}"
                )
            lines.append(
                "imagegen_binary_request_duration_seconds_bucket"
                f"{_labels_text(labels + (('le', '+Inf'),))} {hist.count_value}"
            )
            lines.append(
                f"imagegen_binary_request_duration_seconds_sum{_labels_text(labels)} {hist.sum_value:.6f}"
            )
            lines.append(
                f"imagegen_binary_request_duration_seconds_count{_labels_text(labels)} {hist.count_value}"
            )
        lines.append("# HELP imagegen_job_events_total Job lifecycle event counts.")
        lines.append("# TYPE imagegen_job_events_total counter")
        for labels, value in sorted(job_counts):
            lines.append(f"imagegen_job_events_total{_labels_text(labels)} {value}")
        lines.append("# HELP imagegen_queue_depth Current queue depth.")
        lines.append("# TYPE imagegen_queue_depth gauge")
        lines.append(f"imagegen_queue_depth {queue_depth}")
        lines.append("# HELP imagegen_worker_concurrency Configured worker concurrency.")
        lines.append("# TYPE imagegen_worker_concurrency gauge")
        lines.append(f"imagegen_worker_concurrency {worker_concurrency}")
        lines.append("# HELP imagegen_active_jobs Current active jobs (queued/running).")
        lines.append("# TYPE imagegen_active_jobs gauge")
        lines.append(f"imagegen_active_jobs {active_jobs}")
        lines.append("# HELP imagegen_comfy_timeout_window_count Timeouts in alert window.")
        lines.append("# TYPE imagegen_comfy_timeout_window_count gauge")
        lines.append(f"imagegen_comfy_timeout_window_count {timeout_count}")
        lines.append("# HELP imagegen_comfy_timeout_alert_triggered Timeout alert threshold reached.")
        lines.append("# TYPE imagegen_comfy_timeout_alert_triggered gauge")
        lines.append(
            f"imagegen_comfy_timeout_alert_triggered "
            f"{1 if timeout_count >= self._timeout_alert_threshold else 0}"
        )
        return "\n".join(lines) + "\n"
