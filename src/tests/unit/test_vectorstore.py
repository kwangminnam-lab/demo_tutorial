"""InMemoryVectorStore + FakeEmbedder 단위 테스트 (서버·실모델 없음).

검증 핵심:
- 색인 후 같은 텍스트 쿼리가 해당 청크를 상위로 반환.
- `where` 메타 필터로 특정 `source`만 / `access<=N`만 반환 (권한 인지 메커니즘).
- 같은 `chunk_id` 재색인 시 중복 없이 upsert.
- `FakeEmbedder`가 결정론적 (같은 입력 → 같은 벡터, 인스턴스 무관).
- 메타에 `access`(정수)·`author_department`가 저장돼 권한 필터·부서 가중 가능.

각 store는 인메모리라 테스트 함수마다 새로 만들어 상호 오염을 막는다.
"""

from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata
from kms.adapters.vectorstore import FakeEmbedder, InMemoryVectorStore
from kms.domain.access import AccessLevel
from kms.domain.models import SourceType


def _chunk(
    chunk_id: str,
    text: str,
    *,
    source: SourceType = SourceType.ONEDRIVE,
    access: AccessLevel = AccessLevel.임직원,
    department: str | None = "영업부",
    header_path: list[str] | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            source=source,
            access=access,
            author_department=department,
            chunk_index=0,
            header_path=header_path,
        ),
    )


def _store() -> tuple[InMemoryVectorStore, FakeEmbedder]:
    embedder = FakeEmbedder()
    return InMemoryVectorStore(embedder), embedder


def test_index_then_query_returns_relevant_chunk() -> None:
    store, embedder = _store()
    store.index([_chunk("c1", "매출 보고서 내용"), _chunk("c2", "완전히 다른 주제")])

    query_embedding = embedder.embed(["매출 보고서 내용"])[0]
    results = store.query(query_embedding, top_k=2)

    assert results[0][0] == "c1"  # 일치 텍스트가 상위


def test_where_filters_by_source() -> None:
    store, embedder = _store()
    store.index(
        [
            _chunk("od", "원드라이브 문서", source=SourceType.ONEDRIVE),
            _chunk("sl", "슬랙 문서", source=SourceType.SLACK),
        ]
    )

    query_embedding = embedder.embed(["문서"])[0]
    results = store.query(
        query_embedding, top_k=10, where={"source": SourceType.SLACK.value}
    )

    assert {r[0] for r in results} == {"sl"}


def test_where_filters_by_access_level() -> None:
    store, embedder = _store()
    store.index(
        [
            _chunk("emp", "임직원 문서", access=AccessLevel.임직원),
            _chunk("ceo", "사장 문서", access=AccessLevel.사장),
        ]
    )

    # 임직원 레벨 사용자: access<=1 만 허용 (사장 문서 제외) — 메커니즘 검증.
    query_embedding = embedder.embed(["문서"])[0]
    results = store.query(
        query_embedding,
        top_k=10,
        where={"access": {"$lte": int(AccessLevel.임직원)}},
    )

    assert {r[0] for r in results} == {"emp"}


def test_reindex_same_id_upserts_without_duplicate() -> None:
    store, embedder = _store()
    store.index([_chunk("c1", "원본 텍스트")])
    store.index([_chunk("c1", "수정된 텍스트")])

    query_embedding = embedder.embed(["수정된 텍스트"])[0]
    results = store.query(query_embedding, top_k=10)

    assert len(results) == 1  # 중복 없이 갱신
    assert results[0][1] == "수정된 텍스트"


def test_query_returns_access_and_department_metadata() -> None:
    store, embedder = _store()
    store.index([_chunk("c1", "본문", header_path=["대제목", "중제목"])])

    query_embedding = embedder.embed(["본문"])[0]
    _chunk_id, _text, metadata, _score = store.query(query_embedding, top_k=1)[0]

    assert metadata["access"] == int(AccessLevel.임직원)  # 정수 레벨 → 권한 필터
    assert metadata["author_department"] == "영업부"  # 부서 가중 기준
    assert metadata["header_path"] == ["대제목", "중제목"]  # 네이티브 리스트 보존


def test_fake_embedder_is_deterministic() -> None:
    embedder = FakeEmbedder()
    first = embedder.embed(["같은 텍스트"])
    second = embedder.embed(["같은 텍스트"])

    assert first == second
    assert len(first[0]) == 64
    # 인스턴스가 달라도 같은 입력이면 같은 벡터 (프로세스 솔트 비의존).
    assert FakeEmbedder().embed(["같은 텍스트"]) == first
