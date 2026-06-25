"""형식별 청커 + registry.

IR(`MarkdownDoc`/`SlideDeck`/`Workbook`)을 검색·RAG 단위 `Chunk`로 쪼갠다.
모든 청크는 doc-level 메타(`access`·`author_department` 등)를 상속해 권한 인지
필터·부서 가중에 쓰일 수 있게 한다. 구조 분할 후 size-cap 2차 분할로 거대 청크를
막는다.
"""

from kms.adapters.ingestion.chunk.base import Chunker
from kms.adapters.ingestion.chunk.markdown_chunker import MarkdownDocChunker
from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.ingestion.chunk.registry import (
    UnsupportedIRError,
    get_chunker,
)
from kms.adapters.ingestion.chunk.slide_chunker import SlideDeckChunker
from kms.adapters.ingestion.chunk.workbook_chunker import WorkbookChunker

__all__ = [
    "Chunk",
    "ChunkMetadata",
    "Chunker",
    "MarkdownDocChunker",
    "SlideDeckChunker",
    "WorkbookChunker",
    "get_chunker",
    "UnsupportedIRError",
]
