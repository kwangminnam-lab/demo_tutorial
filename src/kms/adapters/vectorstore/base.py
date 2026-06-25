"""벡터 저장소 어댑터 인터페이스 — 청크 임베딩 색인·의미 검색 경계.

어휘 검색(adapters/searchindex)과 **분리**된 의미(임베딩 유사도) 검색 경계다.
권한 정책 판단(어떤 access 허용?)은 여기 책임이 아니다(search-service) — 여기서는
임의 메타 `where` 필터를 받아 해석하는 메커니즘만 제공한다.

구현은 두 개로, 같은 인터페이스 + 같은 `where` 방언을 만족한다:
`InMemoryVectorStore`(테스트·dev 기본, 서버 불요), `OpenSearchVectorStore`
(실구현, OpenSearch k-NN). graph(memory/neo4j)·searchindex(memory/opensearch)와
동일한 backend 격리 패턴(ADR-007).

`where` 방언(두 구현 공통):
- `{"필드": 값}` — 등호
- `{"필드": {"$lte"|"$gte"|"$lt"|"$gt"|"$ne"|"$eq": 값}}`
- `{"필드": {"$in"|"$nin": [값, ...]}}`
- `{"$and"|"$or": [절, ...]}` — 절 결합
권한 인지(ADR-005)는 `{"access": {"$lte": N}}`로 retrieval 단계에 박는다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata


@runtime_checkable
class VectorStore(Protocol):
    """청크 임베딩 색인·검색 계약 (어휘 검색 SearchIndex와 분리)."""

    def index(self, chunks: list[Chunk]) -> None:
        """청크를 `chunk_id`로 upsert한다 — 같은 ID 재색인 시 중복 없이 갱신."""
        ...

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, dict[str, Any], float]]:
        """임베딩으로 top_k 청크를 검색한다.

        `where`는 임의 메타 필터(위 방언). 반환은 `(chunk_id, text, metadata, score)`
        목록이며 score는 유사도(클수록 관련도 높음).
        """
        ...

    def get(self, chunk_ids: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
        """`chunk_id` 목록으로 청크 본문·메타를 조회한다 (점수 없음).

        그래프 보강(search-service)이 확장한 청크 id를 본문·메타로 해소하는 데 쓴다.
        존재하지 않는 id는 결과에서 빠진다. 반환은 `(chunk_id, text, metadata)`.
        """
        ...

    def ping(self) -> None:
        """도달성 확인(헬스체크용). 미도달이면 예외 전파(조용한 OK 금지)."""
        ...

    def delete_by_source_url(self, source_url: str) -> int:
        """해당 source_url의 청크를 모두 삭제하고 삭제 건수를 반환한다.

        청크 메타엔 doc_id가 없고 source_url이 있으므로 문서 단위 삭제는 source_url로
        한다(같은 문서의 모든 청크는 같은 source_url). 호출자(서비스)가 doc_id→
        source_url을 FileDoc으로 해소해 넘긴다.
        """
        ...


def chunk_to_meta(meta: ChunkMetadata) -> dict[str, Any]:
    """ChunkMetadata → 저장용 메타 dict (두 구현 공통).

    None 값은 제외하고, `model_dump(mode="json")`이 enum을 원시값(`source`→str,
    `access`→int)·datetime을 ISO 문자열로 평탄화한다. 리스트(`header_path`·
    `columns`)는 네이티브 배열로 둔다 — InMemory(dict)·OpenSearch(array) 모두
    배열을 그대로 저장하므로 Chroma 때의 JSON-string 직렬화는 불필요하다.
    """
    raw = meta.model_dump(mode="json")
    return {key: value for key, value in raw.items() if value is not None}
