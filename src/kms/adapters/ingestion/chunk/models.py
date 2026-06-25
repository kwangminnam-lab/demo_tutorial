"""청크 모델 — 검색·RAG 단위 + 상속된 doc-level 메타.

`ChunkMetadata`는 doc-level 메타(`DocumentMetadata`)를 그대로 상속하고
chunk-level 위치 메타를 더한다. doc-level의 `access`·`author_department`를
모든 청크가 갖는 것이 핵심 — 권한 인지 하드 필터(`access`)와 부서 가중
soft boost(`author_department`)는 청크 단위로 적용되기 때문이다 (ARCHITECTURE).
"""

from pydantic import BaseModel

from kms.domain.models import DocumentMetadata


class ChunkMetadata(DocumentMetadata):
    """doc-level 메타(상속) + chunk-level 위치 메타.

    doc-level(`source`·`access`·`author`·`author_department`·`source_url`·
    `ingested_at`)은 `DocumentMetadata`에서 상속한다. chunk-level 필드는
    형식별로 채워진다 (markdown→`header_path`/`page`, slide→`slide_no`,
    workbook→`table_title`/`columns`).
    """

    chunk_index: int
    page: int | None = None
    slide_no: int | None = None
    header_path: list[str] | None = None
    table_title: str | None = None
    columns: list[str] | None = None


class Chunk(BaseModel):
    """검색·RAG 단위 청크 — 본문 + 메타.

    `chunk_id`는 여기서 결정론적 locator(예: `p1:0`, `s2:3`, `t:매출:0`)로
    채운다. 다음 step(ingestion-service)이 doc 콘텐츠 해시를 결합해 전역 고유
    ID로 확정한다 (해시 결합은 이 계층의 책임이 아니다).
    """

    chunk_id: str
    text: str
    metadata: ChunkMetadata
