"""GraphStore 단위 테스트 — InMemoryGraphStore (Neo4j 서버 없이 자동 실행).

검증 핵심:
- `add_document`/`add_chunks` 후 `related`가 같은 문서·같은 source의 청크를 반환.
- `related`가 입력 청크 자신과 다른 source 청크는 제외.
- `related`가 사용자 권한(access)보다 높은 청크를 제외 (권한 인지).
- `Neo4jGraphStore`가 `GraphStore` 프로토콜을 만족 (실연결 호출 없음).
"""

from kms.adapters.graph import GraphStore, InMemoryGraphStore, Neo4jGraphStore
from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, SourceType


def _chunk(
    chunk_id: str,
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    author: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=f"본문 {chunk_id}",
        metadata=ChunkMetadata(
            source=source,
            access=access,
            author=author,
            chunk_index=0,
        ),
    )


def _seeded_store() -> InMemoryGraphStore:
    """doc1(c1,c2)·doc2(c3, 같은 onedrive source)·doc3(c4, slack)을 적재."""
    store = InMemoryGraphStore()
    onedrive = ChunkMetadata(
        source=SourceType.ONEDRIVE, access=AccessLevel.임직원, chunk_index=0
    )
    slack = ChunkMetadata(
        source=SourceType.SLACK, access=AccessLevel.임직원, chunk_index=0
    )
    store.add_document("doc1", onedrive)
    store.add_chunks("doc1", [_chunk("c1"), _chunk("c2")])
    store.add_document("doc2", onedrive)
    store.add_chunks("doc2", [_chunk("c3")])
    store.add_document("doc3", slack)
    store.add_chunks("doc3", [_chunk("c4", source=SourceType.SLACK)])
    return store


def test_related_returns_same_document_chunk() -> None:
    store = _seeded_store()

    related = store.related(["c1"], AccessLevel.임직원)

    assert "c2" in related  # 같은 문서(doc1)의 형제 청크


def test_related_returns_same_source_chunk() -> None:
    store = _seeded_store()

    related = store.related(["c1"], AccessLevel.임직원)

    assert "c3" in related  # 다른 문서지만 같은 source(onedrive)


def test_related_excludes_seed_and_unrelated_source() -> None:
    store = _seeded_store()

    related = store.related(["c1"], AccessLevel.임직원)

    assert "c1" not in related  # 입력 청크 자신 제외
    assert "c4" not in related  # 다른 source(slack) 제외


def test_related_excludes_higher_access_chunk() -> None:
    store = _seeded_store()
    # 같은 source(onedrive)지만 사장 권한 문서 — 임직원에게는 안 보여야 한다.
    store.add_document(
        "doc_ceo",
        ChunkMetadata(
            source=SourceType.ONEDRIVE, access=AccessLevel.사장, chunk_index=0
        ),
    )
    store.add_chunks("doc_ceo", [_chunk("ceo", access=AccessLevel.사장)])

    employee_view = store.related(["c1"], AccessLevel.임직원)
    ceo_view = store.related(["c1"], AccessLevel.사장)

    assert "ceo" not in employee_view  # 권한 밖 — 관계 확장에서도 제외
    assert "ceo" in ceo_view  # 사장 권한이면 보인다


def test_related_respects_limit() -> None:
    store = _seeded_store()

    related = store.related(["c1"], AccessLevel.임직원, limit=1)

    assert len(related) == 1


def test_neo4j_store_satisfies_graphstore_protocol() -> None:
    # 드라이버 생성은 lazy — 인스턴스화만으로는 서버에 연결하지 않는다.
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "password")

    assert isinstance(store, GraphStore)  # 인터페이스 일치 (실연결 없음)


# ── 사용자-콘텐츠 그래프 ──────────────────────────────────────────────
def _doc_meta(
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    author: str | None = None,
    author_department: str | None = None,
) -> DocumentMetadata:
    return DocumentMetadata(
        source=source,
        access=access,
        author=author,
        author_department=author_department,
    )


def test_related_expands_via_same_user_authored_doc() -> None:
    """seed 문서의 author(User)가 쓴 다른 문서의 청크까지 관계 확장된다."""
    store = InMemoryGraphStore()
    # 같은 author 'alice'가 쓴 두 문서 (다른 source — chunk-level 매칭은 안 됨).
    store.add_document(
        "docA",
        _doc_meta(source=SourceType.ONEDRIVE, author="alice@corp", author_department="기획"),
    )
    store.add_chunks("docA", [_chunk("cA1")])
    store.add_document(
        "docB",
        _doc_meta(source=SourceType.SLACK, author="alice@corp", author_department="기획"),
    )
    store.add_chunks("docB", [_chunk("cB1", source=SourceType.SLACK)])
    # 무관한 사용자의 문서 (다른 author, 다른 source) — 포함되면 안 됨.
    store.add_document(
        "docC",
        _doc_meta(source=SourceType.GOOGLEDRIVE, author="bob@corp", author_department="영업"),
    )
    store.add_chunks("docC", [_chunk("cC1", source=SourceType.GOOGLEDRIVE)])

    related = store.related(["cA1"], AccessLevel.임직원)

    assert "cB1" in related  # 같은 User가 쓴 다른 문서의 청크 — User-AUTHORED 경로
    assert "cC1" not in related  # 다른 author·source·dept — 연결 없음


def test_related_expands_via_same_department_user() -> None:
    """같은 부서 작성자(User)가 쓴 다른 문서의 청크까지 확장된다."""
    store = InMemoryGraphStore()
    store.add_document(
        "doc1",
        _doc_meta(source=SourceType.ONEDRIVE, author="alice@corp", author_department="기획"),
    )
    store.add_chunks("doc1", [_chunk("c1")])
    # 같은 부서(기획)의 다른 작성자 carol — 다른 source.
    store.add_document(
        "doc2",
        _doc_meta(source=SourceType.GOOGLEDRIVE, author="carol@corp", author_department="기획"),
    )
    store.add_chunks("doc2", [_chunk("c2", source=SourceType.GOOGLEDRIVE)])
    # 다른 부서(영업) — 포함되면 안 됨.
    store.add_document(
        "doc3",
        _doc_meta(source=SourceType.SLACK, author="dan@corp", author_department="영업"),
    )
    store.add_chunks("doc3", [_chunk("c3", source=SourceType.SLACK)])

    related = store.related(["c1"], AccessLevel.임직원)

    assert "c2" in related  # 같은 부서 다른 User가 쓴 문서
    assert "c3" not in related  # 다른 부서 — 연결 없음


def test_related_user_expansion_respects_access() -> None:
    """User 경로 확장에서도 권한 hard filter는 유지된다."""
    store = InMemoryGraphStore()
    store.add_document(
        "doc_emp",
        _doc_meta(source=SourceType.ONEDRIVE, author="alice@corp", author_department="기획"),
    )
    store.add_chunks("doc_emp", [_chunk("c_emp")])
    # 같은 author지만 사장 권한 문서.
    store.add_document(
        "doc_ceo",
        _doc_meta(
            source=SourceType.SLACK,
            access=AccessLevel.사장,
            author="alice@corp",
            author_department="기획",
        ),
    )
    store.add_chunks("doc_ceo", [_chunk("c_ceo", source=SourceType.SLACK, access=AccessLevel.사장)])

    employee_view = store.related(["c_emp"], AccessLevel.임직원)
    ceo_view = store.related(["c_emp"], AccessLevel.사장)

    assert "c_ceo" not in employee_view  # 임직원에게는 권한 밖 — User 경로여도 차단
    assert "c_ceo" in ceo_view  # 사장 권한이면 User 경로 확장으로 포함
