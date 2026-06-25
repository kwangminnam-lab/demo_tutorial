"""SearchService 단위 테스트 — 권한 필터(하드) + 부서 가중(soft).

FakeEmbedder + 임시 디스크 Chroma + InMemoryGraphStore로 실모델·서버 없이 돈다.
FakeEmbedder는 같은 텍스트 → 같은 벡터라, 동일 텍스트 청크 두 개는 쿼리와의
유사도가 같다 — 그 동점을 부서 가중이 어떻게 깨는지 결정론적으로 검증할 수 있다.

검증 핵심(AC):
- 권한: 사용자(임직원)보다 높은 access(사장) 문서는 결과에 절대 안 나온다.
- 부서 가중: 유사도 동점일 때 같은 author_department 문서가 상위로 온다.
- 권한 우회 방지: 권한 밖 문서는 부서 가중이 있어도 노출되지 않는다.
- source 필터가 동작한다.
"""

from pathlib import Path

from kms.adapters.graph import InMemoryGraphStore
from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata
from kms.adapters.reranker import FakeReranker
from kms.adapters.searchindex import InMemorySearchIndex
from kms.adapters.vectorstore import InMemoryVectorStore, FakeEmbedder
from kms.config.settings import Settings
from kms.domain.access import AccessLevel
from kms.domain.models import FileDoc, SearchQuery, SourceType, UserContext
from kms.services.search_service import SearchService

USER = UserContext(user_id="u1", department="영업부", access_level=AccessLevel.임직원)


def _chunk(
    chunk_id: str,
    text: str,
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    department: str | None = "영업부",
    source_url: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            source=source,
            access=access,
            author_department=department,
            source_url=source_url,
            chunk_index=0,
        ),
    )


def _service(tmp_path: Path, chunks: list[Chunk]) -> SearchService:
    """주어진 청크를 vectorstore·graph 양쪽에 적재한 SearchService를 만든다."""
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    vectorstore.index(chunks)
    for chunk in chunks:
        doc_id = f"doc-{chunk.chunk_id}"
        graph.add_document(doc_id, chunk.metadata)
        graph.add_chunks(doc_id, [chunk])
    # 시크릿 기본값 없는 필드는 명시값으로 채운다(테스트 결정론). boost는 기본 0.2.
    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )
    return SearchService(vectorstore, graph, embedder, settings, InMemorySearchIndex())


def test_search_excludes_higher_access_documents(tmp_path: Path) -> None:
    # Arrange: 같은 텍스트의 임직원 문서 + 사장 문서.
    service = _service(
        tmp_path,
        [
            _chunk("emp", "공통 본문", access=AccessLevel.임직원),
            _chunk("ceo", "공통 본문", access=AccessLevel.사장),
        ],
    )

    # Act: 임직원 사용자로 검색.
    results = service.search(SearchQuery(text="공통 본문"), USER)

    # Assert: 사장 문서는 retrieval 단계에서 제외 — 결과에 절대 없다.
    ids = {result.document.doc_id for result in results}
    assert "emp" in ids
    assert "ceo" not in ids


def test_search_same_department_ranks_higher(tmp_path: Path) -> None:
    # Arrange: 유사도 동점(같은 텍스트), 부서만 다른 두 문서.
    service = _service(
        tmp_path,
        [
            _chunk("same_dept", "공통 본문", department="영업부"),
            _chunk("other_dept", "공통 본문", department="인사부"),
        ],
    )

    # Act
    results = service.search(SearchQuery(text="공통 본문"), USER)

    # Assert: 같은 부서(영업부) 문서가 부서 가중으로 상위.
    assert results[0].document.doc_id == "same_dept"
    assert results[1].document.doc_id == "other_dept"
    assert results[0].score > results[1].score


def test_search_does_not_surface_unauthorized_doc_despite_boost(tmp_path: Path) -> None:
    # Arrange: 권한 밖(사장) 문서가 사용자와 같은 부서 — 가중이 있어도 노출 금지.
    service = _service(
        tmp_path,
        [
            _chunk("emp_other_dept", "공통 본문", department="인사부"),
            _chunk(
                "ceo_same_dept",
                "공통 본문",
                access=AccessLevel.사장,
                department="영업부",
            ),
        ],
    )

    # Act
    results = service.search(SearchQuery(text="공통 본문"), USER)

    # Assert: 부서 가중 대상이지만 권한 밖이므로 결과에 없다(권한 우회 방지).
    ids = {result.document.doc_id for result in results}
    assert "ceo_same_dept" not in ids
    assert "emp_other_dept" in ids


