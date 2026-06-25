"""healthz 통합 테스트 (step 3) — 공개 헬스체크의 graceful 동작·시크릿 무노출.

`TestClient`로 `GET /healthz`를 호출해 검증한다. 서비스 의존성은 explicit Settings로
조립한 `HealthService`를 `dependency_overrides`로 주입한다(기본 조립은 env 필수
필드를 요구하므로 테스트는 자기 조립을 넣는다).

AC(step 3):
- 기본(fake/memory/ephemeral) 구성 → 200, `status` 필드 존재(=ok).
- 백엔드 down(neo4j/openai_compat 도달 불가) → 500이 아니라 200 + degraded +
  해당 backend `ok:false`.
- 응답에 자격증명/시크릿(api_key·base_url) 문자열이 없음.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.graph.neo4j_store import Neo4jGraphStore
from kms.adapters.llm.openai_compat import OpenAICompatLLM
from kms.adapters.searchindex import InMemorySearchIndex
from kms.adapters.vectorstore import InMemoryVectorStore, FakeEmbedder
from kms.api.app import create_app
from kms.api.deps import get_health_service
from kms.config.settings import Settings
from kms.services.health import HealthService
from tests._stub_llm import StubLLM

# down 시뮬레이션 — 닫힌 로컬 포트(연결 거부가 즉시 나 빠르고 결정론적).
_DEAD_NEO4J_URI = "bolt://127.0.0.1:9"
_DEAD_LLM_BASE_URL = "http://127.0.0.1:9/v1"
# 응답에 절대 새면 안 되는 시크릿 — 노출 여부 단언에 쓴다.
_SECRET_API_KEY = "sk-super-secret-token-123"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _client(health: HealthService) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_health_service] = lambda: health
    with TestClient(app) as client:
        yield client


@pytest.fixture
def default_client(tmp_path: Path) -> Iterator[TestClient]:
    """기본 구성(fake embedder·InMemory vector/graph/search·StubLLM).

    llm_backend는 기본 openai_compat이나, StubLLM은 OpenAICompatLLM 인스턴스가
    아니므로 헬스체크가 ping 없이 ok로 본다(실 서버 불요).
    """
    settings = _settings()
    embedder = FakeEmbedder()
    health = HealthService(
        settings=settings,
        vectorstore=InMemoryVectorStore(embedder),
        graph_store=InMemoryGraphStore(),
        llm=StubLLM(),
        search_index=InMemorySearchIndex(),
    )
    yield from _client(health)


@pytest.fixture
def down_client(tmp_path: Path) -> Iterator[TestClient]:
    """graph=neo4j·llm=openai_compat이 도달 불가(닫힌 포트)인 구성."""
    settings = _settings(graph_backend="neo4j", llm_backend="openai_compat")
    embedder = FakeEmbedder()
    health = HealthService(
        settings=settings,
        vectorstore=InMemoryVectorStore(embedder),
        graph_store=Neo4jGraphStore(_DEAD_NEO4J_URI, "u", "p"),
        llm=OpenAICompatLLM(_DEAD_LLM_BASE_URL, "gemma", api_key=_SECRET_API_KEY),
        search_index=InMemorySearchIndex(),
    )
    yield from _client(health)


def test_default_config_returns_ok(default_client: TestClient) -> None:
    response = default_client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    backends = body["backends"]
    assert backends["embedder"]["ok"] is True
    assert backends["vectorstore"]["ok"] is True
    assert backends["graph"]["ok"] is True
    assert backends["llm"]["ok"] is True
    # 어휘 검색 인덱스(memory)도 헬스 응답에 뜨고 항상 ok다.
    assert backends["search_index"]["ok"] is True


def test_down_backend_degrades_without_500(down_client: TestClient) -> None:
    # 도달 불가 백엔드가 있어도 헬스체크는 500이 아니라 200 + degraded여야 한다.
    response = down_client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    backends = body["backends"]
    # 도달 불가 backend는 ok:false로 표기되고, 멀쩡한 backend는 ok:true 유지.
    assert backends["graph"]["ok"] is False
    assert backends["llm"]["ok"] is False
    assert backends["embedder"]["ok"] is True
    assert backends["vectorstore"]["ok"] is True


def test_response_does_not_leak_secrets(down_client: TestClient) -> None:
    # 실패 detail에 api_key·base_url 같은 시크릿이 새면 안 된다(타입명만 노출).
    body = down_client.get("/healthz").text

    assert _SECRET_API_KEY not in body
    assert _DEAD_LLM_BASE_URL not in body
    assert "127.0.0.1" not in body
