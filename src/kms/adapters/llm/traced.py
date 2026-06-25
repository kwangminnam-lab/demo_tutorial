"""TracedLLMClient — LLMClient를 감싸 호출을 Tracer(Langfuse)로 관측.

`generate`/`stream`을 그대로 위임하되 지연·입출력·provider·model·토큰 usage를 기록한다.
지연은 wall-clock start/end 타임스탬프로 남겨 Langfuse가 latency를 계산하게 한다(메타
숫자만으론 latency 차트가 안 채워짐). usage는 inner 클라이언트가 노출할 때만 전달한다
(`generate_with_usage`/`stream_with_usage` — 없으면 토큰 없이 진행). 추적은 best-effort
(Tracer가 내부에서 실패를 삼킴) — LLM 자체 오류는 기록 후 그대로 전파한다(조용한 실패
아님). provider/model 라벨은 라우터가 클라이언트별로 주입한다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone

from kms.adapters.llm.base import LLMClient, TokenUsage
from kms.adapters.observability.base import Tracer

# Langfuse generation 이름 — 대시보드 필터/그룹용 고정 라벨.
_GEN_NAME = "docux-rag-llm"


class TracedLLMClient:
    """`LLMClient` 프로토콜을 만족하는 추적 래퍼."""

    def __init__(self, inner: LLMClient, tracer: Tracer, *, provider: str, model: str) -> None:
        self._inner = inner
        self._tracer = tracer
        self._provider = provider
        self._model = model

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        start_perf = time.perf_counter()
        start_dt = datetime.now(timezone.utc)
        usage: TokenUsage | None = None
        try:
            inner_usage = getattr(self._inner, "generate_with_usage", None)
            if inner_usage is not None:
                output, usage = inner_usage(
                    prompt, system=system, response_format=response_format
                )
            else:
                output = self._inner.generate(
                    prompt, system=system, response_format=response_format
                )
        except Exception as exc:
            self._record(prompt, "", start_perf, start_dt, error=type(exc).__name__)
            raise
        self._record(prompt, output, start_perf, start_dt, usage=usage)
        return output

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        start_perf = time.perf_counter()
        start_dt = datetime.now(timezone.utc)
        chunks: list[str] = []
        usage: TokenUsage | None = None
        try:
            inner_stream_usage = getattr(self._inner, "stream_with_usage", None)
            if inner_stream_usage is not None:
                for item in inner_stream_usage(prompt, system=system):
                    if isinstance(item, TokenUsage):
                        usage = item
                    else:
                        chunks.append(item)
                        yield item
            else:
                for chunk in self._inner.stream(prompt, system=system):
                    chunks.append(chunk)
                    yield chunk
        except Exception as exc:
            self._record(
                prompt, "".join(chunks), start_perf, start_dt, error=type(exc).__name__
            )
            raise
        self._record(prompt, "".join(chunks), start_perf, start_dt, usage=usage)

    def _record(
        self,
        prompt: str,
        output: str,
        start_perf: float,
        start_dt: datetime,
        *,
        error: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        self._tracer.record_generation(
            name=_GEN_NAME,
            model=self._model,
            provider=self._provider,
            input=prompt,
            output=output,
            latency_ms=(time.perf_counter() - start_perf) * 1000.0,
            start_time=start_dt,
            end_time=datetime.now(timezone.utc),
            usage=usage,
            error=error,
        )
