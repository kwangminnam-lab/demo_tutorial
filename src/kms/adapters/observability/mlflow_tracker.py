"""MLflow 추적 어댑터 — AI OCR(Qwen3-VL) 추출 성능을 플랫폼 MLflow로 관리.

`MlflowTracker`는 `mlflow` SDK를 **lazy import**한다(미설치 환경에서도 import 안전).
설정(enabled+tracking_uri)이 없거나 SDK 부재면 `NoopOcrTracker`로 폴백한다(조용한 실패
아님 — 사유 로깅). 기록(`log_ocr_run`)은 best-effort: 백엔드 오류가 추출을 막지 않도록
내부에서 예외를 삼키되 디버그 로깅한다.

스레드 안전을 위해 fluent API(전역 active run)가 아닌 **MlflowClient 저수준 API**를 쓴다
— AI OCR 추출은 ThreadPool/백그라운드 잡에서 동시 실행될 수 있어 전역 상태를 피한다.
추출 1회 = run 1건(create_run→log_param/metric→set_terminated).
"""

from __future__ import annotations

import logging
from typing import Any

from kms.config.settings import Settings

logger = logging.getLogger(__name__)


class NoopOcrTracker:
    """추적 비활성/미설정용 무동작 sink — `OcrMetricsSink` 프로토콜 충족."""

    def log_ocr_run(
        self,
        *,
        run_name: str,
        params: dict[str, object],
        metrics: dict[str, float],
        tags: dict[str, str],
    ) -> None:
        return None


class MlflowTracker:
    """AI OCR run 기록기. 생성자에서 client·experiment를 준비한다."""

    def __init__(self, client: Any, experiment_id: str) -> None:
        self._client = client
        self._experiment_id = experiment_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "MlflowTracker | NoopOcrTracker":
        """설정으로 트래커를 만든다 — 비활성/uri누락/SDK부재면 NoopOcrTracker.

        experiment는 이름으로 조회하고 없으면 생성한다. tracking_uri는 시크릿이 아니지만
        실패 사유는 타입만 로깅한다(불필요한 노출 방지).
        """
        if not settings.mlflow_enabled:
            return NoopOcrTracker()
        if not settings.mlflow_tracking_uri:
            logger.warning("MLflow enabled지만 tracking_uri 미설정 — 추적 비활성(Noop)")
            return NoopOcrTracker()
        try:
            from mlflow.tracking import MlflowClient

            client = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
            exp = client.get_experiment_by_name(settings.mlflow_experiment)
            experiment_id = (
                exp.experiment_id
                if exp is not None
                else client.create_experiment(settings.mlflow_experiment)
            )
        except Exception as exc:  # noqa: BLE001 — SDK 부재/초기화 실패는 추적 비활성 사유
            logger.warning("MLflow 초기화 실패 — 추적 비활성 (%s)", type(exc).__name__)
            return NoopOcrTracker()
        return cls(client, experiment_id)

    def log_ocr_run(
        self,
        *,
        run_name: str,
        params: dict[str, object],
        metrics: dict[str, float],
        tags: dict[str, str],
    ) -> None:
        """MLflow에 OCR run 1건 기록. 백엔드 오류는 삼키되 디버그 로깅(best-effort)."""
        try:
            run = self._client.create_run(
                self._experiment_id, run_name=run_name, tags=tags
            )
            run_id = run.info.run_id
            for key, value in params.items():
                self._client.log_param(run_id, key, value)
            for key, value in metrics.items():
                self._client.log_metric(run_id, key, float(value))
            self._client.set_terminated(run_id, "FINISHED")
        except Exception as exc:  # noqa: BLE001 — 추적 실패가 추출을 막지 않게 삼킨다(로깅)
            logger.debug("MLflow 기록 실패 — 스킵 (%s)", type(exc).__name__)
