"""Tracer 프로토콜 — LLM 생성(generation) 한 건을 관측 백엔드에 기록.

어댑터 격리(ADR-007): 실 관측 백엔드(Langfuse)를 인터페이스 뒤로 빼고, 미설정·
테스트는 NoopTracer로 격리한다. 추적은 답변 경로의 부가 기능이라 실패해도 전파하지
않는다(호출자가 결과를 못 받는 일이 없도록 — best-effort).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from kms.adapters.llm.base import TokenUsage


@runtime_checkable
class Tracer(Protocol):
    """LLM 생성 1건을 기록한다(모델·provider·입출력·지연·토큰). best-effort."""

    def record_generation(
        self,
        *,
        name: str,
        model: str,
        provider: str,
        input: str,
        output: str,
        latency_ms: float,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        usage: TokenUsage | None = None,
        error: str | None = None,
    ) -> None:
        """관측 백엔드에 generation을 남긴다. 실패해도 예외를 던지지 않는다.

        `start_time`/`end_time`(wall-clock)으로 백엔드가 latency를 계산한다(metadata의
        latency_ms는 보조). `usage`(토큰)는 Usage/Cost 차트용 — None이면 토큰 없이 기록.
        """
        ...


@runtime_checkable
class OcrMetricsSink(Protocol):
    """AI OCR(Qwen3-VL) 추출 1회의 성능 지표를 기록한다(MLflow run). best-effort.

    파라미터(모델·zoom 등 설정)와 지표(지연·fill_rate·신뢰도)를 run 1건으로 남겨
    설정별 성능을 비교한다. 미설정/실패는 무동작(추출 경로를 막지 않는다).
    """

    def log_ocr_run(
        self,
        *,
        run_name: str,
        params: dict[str, object],
        metrics: dict[str, float],
        tags: dict[str, str],
    ) -> None:
        """관측 백엔드에 OCR run 1건을 남긴다. 실패해도 예외를 던지지 않는다."""
        ...