def test_search_reranker_reorders_top_n(tmp_path: Path) -> None:
    """reranker_enabled=True + Reranker 주입 시 cross-encoder 점수로 top-N이 재정렬된다."""
    # Arrange: 같은 source/access이지만 본문 토큰이 다른 청크 두 개.
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    c_low = _chunk("c_low", "공통 본문 alpha")        # query에 없는 단어
    c_high = _chunk("c_high", "공통 본문 키워드 매치") # query 단어 직접 포함
    vectorstore.index([c_low, c_high])
    for c in (c_low, c_high):
        graph.add_document(f"doc-{c.chunk_id}", c.metadata)
        graph.add_chunks(f"doc-{c.chunk_id}", [c])

    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
        reranker_enabled=True,
        reranker_top_n=10,
    )
    service = SearchService(
        vectorstore, graph, embedder, settings, InMemorySearchIndex(),
        reranker=FakeReranker(),
    )

    # Act: 'c_high' 본문에 들어 있는 단어를 포함하는 질의 — FakeReranker가 더 높은 점수.
    results = service.search(SearchQuery(text="공통 본문 키워드 매치"), USER)

    # Assert: c_high가 상단(cross-encoder 점수로 재정렬됨).
    assert results[0].document.doc_id == "c_high"


def test_search_reranker_disabled_keeps_rrf_order(tmp_path: Path) -> None:
    """reranker_enabled=False면 reranker가 주입돼 있어도 적용 안 됨 (회귀 가드)."""
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    c1 = _chunk("c1", "공통 본문 a")
    c2 = _chunk("c2", "공통 본문 b")
    vectorstore.index([c1, c2])
    for c in (c1, c2):
        graph.add_document(f"doc-{c.chunk_id}", c.metadata)
        graph.add_chunks(f"doc-{c.chunk_id}", [c])
    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
        reranker_enabled=False,
    )
    service = SearchService(
        vectorstore, graph, embedder, settings, InMemorySearchIndex(),
        reranker=FakeReranker(),
    )

    results = service.search(SearchQuery(text="공통 본문"), USER)
    # 둘 다 결과에 포함 (재정렬 X — 동점일 수 있음).
    ids = {r.document.doc_id for r in results}
    assert {"c1", "c2"} <= ids


def test_search_rrf_combines_vector_and_bm25(tmp_path: Path) -> None:
    """RRF 융합 — BM25(어휘 인덱스)에만 잡힌 파일이 후보로 합류한다."""
    # Arrange: 청크는 vector store에만 있고, 별도의 파일 doc은 BM25(SearchIndex)에 추가.
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()

    # 벡터에 들어가는 청크 — source_url을 두 종류로.
    vec_chunk = _chunk(
        "vec_hit", "벡터 본문", source_url="local://vec.pdf"
    )
    bm25_chunk = _chunk(
        "bm25_only", "어휘 본문", source_url="local://bm25.pdf"
    )
    vectorstore.index([vec_chunk, bm25_chunk])
    graph.add_document("doc-vec", vec_chunk.metadata)
    graph.add_chunks("doc-vec", [vec_chunk])
    graph.add_document("doc-bm25", bm25_chunk.metadata)
    graph.add_chunks("doc-bm25", [bm25_chunk])

    # BM25 SearchIndex에 bm25_chunk의 파일만 별도 등록 — RRF로 끌어올림 검증.
    si = InMemorySearchIndex()
    si.index_file(
        FileDoc(
            doc_id="doc-bm25",
            title="어휘 본문",
            description="어휘 본문",
            source=SourceType.ONEDRIVE,
            source_url="local://bm25.pdf",
            doc_type="PDF",
            access=AccessLevel.임직원,
        )
    )

    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )
    service = SearchService(vectorstore, graph, embedder, settings, si)

    # Act: BM25에만 강하게 매치되는 질의.
    results = service.search(SearchQuery(text="어휘 본문"), USER)

    # Assert: BM25 매치된 청크가 결과에 포함되고, RRF score > 0.
    ids = {r.document.doc_id for r in results}
    assert "bm25_only" in ids  # BM25 단독 매치도 RRF에 의해 후보화
    bm25_result = next(r for r in results if r.document.doc_id == "bm25_only")
    assert bm25_result.score > 0  # RRF score (bm25_part > 0)


def test_search_excludes_disabled_sources(tmp_path: Path) -> None:
    """disabled_sources에 포함된 source는 결과에 등장하지 않는다 (커넥터 토글 OFF)."""
    service = _service(
        tmp_path,
        [
            _chunk("od", "공통 본문", source=SourceType.ONEDRIVE),
            _chunk("sl", "공통 본문", source=SourceType.SLACK),
            _chunk("gd", "공통 본문", source=SourceType.GOOGLEDRIVE),
        ],
    )
    results = service.search(
        SearchQuery(text="공통 본문", disabled_sources=[SourceType.SLACK]), USER
    )
    ids = {r.document.doc_id for r in results}
    assert "sl" not in ids
    assert "od" in ids
    assert "gd" in ids


