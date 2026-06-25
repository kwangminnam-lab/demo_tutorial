"""search-api 통합 테스트 (step 6) — HTTP 경계의 인증·권한·검색·적재.

실 자격증명 없이 도는 dev 조립으로 검증한다: FastAPI `TestClient` + `FakeEmbedder`
+ `InMemoryVectorStore` + `InMemoryGraphStore` + 서명 JWT(`JwtIdentityProvider`) +
인메모리 계정 더블(`FakeAccountRepository`). 서비스 의존성은
`app.dependency_overrides`로 주입한다.

AC(step 6):
- 인증 없이 `/v1/search` → 401.
- 인증된 임직원 검색 → 자신의 권한 내 결과만, 사장-전용 문서는 미포함.
- 같은 부서 작성 문서가 상위(부서 가중).
- `/v1/ingest`로 적재 후 `/v1/search`에서 조회됨.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kms.adapters.graph.memory_store import InMemoryGraphStore
from kms.adapters.searchindex import InMemorySearchIndex
from kms.adapters.vectorstore import InMemoryVectorStore, FakeEmbedder
from kms.api.app import create_app
from kms.api.deps import (
    get_auth_service,
    get_ingestion_service,
    get_search_service,
)
from kms.config.settings import Settings
from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, SourceType
from kms.services.auth_service import AuthService, JwtIdentityProvider
from kms.services.ingestion_service import IngestionService
from kms.services.search_service import SearchService
from tests._auth_tokens import TEST_CODEC, issue_token
from tests.integration._fake_accounts import FakeAccountRepository

# 서명 JWT 세션 토큰(항상 인증 활성). 계정은 아래 fixture에서 seed.
ADMIN_TOKEN = issue_token("ceo@corp.com")  # 사장, 영업부 — 적재 가능
EMP_TOKEN = issue_token("emp@corp.com")  # 임직원, 영업부


@dataclass
class Harness:
    """테스트 조립 핸들 — 클라이언트와, 직접 색인이 필요한 store 참조."""

    client: TestClient
    vectorstore: InMemoryVectorStore
    graph: InMemoryGraphStore
    search_index: InMemorySearchIndex


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
    search_index = InMemorySearchIndex()
    search_service = SearchService(
        vectorstore, graph, embedder, settings, search_index
    )
    ingestion_service = IngestionService(vectorstore, graph, embedder, search_index)

    # 계정 repository — 인메모리 더블(DB 서버 불요). 실 PostgreSQL repo 동작은
    # test_account_repo.py(integration)가 검증한다.
    accounts = FakeAccountRepository()
    accounts.upsert(email="ceo@corp.com", department="영업부", access_level=2)
    accounts.upsert(email="emp@corp.com", department="영업부", access_level=1)
    auth_service = AuthService(
        JwtIdentityProvider(TEST_CODEC), accounts, codec=TEST_CODEC
    )

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_search_service] = lambda: search_service
    app.dependency_overrides[get_ingestion_service] = lambda: ingestion_service

    with TestClient(app) as client:
        yield Harness(
            client=client,
            vectorstore=vectorstore,
            graph=graph,
            search_index=search_index,
        )


def _write_doc(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _ingest(client: TestClient, token: str, items: list[dict[str, object]]):
    return client.post(
        "/v1/ingest", json=items, headers={"Authorization": f"Bearer {token}"}
    )


def _search(client: TestClient, token: str, **params: object):
    return client.get(
        "/v1/search", params=params, headers={"Authorization": f"Bearer {token}"}
    )


def test_search_no_auth_required_in_demo(harness: Harness) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — 토큰 없이도 검색이 동작한다(빈 인덱스 → []).
    response = harness.client.get("/v1/search", params={"q": "무엇"})
    assert response.status_code == 200
    assert response.json() == []


def test_employee_search_excludes_ceo_only_documents(
    harness: Harness, tmp_path: Path
) -> None:
    # Arrange: 사장이 임직원-공개 문서 + 사장-전용 문서를 적재.
    visible = _write_doc(tmp_path, "visible.txt", "ALPHA 임직원 공개 자료")
    secret = _write_doc(tmp_path, "secret.txt", "BETA 사장 전용 기밀")
    ingest_response = _ingest(
        harness.client,
        ADMIN_TOKEN,
        [
            {"file_path": str(visible), "source": "slack", "access": 1},
            {"file_path": str(secret), "source": "slack", "access": 2},
        ],
    )
    assert ingest_response.status_code == 200
    assert all(report["ok"] for report in ingest_response.json())

    # Act: 임직원이 검색.
    response = _search(harness.client, EMP_TOKEN, q="ALPHA", top_k=10)

    # Assert: 권한 내(ALPHA)는 보이고, 사장-전용(BETA)은 어떤 결과에도 없다.
    assert response.status_code == 200
    texts = " ".join(hit["text"] for hit in response.json())
    assert "ALPHA" in texts
    assert "BETA" not in texts


def test_demo_user_gets_no_department_boost(harness: Harness) -> None:
    # 데모 모드(ADR-026): 고정 마스터 컨텍스트는 부서가 빈 문자열이라 부서 가중이
    # 어느 문서에도 적용되지 않는다(부서 가중 내부 배선은 보존 — 컨텍스트만 무력화).
    # 어휘 점수 동점(같은 제목)·부서만 다른 두 파일 → 가중 없이 동점.
    files = [
        FileDoc(
            doc_id="same_dept",
            title="공통 제목",
            description="공통 본문",
            source=SourceType.ONEDRIVE,
            access=AccessLevel.임직원,
            author_department="영업부",
            doc_type="DOCX",
        ),
        FileDoc(
            doc_id="other_dept",
            title="공통 제목",
            description="공통 본문",
            source=SourceType.ONEDRIVE,
            access=AccessLevel.임직원,
            author_department="인사부",
            doc_type="DOCX",
        ),
    ]
    for doc in files:
        harness.search_index.index_file(doc)

    # Act: 인증 없이 검색(데모 마스터 컨텍스트, 부서 "").
    response = _search(harness.client, EMP_TOKEN, q="공통 제목", top_k=10)

    # Assert: 부서 가중 없음 → 두 파일 동점, 둘 다 노출.
    assert response.status_code == 200
    hits = response.json()
    assert {hit["doc_id"] for hit in hits} == {"same_dept", "other_dept"}
    assert hits[0]["score"] == hits[1]["score"]
    # 파일 단위 추가 필드(title·doc_type)가 응답에 포함된다(비파괴 확장).
    assert hits[0]["title"] == "공통 제목"
    assert hits[0]["doc_type"] == "DOCX"


def test_ingested_document_is_searchable(harness: Harness, tmp_path: Path) -> None:
    # Arrange: 사장이 자료를 적재.
    doc = _write_doc(tmp_path, "policy.txt", "연차 GAMMA 휴가 정책 안내")
    ingest_response = _ingest(
        harness.client,
        ADMIN_TOKEN,
        [{"file_path": str(doc), "source": "onedrive", "access": 1}],
    )
    assert ingest_response.status_code == 200
    assert ingest_response.json()[0]["ok"] is True

    # Act: 임직원이 적재된 자료를 검색.
    response = _search(harness.client, EMP_TOKEN, q="GAMMA", top_k=10)

    # Assert: 적재 파일이 출처 메타·파일 단위 필드(title·doc_type)와 함께 조회된다.
    assert response.status_code == 200
    hits = response.json()
    assert hits, "적재된 파일이 검색되어야 한다"
    assert any("GAMMA" in hit["text"] for hit in hits)
    assert hits[0]["metadata"]["source"] == "onedrive"
    assert hits[0]["title"] == "policy.txt"
    assert hits[0]["doc_type"] == "TXT"


def test_ingest_no_admin_gate_in_demo(harness: Harness, tmp_path: Path) -> None:
    # 데모 모드(ADR-026): 적재 관리자 게이트 무력화(고정 마스터) — 누구나 적재 가능.
    doc = _write_doc(tmp_path, "x.txt", "적재 시도")
    response = _ingest(
        harness.client,
        EMP_TOKEN,
        [{"file_path": str(doc), "source": "slack", "access": 1}],
    )
    assert response.status_code == 200
    assert all(report["ok"] for report in response.json())
