"""MlflowTracker 단위 테스트 — from_settings 폴백 + log_ocr_run 배선.

실 MLflow 백엔드/SDK 없이 검증한다: 비활성/uri누락이면 NoopOcrTracker, 활성이면 fake
client(create_run→log_param/metric→set_terminated) 호출 순서·인자를 캡처해 본다.
"""

from __future__ import annotations

from typing import Any

from kms.adapters.observability.mlflow_tracker import (
    MlflowTracker,
    NoopOcrTracker,
)
from kms.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg, arg-type]


class _FakeRunInfo:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


class _FakeRun:
    def __init__(self, run_id: str) -> None:
        self.info = _FakeRunInfo(run_id)


class _FakeClient:
    """MlflowClient 흉내 — 호출을 캡처한다."""

    def __init__(self) -> None:
        self.params: dict[str, object] = {}
        self.metrics: dict[str, float] = {}
        self.created: dict[str, Any] = {}
        self.terminated: str | None = None

    def create_run(self, experiment_id: str, *, run_name: str, tags: dict[str, str]) -> _FakeRun:
        self.created = {"experiment_id": experiment_id, "run_name": run_name, "tags": tags}
        return _FakeRun("run-1")

    def log_param(self, run_id: str, key: str, value: object) -> None:
        self.params[key] = value

    def log_metric(self, run_id: str, key: str, value: float) -> None:
        self.metrics[key] = value

    def set_terminated(self, run_id: str, status: str) -> None:
        self.terminated = status


def test_from_settings_noop_when_disabled() -> None:
    assert isinstance(MlflowTracker.from_settings(_settings(mlflow_enabled=False)), NoopOcrTracker)


def test_from_settings_noop_when_uri_missing() -> None:
    # enabled지만 uri 미설정 → Noop(추적 비활성, 추출은 정상).
    tracker = MlflowTracker.from_settings(_settings(mlflow_enabled=True))
    assert isinstance(tracker, NoopOcrTracker)


def test_log_ocr_run_records_params_metrics_and_terminates() -> None:
    client = _FakeClient()
    tracker = MlflowTracker(client, experiment_id="exp-1")

    tracker.log_ocr_run(
        run_name="scan.pdf",
        params={"model": "Qwen3-VL-4B-Instruct", "zoom": 2.0},
        metrics={"total_s": 3.5, "fill_rate": 0.75},
        tags={"document": "scan.pdf"},
    )

    assert client.created["experiment_id"] == "exp-1"
    assert client.created["run_name"] == "scan.pdf"
    assert client.params == {"model": "Qwen3-VL-4B-Instruct", "zoom": 2.0}
    assert client.metrics == {"total_s": 3.5, "fill_rate": 0.75}
    assert client.terminated == "FINISHED"


def test_log_ocr_run_swallows_backend_error() -> None:
    class _BoomClient:
        def create_run(self, *a: object, **k: object) -> Any:
            raise RuntimeError("mlflow down")

    # 추적 실패가 추출을 막지 않는다(예외 전파 금지).
    MlflowTracker(_BoomClient(), "exp-1").log_ocr_run(
        run_name="x", params={}, metrics={}, tags={}
    )


def test_mlflow_tracking_uri_reads_env_var(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    assert _settings().mlflow_tracking_uri == "http://mlflow:5000"
