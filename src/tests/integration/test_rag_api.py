"""rag-api 통합 테스트 (step 4) — RAG·diff·export HTTP 경계의 인증·권한.

실 자격증명·모델 없이 도는 dev 조립으로 검증한다: FastAPI `TestClient` +
`StubLLM` + `FakeEmbedder` + `InMemoryVectorStore` + `InMemoryGraphStore` +
서명 JWT(`JwtIdentityProvider`) + 인메모리 계정 더블(`FakeAccountRepository`). 서비스
의존성은 `app.dependency_overrides`로 주입한다.

AC(step 4):
- 인증 없이 `/v1/rag` → 401.
- 인증된 임직원 RAG → 권한 내 근거만 인용, 사장-전용 문서 미사용.
- `/v1/diff` 권한 밖 문서 → 403; 가시 문서 → DiffResult 반환.
- `/v1/export`가 요청 형식의 파일 바이트 반환.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata
from kms.adapters.llm.router import LLMRouter
from kms.adapters.searchindex import InMemorySearchIndex
from kms.adapters.vectorstore import InMemoryVectorStore, FakeEmbedder
from kms.api.app import create_app
from kms.api.deps import (
    get_auth_service,
    get_diff_service,
    get_document_resolver,
    get_llm_router,
    get_rag_service,
)
from kms.config.settings import Settings
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, SourceType
from kms.services.auth_service import AuthService, JwtIdentityProvider
from kms.services.diff_service import DiffService
from kms.services.rag_service import RAGService
from kms.services.search_service import SearchService
from tests._auth_tokens import TEST_CODEC, issue_token
from tests._stub_llm import StubLLM
from tests.integration._fake_accounts import FakeAccountRepository

EMP_TOKEN = issue_token("emp@corp.com")  # 임직원, 영업부


@dataclass
class Harness:
    """테스트 조립 핸들 — 클라이언트와, 직접 색인이 필요한 store 참조."""

    client: TestClient
    vectorstore: InMemoryVectorStore
    graph: InMemoryGraphStore
    documents: dict[str, tuple[Path, DocumentMetadata]]


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )
    search_service = SearchService(
        vectorstore, graph, embedder, settings, InMemorySearchIndex()
    )
    rag_service = RAGService(search_service, StubLLM(), settings)
    diff_service = DiffService()
    # /v1/rag 라우트는 llm_router에서 client를 받아 service에 넘긴다(서비스 자체 LLM이
    # 아니라). fake 제거로 기본 라우터는 openai_compat(실서버)라, 테스트는 StubLLM
    # 라우터를 주입해 실 LLM 연결 없이 RAG를 검증한다.
    llm_router = LLMRouter(default_client=StubLLM(), settings=settings)

    # diff용 doc_id → (경로, 메타) 해소기. 라우트가 doc_id로 메타·경로를 조회한다.
    documents: dict[str, tuple[Path, DocumentMetadata]] = {}

    def resolver(doc_id: str) -> tuple[Path, DocumentMetadata]:
        return documents[doc_id]  # 미등록이면 KeyError → 라우트가 404로 변환.

    # 계정 repository — 인메모리 더블(DB 서버 불요). 실 PostgreSQL repo 동작은
    # test_account_repo.py(integration)가 검증한다.
    accounts = FakeAccountRepository()
    accounts.upsert(email="emp@corp.com", department="영업부", access_level=1)
    auth_service = AuthService(
        JwtIdentityProvider(TEST_CODEC), accounts, codec=TEST_CODEC
    )

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_rag_service] = lambda: rag_service
    app.dependency_overrides[get_llm_router] = lambda: llm_router
    app.dependency_overrides[get_diff_service] = lambda: diff_service
    app.dependency_overrides[get_document_resolver] = lambda: resolver

    with TestClient(app) as client:
        yield Harness(
            client=client,
            vectorstore=vectorstore,
            graph=graph,
            documents=documents,
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _index_chunk(
    harness: Harness, chunk_id: str, text: str, access: AccessLevel
) -> None:
    """청크 1건을 vectorstore·graph 양쪽에 색인한다(검색 가능하도록)."""
    chunk = Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            source=SourceType.ONEDRIVE,
            access=access,
            author_department="영업부",
            chunk_index=0,
        ),
    )
    harness.vectorstore.index([chunk])
    harness.graph.add_document(f"doc-{chunk_id}", chunk.metadata)
    harness.graph.add_chunks(f"doc-{chunk_id}", [chunk])


def _register_doc(
    harness: Harness, tmp_path: Path, doc_id: str, text: str, access: AccessLevel
) -> None:
    """diff 해소기에 doc_id → (파일, 메타)를 등록한다."""
    path = tmp_path / f"{doc_id}.txt"
    path.write_text(text, encoding="utf-8")
    harness.documents[doc_id] = (
        path,
        DocumentMetadata(source=SourceType.ONEDRIVE, access=access),
    )


def test_rag_no_auth_required_in_demo(harness: Harness) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — 토큰 없이도 RAG가 동작한다(기본 SSE 200).
    response = harness.client.post("/v1/rag", json={"query": "매출"})
    assert response.status_code == 200


def test_demo_rag_cites_evidence_without_access_filter(harness: Harness) -> None:
    # 데모 모드(ADR-026): access 하드필터가 무력화(고정 마스터)되어 이전 권한 밖
    # (사장-전용) 근거도 인용된다. access 필터 내부 배선은 보존 — 컨텍스트만 마스터.
    _index_chunk(harness, "ceo", "사장만 보는 기밀 전략 보고", AccessLevel.사장)

    # Act: 인증 없이 비스트리밍 폴백으로 Answer JSON을 받는다.
    response = harness.client.post(
        "/v1/rag",
        params={"stream": "false"},
        json={"query": "기밀 전략"},
    )

    # Assert: 사장-전용 근거가 인용된다(권한 필터 제거).
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    cited_ids = {citation["doc_id"] for citation in body["citations"]}
    assert "ceo" in cited_ids


def test_rag_streaming_returns_sse(harness: Harness) -> None:
    # Arrange: 접근 가능한 근거 1건.
    _index_chunk(harness, "emp", "임직원용 매출 보고", AccessLevel.임직원)

    # Act: 기본(stream=true) — SSE.
    response = harness.client.post(
        "/v1/rag", json={"query": "매출"}, headers=_auth(EMP_TOKEN)
    )

    # Assert: text/event-stream, data 이벤트로 LLM 텍스트 전달 + [DONE] 종료.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data:" in response.text
    assert "[DONE]" in response.text


def test_diff_any_document_in_demo_returns_result(
    harness: Harness, tmp_path: Path
) -> None:
    # 데모 모드(ADR-026): access 필터 무력화 — 이전 권한 밖(사장-전용) 문서도 diff 가능.
    _register_doc(harness, tmp_path, "visible", "한 줄\n두 줄", AccessLevel.임직원)
    _register_doc(harness, tmp_path, "secret", "한 줄\n세 줄", AccessLevel.사장)

    response = harness.client.post(
        "/v1/diff",
        json={"doc_id_a": "visible", "doc_id_b": "secret"},
    )
    assert response.status_code == 200
    assert response.json()["changed"] >= 1


def test_diff_visible_documents_returns_diff_result(
    harness: Harness, tmp_path: Path
) -> None:
    # Arrange: 임직원 가시 문서 두 개(둘째 줄만 다름).
    _register_doc(harness, tmp_path, "a", "공통 줄\n원래 내용", AccessLevel.임직원)
    _register_doc(harness, tmp_path, "b", "공통 줄\n바뀐 내용", AccessLevel.임직원)

    response = harness.client.post(
        "/v1/diff",
        json={"doc_id_a": "a", "doc_id_b": "b"},
        headers=_auth(EMP_TOKEN),
    )

    # Assert: DiffResult — 변경 라인 1건 감지.
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] == 1
    assert any(op["op"] == "change" for op in body["ops"])


def test_export_returns_requested_format_bytes(harness: Harness) -> None:
    # Act: 평문을 TXT로 내보낸다(폰트 의존 없는 결정론적 경로).
    response = harness.client.post(
        "/v1/export",
        json={"content": "안녕 hello", "format": "txt"},
        headers=_auth(EMP_TOKEN),
    )

    # Assert: 요청 형식의 파일 바이트 + 다운로드 헤더.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.decode("utf-8") == "안녕 hello"


def test_export_no_auth_required_in_demo(harness: Harness) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — 토큰 없이도 내보내기가 동작한다.
    response = harness.client.post(
        "/v1/export", json={"content": "x", "format": "txt"}
    )
    assert response.status_code == 200
