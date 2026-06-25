"""factory(조립 단일 지점) 단위 테스트 (step 0) — backend 선택·기본 조립·에러.

기본 Settings(fake/memory/ephemeral)는 인프라·모델 없이 조립돼야 하고, backend
이름에 따라 올바른 구현이 선택돼야 하며, 알 수 없는 이름은 명확히 실패해야 한다.
"""

from __future__ import annotations

import importlib.util

import pytest

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.llm.openai_compat import OpenAICompatLLM
from kms.adapters.searchindex.memory_store import InMemorySearchIndex
from kms.adapters.vectorstore.embedder import FakeEmbedder
from kms.adapters.reranker.bge_reranker import BgeReranker
from kms.adapters.reranker.fake import FakeReranker
from kms.adapters.reranker.qwen3_reranker import Qwen3Reranker
from kms.config.settings import Settings
from kms.factory import (
    AppServices,
    build_embedder,
    build_graph_store,
    build_llm,
    build_reranker,
    build_search_index,
    build_services,
)


def _settings(**overrides: object) -> Settings:
    """필수 필드를 채운 테스트용 Settings (env 비의존)."""
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg, arg-type]


def test_default_backends_select_lightweight_implementations() -> None:
    settings = _settings()

    assert isinstance(build_embedder(settings), FakeEmbedder)
    # LLM 기본은 openai_compat(실구현, lazy 연결) — fake 제거됨.
    assert isinstance(build_llm(settings), OpenAICompatLLM)
    assert isinstance(build_graph_store(settings), InMemoryGraphStore)
    assert isinstance(build_search_index(settings), InMemorySearchIndex)


def test_build_services_assembles_with_defaults() -> None:
    # 인프라(OpenSearch·Neo4j·LLM 서버·모델) 없이 예외 없이 조립돼야 한다
    # (InMemory vector/graph/search + openai_compat은 lazy 연결).
    services = build_services(_settings())

    assert isinstance(services, AppServices)
    assert services.search is not None
    assert services.rag is not None
    assert services.ingestion is not None
    assert services.diff is not None


def test_unknown_embedder_backend_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 embedder_backend"):
        build_embedder(_settings(embedder_backend="bogus"))


def test_unknown_graph_backend_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 graph_backend"):
        build_graph_store(_settings(graph_backend="bogus"))


def test_unknown_llm_backend_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 llm_backend"):
        build_llm(_settings(llm_backend="bogus"))


def test_unknown_search_backend_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 search_backend"):
        build_search_index(_settings(search_backend="bogus"))


def test_sentence_transformers_backend_requires_optional_dependency() -> None:
    # 실모델 어댑터는 구현됐다(step 1). 무거운 ML 의존이 미설치면 사용 시점에
    # 설치 안내가 담긴 명확한 에러로 실패한다(조용한 폴백 금지). 설치돼 있으면
    # 실제 모델 로드(다운로드)는 무거우므로 단위 테스트에서 건너뛴다.
    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("sentence_transformers 설치됨 — 무거운 모델 로드는 skip")
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        build_embedder(_settings(embedder_backend="sentence_transformers"))


def test_build_reranker_disabled_returns_none() -> None:
    # reranker_enabled=False면 재정렬 스킵(None) — SearchService가 RRF 결과 그대로.
    assert build_reranker(_settings(reranker_enabled=False)) is None


def test_build_reranker_selects_backend() -> None:
    # 생성만 검증 — 어댑터는 lazy load라 이 시점엔 모델 가중치를 끌어오지 않는다.
    assert isinstance(
        build_reranker(_settings(reranker_enabled=True, reranker_backend="fake")),
        FakeReranker,
    )
    assert isinstance(
        build_reranker(_settings(reranker_enabled=True, reranker_backend="qwen3")),
        Qwen3Reranker,
    )
    assert isinstance(
        build_reranker(_settings(reranker_enabled=True, reranker_backend="bge")),
        BgeReranker,
    )


def test_build_reranker_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는 reranker_backend"):
        build_reranker(_settings(reranker_enabled=True, reranker_backend="nope"))
