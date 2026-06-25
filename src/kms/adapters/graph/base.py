"""그래프 저장소 인터페이스 — 문서·청크와 그 관계를 저장·탐색한다.

관계 기반 보강 검색(ADR-007 RAG)의 경계. 의미 유사도(vectorstore)와 달리
여기서는 같은 문서·같은 `source`/`author`로 연결된 청크를 확장 탐색한다.
권한 인지(ADR-005)는 `related`에서 강제한다 — 관계 확장으로 사용자 권한 밖
문서가 새지 않도록 `access` 허용분만 반환한다.

구현은 두 개: `InMemoryGraphStore`(테스트·dev 기본, Neo4j 서버 불요),
`Neo4jGraphStore`(실구현). 둘은 같은 인터페이스를 만족한다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kms.adapters.ingestion.chunk.models import Chunk
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata


@runtime_checkable
class GraphStore(Protocol):
    """문서·청크 그래프 저장소 계약."""

    def add_document(self, doc_id: str, metadata: DocumentMetadata) -> None:
        """문서 노드와 `source`·`author`·`author_department`·`access` 속성을 적재한다."""
        ...

    def add_chunks(self, doc_id: str, chunks: list[Chunk]) -> None:
        """청크 노드와 `(:Chunk)-[:PART_OF]->(:Document)` 관계를 적재한다."""
        ...

    def delete(self, doc_id: str) -> None:
        """문서 노드와 그 문서에 PART_OF로 연결된 청크 노드를 삭제한다.

        AUTHORED한 User 노드는 다른 문서를 가질 수 있어 남긴다(고아 User는 무해).
        """
        ...

    def related(
        self,
        chunk_ids: list[str],
        user_access_level: AccessLevel,
        limit: int = 10,
    ) -> list[str]:
        """주어진 청크와 연결된(같은 문서·같은 source/author) 청크 id를 반환한다.

        입력 청크 자신은 제외하고, **사용자 권한(`user_access_level`)으로 접근
        가능한 청크만** 반환한다(권한 인지 — 관계 확장으로 권한 밖 문서가 새지
        않게 강제). `limit` 개수만큼 반환한다.
        """
        ...
