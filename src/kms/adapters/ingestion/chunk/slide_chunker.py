"""SlideDeck 청커 — 슬라이드 단위 (제목+본문+노트).

슬라이드 1장 = 1 청크가 기본. 한 장이 `chunk_size`를 넘으면 같은 `slide_no`를
유지한 채 size-cap 2차 분할한다. 본문·노트가 모두 빈 슬라이드는 색인 가치가
없어 건너뛰고 경고를 남긴다 (조용히 삼키지 않음 — CONVENTIONS 부분 실패 규약).
"""

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.ingestion.ir import IR, SlideDeck
from kms.domain.models import DocumentMetadata

logger = logging.getLogger(__name__)


class SlideDeckChunker:
    """PPTX 슬라이드를 장 단위로 청킹한다 (제목+본문+스피커노트)."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 200) -> None:
        self._chunk_size = chunk_size
        self._size_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    def chunk(self, ir: IR, doc_meta: DocumentMetadata) -> list[Chunk]:
        if not isinstance(ir, SlideDeck):
            raise TypeError(f"SlideDeckChunker는 SlideDeck만 처리한다: {type(ir)}")

        chunks: list[Chunk] = []
        index = 0
        for slide in ir.slides:
            parts = [part for part in (slide.title, slide.body, slide.notes) if part]
            text = "\n".join(parts)
            if not text.strip():
                logger.warning("빈 슬라이드 스킵", extra={"slide_no": slide.number})
                continue
            texts = (
                self._size_splitter.split_text(text)
                if len(text) > self._chunk_size
                else [text]
            )
            for body in texts:
                chunks.append(
                    Chunk(
                        chunk_id=f"s{slide.number}:{index}",
                        text=body,
                        metadata=ChunkMetadata(
                            **doc_meta.model_dump(),
                            chunk_index=index,
                            slide_no=slide.number,
                        ),
                    )
                )
                index += 1
        return chunks
