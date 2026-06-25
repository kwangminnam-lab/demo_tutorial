"""Langfuse 추적 어댑터 — 챗봇 LLM 호출을 플랫폼 Langfuse로 관측.

`LangfuseTracer`는 `Langfuse` SDK를 **lazy import**한다(미설치 환경에서도 import 안전).
설정(enabled+키)이 없거나 SDK 부재면 `NoopTracer`로 폴백한다(조용한 실패 아님 — 사유
로깅). 추적 호출(`record_generation`)은 best-effort: 백엔드 오류가 답변 생성을 막지
않도록 내부에서 예외를 삼키되 디버그 로깅한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from kms.adapters.llm.base import TokenUsage
from kms.config.settings import Settings

logger = logging.getLogger(__name__)


class NoopTracer:
    """추적 비활성/미설정용 무동작 Tracer — `Tracer` 프로토콜 충족."""

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
        return None


class LangfuseTracer:
    """Langfuse generation 기록기. 생성자에서 SDK·클라이언트를 준비한다."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "LangfuseTracer | NoopTracer":
        """설정으로 트레이서를 만든다 — 비활성/키누락/SDK부재면 NoopTracer.

        키는 시크릿이므로 에러·로그에 노출하지 않는다(타입/사유만 로깅).
        """
        if not settings.langfuse_enabled:
            return NoopTracer()
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            logger.warning("Langfuse enabled지만 키 미설정 — 추적 비활성(NoopTracer)")
            return NoopTracer()
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_base_url,
            )
        except Exception as exc:  # noqa: BLE001 — SDK 부재/초기화 실패는 추적 비활성 사유
            logger.warning("Langfuse 초기화 실패 — 추적 비활성 (%s)", type(exc).__name__)
            return NoopTracer()
        return cls(client)

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
        """Langfuse에 generation 1건 기록. 백엔드 오류는 삼키되 디버그 로깅(best-effort).

        start/end_time → latency 차트, usage → Usage/Cost 차트. cost는 Langfuse가
        `model`(예: openai/gpt-oss-120b)을 가격표에서 매칭해 환산하므로, 자체호스팅
        모델은 Langfuse Models 설정에 단가를 등록해야 cost가 채워진다(토큰은 등록 없이도 집계).
        """
        try:
            self._client.generation(
                name=name,
                model=model,
                input=input,
                output=output,
                start_time=start_time,
                end_time=end_time,
                usage=_usage_payload(usage),
                metadata={
                    "provider": provider,
                    "latency_ms": round(latency_ms, 1),
                    **({"error": error} if error else {}),
                },
                level="ERROR" if error else "DEFAULT",
                status_message=error,
            )
        except Exception as exc:  # noqa: BLE001 — 추적 실패가 답변을 막지 않게 삼킨다(로깅)
            logger.debug("Langfuse 기록 실패 — 스킵 (%s)", type(exc).__name__)


def _usage_payload(usage: TokenUsage | None) -> dict[str, Any] | None:
    """TokenUsage → Langfuse usage dict(없으면 None). 단위는 TOKENS."""
    if usage is None:
        return None
    return {
        "input": usage.prompt_tokens,
        "output": usage.completion_tokens,
        "total": usage.total_tokens,
        "unit": "TOKENS",
    }