def test_search_disabled_sources_can_exclude_multiple(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        [
            _chunk("od", "공통 본문", source=SourceType.ONEDRIVE),
            _chunk("sl", "공통 본문", source=SourceType.SLACK),
            _chunk("gd", "공통 본문", source=SourceType.GOOGLEDRIVE),
        ],
    )
    results = service.search(
        SearchQuery(
            text="공통 본문",
            disabled_sources=[SourceType.SLACK, SourceType.GOOGLEDRIVE],
        ),
        USER,
    )
    ids = {r.document.doc_id for r in results}
    assert ids == {"od"}  # OneDrive만 남음


def test_search_source_filter_returns_only_matching_source(tmp_path: Path) -> None:
    # Arrange: onedrive + slack 문서.
    service = _service(
        tmp_path,
        [
            _chunk("od", "공통 본문", source=SourceType.ONEDRIVE),
            _chunk("sl", "공통 본문", source=SourceType.SLACK),
        ],
    )

    # Act: slack만 필터.
    results = service.search(
        SearchQuery(text="공통 본문", source_filter=SourceType.SLACK), USER
    )

    # Assert: slack 문서만 반환.
    ids = {result.document.doc_id for result in results}
    assert ids == {"sl"}


# --- search_files: 어휘 인덱스(SearchIndex) 위임 -------------------------------
#
# search_files는 청크 의미검색(search)과 분리된 파일 단위 어휘 인덱스에 위임한다.
# 아래 헬퍼는 InMemorySearchIndex에 FileDoc을 직접 적재한 SearchService를 만든다
# (search_files는 인덱스만 쓰므로 vectorstore/graph는 빈 채로 둔다).


def _file(
    doc_id: str,
    title: str,
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    department: str | None = "영업부",
    doc_type: str | None = "DOCX",
) -> FileDoc:
    return FileDoc(
        doc_id=doc_id,
        title=title,
        description=title,
        source=source,
        access=access,
        author_department=department,
        doc_type=doc_type,
    )


def _file_service(tmp_path: Path, files: list[FileDoc]) -> SearchService:
    """주어진 FileDoc을 어휘 인덱스에 적재한 SearchService를 만든다."""
    embedder = FakeEmbedder()
    vectorstore = InMemoryVectorStore(embedder)
    graph = InMemoryGraphStore()
    search_index = InMemorySearchIndex()
    for doc in files:
        search_index.index_file(doc)
    settings = Settings(
        database_url="postgresql://test",
        neo4j_uri="bolt://test",
        neo4j_user="u",
        neo4j_password="p",
    )
    return SearchService(vectorstore, graph, embedder, settings, search_index)


def test_search_files_excludes_higher_access_files(tmp_path: Path) -> None:
    # Arrange: 같은 제목의 임직원 파일 + 사장 파일.
    service = _file_service(
        tmp_path,
        [
            _file("emp", "라이선스 정책", access=AccessLevel.임직원),
            _file("ceo", "라이선스 정책", access=AccessLevel.사장),
        ],
    )

    # Act: 임직원 사용자로 파일 검색.
    results = service.search_files(SearchQuery(text="라이선스 정책"), USER)

    # Assert: 권한 밖(사장) 파일은 어휘 인덱스 단계에서 제외 — FileHit 단위 반환.
    ids = {hit.file.doc_id for hit in results}
    assert "emp" in ids
    assert "ceo" not in ids


def test_search_files_same_department_ranks_higher(tmp_path: Path) -> None:
    # Arrange: 어휘 점수 동점(같은 제목)·부서만 다른 두 파일.
    service = _file_service(
        tmp_path,
        [
            _file("same_dept", "공통 제목", department="영업부"),
            _file("other_dept", "공통 제목", department="인사부"),
        ],
    )

    # Act
    results = service.search_files(SearchQuery(text="공통 제목"), USER)

    # Assert: 같은 부서(영업부) 파일이 부서 가중으로 상위.
    assert results[0].file.doc_id == "same_dept"
    assert results[1].file.doc_id == "other_dept"
    assert results[0].score > results[1].score


def test_search_files_truncates_to_top_k_files(tmp_path: Path) -> None:
    # Arrange: 같은 제목의 서로 다른 파일 3개.
    service = _file_service(
        tmp_path,
        [
            _file("f1", "공통 제목"),
            _file("f2", "공통 제목"),
            _file("f3", "공통 제목"),
        ],
    )

    # Act: top_k=2 파일만 요청.
    results = service.search_files(SearchQuery(text="공통 제목", top_k=2), USER)

    # Assert: 파일 2건으로 잘린다.
    assert len(results) == 2
