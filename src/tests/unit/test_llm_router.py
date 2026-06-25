"""LLMRouter 단위 테스트 — 단일 사내 LLM 제공 + tracer 추적 래핑.

(듀얼 LLM provider 라우팅(gemma/qwen3)은 제거됨 — 챗봇은 사내 단일 LLM만 사용.)
"""

from __future__ import annotations

from tests._stub_llm import StubLLM
from kms.adapters.llm.router import LLMRouter
from kms.config.settings import Settings


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )


def test_for_request_returns_default_client() -> None:
    default = StubLLM()
    router = LLMRouter(default_client=default, settings=_settings())

    assert router.for_request() is default


def test_for_request_wraps_with_tracer_when_provided() -> None:
    # tracer 주입 시 for_request 반환이 TracedLLMClient(model 라벨)로 감싸진다.
    from kms.adapters.llm.traced import TracedLLMClient

    class _RecTracer:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def record_generation(self, **kwargs: object) -> None:
            self.calls.append(kwargs)

    tracer = _RecTracer()
    default = StubLLM()
    router = LLMRouter(default_client=default, settings=_settings(), tracer=tracer)

    client = router.for_request()
    assert isinstance(client, TracedLLMClient)
    client.generate("q")
    assert tracer.calls and tracer.calls[0]["provider"] == "llm"


def test_for_request_no_wrap_without_tracer() -> None:
    default = StubLLM()
    router = LLMRouter(default_client=default, settings=_settings())
    assert router.for_request() is default
