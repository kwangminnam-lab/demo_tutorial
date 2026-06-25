"""LLM 클라이언트 제공자 — 단일 사내 LLM(기본 게이트웨이 = gpt-oss-120b).

챗봇은 사내 단일 LLM만 사용한다. (이전의 듀얼 LLM provider 라우팅(gemma/qwen3)은
제거됨 — 외부 전송/사용자 키 LLM(gemini·claude·chatgpt)도 미지원.) tracer가 설정되면
반환 클라이언트를 TracedLLMClient로 감싸 Langfuse에 호출을 기록한다(best-effort).
"""

from __future__ import annotations

from kms.adapters.llm.base import LLMClient
from kms.adapters.llm.traced import TracedLLMClient
from kms.adapters.observability.base import Tracer
from kms.config.settings import Settings


class LLMRouter:
    """단일 LLM 클라이언트 제공자. tracer 설정 시 추적 래핑."""

    def __init__(
        self,
        default_client: LLMClient,
        settings: Settings,
        tracer: Tracer | None = None,
    ) -> None:
        self._default = default_client
        self._settings = settings
        # tracer가 있으면 반환 클라이언트를 TracedLLMClient로 감싸 Langfuse에 기록한다.
        self._tracer = tracer

    def for_request(self) -> LLMClient:
        """사내 LLM 클라이언트를 반환한다(tracer 설정 시 추적 래핑)."""
        if self._tracer is None:
            return self._default
        return TracedLLMClient(
            self._default,
            self._tracer,
            provider="llm",
            model=self._settings.llm_model_name,
        )
