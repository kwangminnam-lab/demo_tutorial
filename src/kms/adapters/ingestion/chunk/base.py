"""`Chunker` 프로토콜 — 형식별 청킹의 공통 계약.

청커는 IR과 doc-level 메타만 받아 청크 목록을 만든다. 바이너리·파일 의존이
없어 작은 IR 객체로 결정론적으로 테스트된다 (step 0 IR 경계와 같은 원칙).
신규 형식은 이 프로토콜을 구현하고 registry에 IR 타입으로 등록만 한다.
"""

from typing import Protocol

from kms.adapters.ingestion.chunk.models import Chunk
from kms.adapters.ingestion.ir import IR
from kms.domain.models import DocumentMetadata


class Chunker(Protocol):
    """IR을 청크 목록으로 쪼갠다. doc-level 메타를 모든 청크에 상속시킨다."""

    def chunk(self, ir: IR, doc_meta: DocumentMetadata) -> list[Chunk]:
        """IR을 청크로 분할한다. 받은 IR 타입이 맞지 않으면 `TypeError`."""
        ...
