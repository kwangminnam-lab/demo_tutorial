"""Neo4jGraphStore 통합/계약 테스트 — 실 Neo4j가 떠 있을 때만 돈다.

`InMemoryGraphStore`와 같은 계약(문서·청크 적재, 권한 필터 관계 확장)을 실 서버에서
검증한다. neo4j 드라이버 미설치 또는 서버 미도달이면 skip하므로 CI 기본
(`pytest -m "not integration"`)에는 영향이 없다.

회귀 방지(중요): `add_chunks`의 Cypher는 Neo4j 5에서 `MERGE`↔`UNWIND` 사이 `WITH`가
없으면 SyntaxError가 난다. graph backend를 memory로만 테스트하면 못 잡히므로, 실
Neo4j에 대해 add_chunks→related 라운드트립을 잠근다.

실행: `docker compose up -d neo4j` 후
`NEO4J_PASSWORD=... pytest -q tests/integration/test_graph_store_neo4j.py`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

pytest.importorskip("neo4j")

from kms.adapters.graph.neo4j_store import Neo4jGraphStore  # noqa: E402
from kms.adapters.ingestion.chunk import Chunk, ChunkMetadata  # noqa: E402
from kms.domain.access import AccessLevel  # noqa: E402
from kms.domain.models import DocumentMetadata, SourceType  # noqa: E402

pytestmark = pytest.mark.integration

_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
_USER = os.environ.get("NEO4J_USER", "neo4j")
_PASSWORD = os.environ.get("NEO4J_PASSWORD", "devdocux1234")


def _chunk(chunk_id: str, *, access: AccessLevel = AccessLevel.임직원) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text="본문",
        metadata=ChunkMetadata(
            source=SourceType.ONEDRIVE,
            access=access,
            author="남광민",
            author_department="영업",
            chunk_index=0,
        ),
    )


@pytest.fixture
def store() -> Iterator[Neo4jGraphStore]:
    """매 테스트 고유 doc_id로 격리. 미도달이면 skip, 끝나면 생성 노드 정리."""
    from neo4j.exceptions import Neo4jError, ServiceUnavailable

    s = Neo4jGraphStore(_URI, _USER, _PASSWORD)
    try:
        s.ping()
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        pytest.skip(f"Neo4j 미도달 — 통합 테스트 skip ({type(exc).__name__})")
    yield s
    s.close()


def test_add_chunks_and_related_roundtrip(store: Neo4jGraphStore) -> None:
    # add_chunks 의 MERGE→WITH→UNWIND Cypher가 Neo4j 5에서 SyntaxError 없이 돈다(회귀).
    doc_id = f"doc-{uuid.uuid4().hex}"
    meta = DocumentMetadata(
        source=SourceType.ONEDRIVE,
        access=AccessLevel.임직원,
        author="남광민",
        author_department="영업",
    )
    a, b = f"{doc_id}:c0", f"{doc_id}:c1"
    store.add_document(doc_id, meta)
    store.add_chunks(doc_id, [_chunk(a), _chunk(b)])

    # 같은 문서(PART_OF) 형제 → seed c0 의 관계 확장에 c1 이 잡힌다.
    # limit은 크게 — 공유 DB에 같은 source/author 청크가 많아도 형제가 잘리지 않게(오염 내성).
    related = store.related([a], AccessLevel.임직원, limit=100_000)
    assert b in related


def test_related_enforces_access_filter(store: Neo4jGraphStore) -> None:
    doc_id = f"doc-{uuid.uuid4().hex}"
    meta = DocumentMetadata(source=SourceType.ONEDRIVE, access=AccessLevel.임직원)
    seed = f"{doc_id}:seed"
    secret = f"{doc_id}:secret"
    store.add_document(doc_id, meta)
    store.add_chunks(
        doc_id, [_chunk(seed), _chunk(secret, access=AccessLevel.사장)]
    )

    # 임직원 권한(access<=1)으로는 사장(access=2) 청크가 확장에서 제외된다.
    # access 필터는 AND 조건이라 limit과 무관하게 secret은 절대 나오지 않아야 한다.
    related = store.related([seed], AccessLevel.임직원, limit=100_000)
    assert secret not in related
