"""청커 registry — IR 타입으로 알맞은 청커를 고른다.

신규 형식 추가 = 새 `Chunker` 구현 + 이 매핑에 한 줄 등록. 코어 로직은 손대지
않는다. 기본 파라미터로 인스턴스를 만들어 둔다 — 이 계층은 `get_settings()`를
호출하지 않아 환경 변수 없이도 import·테스트된다 (DB URL 등 불필요). 설정값 주입은
다음 step(ingestion-service)이 청커를 직접 생성해 처리한다.
"""

from kms.adapters.ingestion.chunk.base import Chunker
from kms.adapters.ingestion.chunk.markdown_chunker import MarkdownDocChunker
from kms.adapters.ingestion.chunk.slide_chunker import SlideDeckChunker
from kms.adapters.ingestion.chunk.workbook_chunker import WorkbookChunker
from kms.adapters.ingestion.ir import IR, MarkdownDoc, SlideDeck, Workbook

#: IR 타입 → 청커 인스턴스. 청커는 무상태라 공유해도 안전하다.
_REGISTRY: dict[type, Chunker] = {
    MarkdownDoc: MarkdownDocChunker(),
    SlideDeck: SlideDeckChunker(),
    Workbook: WorkbookChunker(),
}


class UnsupportedIRError(Exception):
    """등록된 청커가 없는 IR 타입 — 청킹 거부 사유(도메인 에러)."""


def get_chunker(ir: IR) -> Chunker:
    """IR 타입에 맞는 청커를 반환한다. 없으면 `UnsupportedIRError`."""
    chunker = _REGISTRY.get(type(ir))
    if chunker is None:
        raise UnsupportedIRError(f"지원하지 않는 IR 타입: {type(ir).__name__}")
    return chunker
