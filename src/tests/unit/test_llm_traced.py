"""TracedLLMClient + LangfuseTracer.from_settings 단위 테스트.

추적은 best-effort 부가기능 — 위임(generate/stream)은 그대로 동작하고, 기록은 Tracer로
나간다. 실 Langfuse 백엔드 없이 RecordingTracer로 검증. 키 없으면 NoopTracer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

from kms.adapters.llm.base import TokenUsage
from kms.adapters.llm.traced import TracedLLMClient
from kms.adapters.observability.langfuse_tracer import (
    LangfuseTracer,
    NoopTracer,
    _usage_payload,
)
from kms.config.settings import Settings


class _RecordingTracer:
    """record_generation 호출을 캡처하는 테스트 Tracer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_generation(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _FixedLLM:
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        return "고정 답변"

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        yield "고정 "
        yield "답변"


class _BoomLLM:
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        raise RuntimeError("llm down")

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        raise RuntimeError("llm down")
        yield ""  # pragma: no cover — 제너레이터로 만들기 위한 미도달 yield


class _UsageLLM:
    """usage·토큰을 노출하는 inner — generate_with_usage/stream_with_usage 구현."""

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        text, _ = self.generate_with_usage(
            prompt, system=system, response_format=response_format
        )
        return text

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        for item in self.stream_with_usage(prompt, system=system):
            if isinstance(item, str):
                yield item

    def generate_with_usage(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> tuple[str, TokenUsage]:
        return "답변", TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14)

    def stream_with_usage(
        self, prompt: str, *, system: str | None = None
    ) -> Iterator[str | TokenUsage]:
        yield "답"
        yield "변"
        yield TokenUsage(prompt_tokens=6, completion_tokens=2, total_tokens=8)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg, arg-type]


def test_generate_delegates_and_records() -> None:
    tracer = _RecordingTracer()
    client = TracedLLMClient(_FixedLLM(), tracer, provider="qwen3", model="Qwen3-4B")

    out = client.generate("질문")

    assert out == "고정 답변"
    assert len(tracer.calls) == 1
    call = tracer.calls[0]
    assert call["provider"] == "qwen3"
    assert call["model"] == "Qwen3-4B"
    assert call["input"] == "질문"
    assert call["output"] == "고정 답변"
    assert call["error"] is None
    assert isinstance(call["latency_ms"], float) and call["latency_ms"] >= 0


def test_generate_records_error_and_reraises() -> None:
    # LLM 오류는 기록 후 그대로 전파(조용한 실패 아님).
    tracer = _RecordingTracer()
    client = TracedLLMClient(_BoomLLM(), tracer, provider="gemma", model="gpt-oss-120b")

    with pytest.raises(RuntimeError, match="llm down"):
        client.generate("질문")

    assert tracer.calls[0]["error"] == "RuntimeError"


def test_stream_yields_and_records_joined_output() -> None:
    tracer = _RecordingTracer()
    client = TracedLLMClient(_FixedLLM(), tracer, provider="qwen3", model="Qwen3-4B")

    chunks = list(client.stream("질문"))

    assert chunks == ["고정 ", "답변"]
    assert tracer.calls[0]["output"] == "고정 답변"


def test_generate_records_start_end_time() -> None:
    # latency 차트는 start/end_time(wall-clock)으로 계산 — 둘 다 기록돼야 한다.
    tracer = _RecordingTracer()
    client = TracedLLMClient(_FixedLLM(), tracer, provider="llm", model="gpt-oss-120b")
    client.generate("질문")
    call = tracer.calls[0]
    assert isinstance(call["start_time"], datetime)
    assert isinstance(call["end_time"], datetime)
    assert call["end_time"] >= call["start_time"]  # type: ignore[operator]
    # usage 미노출 inner(_FixedLLM)는 토큰 없이 기록(None).
    assert call["usage"] is None


def test_generate_records_usage_when_inner_exposes_it() -> None:
    tracer = _RecordingTracer()
    client = TracedLLMClient(_UsageLLM(), tracer, provider="llm", model="gpt-oss-120b")
    out = client.generate("질문")
    assert out == "답변"
    assert tracer.calls[0]["usage"] == TokenUsage(
        prompt_tokens=10, completion_tokens=4, total_tokens=14
    )


def test_stream_collects_usage_and_excludes_it_from_output() -> None:
    tracer = _RecordingTracer()
    client = TracedLLMClient(_UsageLLM(), tracer, provider="llm", model="gpt-oss-120b")
    chunks = list(client.stream("질문"))
    # TokenUsage 항목은 답변 텍스트에 섞이지 않는다.
    assert chunks == ["답", "변"]
    assert tracer.calls[0]["output"] == "답변"
    assert tracer.calls[0]["usage"] == TokenUsage(
        prompt_tokens=6, completion_tokens=2, total_tokens=8
    )


def test_usage_payload_maps_tokens_or_none() -> None:
    assert _usage_payload(None) is None
    assert _usage_payload(
        TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    ) == {"input": 10, "output": 4, "total": 14, "unit": "TOKENS"}


def test_from_settings_noop_when_disabled() -> None:
    assert isinstance(LangfuseTracer.from_settings(_settings(langfuse_enabled=False)), NoopTracer)


def test_from_settings_noop_when_keys_missing() -> None:
    # enabled지만 키 미설정 → NoopTracer(추적 비활성, 앱은 정상).
    tracer = LangfuseTracer.from_settings(_settings(langfuse_enabled=True))
    assert isinstance(tracer, NoopTracer)


def test_langfuse_base_url_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # env 이름은 LANGFUSE_BASE_URL — 필드 langfuse_base_url 로 매핑되어야 한다.
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://lf.example")
    assert _settings().langfuse_base_url == "https://lf.example"
