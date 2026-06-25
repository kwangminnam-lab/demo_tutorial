"""MarkdownDoc 청커 — 계층형(헤더 분할 → size-cap 2차 분할).

전략 (step 1 task):
1. `MarkdownHeaderTextSplitter`로 `#`/`##`/`###` 기준 섹션 분할 → `header_path`.
2. 각 섹션을 `RecursiveCharacterTextSplitter`로 size-cap 2차 분할 (거대 청크 방지).
3. 헤더가 없으면 헤더 분할이 본문 전체를 1개 섹션으로 돌려주므로, 그대로
   2차 분할만 적용된다 (header_path=None) — 별도 fallback 분기 불필요.
4. `page_map`이 있으면 청크 본문의 시작 오프셋으로 `page`를 추정해 기록한다.
"""

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.ingestion.ir import IR, MarkdownDoc
from kms.domain.models import DocumentMetadata

#: 헤더 레벨 → 메타 키 (순서가 곧 header_path 트리 순서).
_HEADER_LEVELS: list[tuple[str, str]] = [("#", "h1"), ("##", "h2"), ("###", "h3")]


class MarkdownDocChunker:
    """PDF/DOCX 등 마크다운 IR을 헤더 계층 + size-cap으로 청킹한다."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 200) -> None:
        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_HEADER_LEVELS
        )
        self._size_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )

    def chunk(self, ir: IR, doc_meta: DocumentMetadata) -> list[Chunk]:
        if not isinstance(ir, MarkdownDoc):
            raise TypeError(f"MarkdownDocChunker는 MarkdownDoc만 처리한다: {type(ir)}")

        chunks: list[Chunk] = []
        index = 0
        for section in self._header_splitter.split_text(ir.markdown):
            header_path = self._header_path(section.metadata)
            for text in self._size_splitter.split_text(section.page_content):
                page = self._estimate_page(ir, text)
                locator = f"p{page}:{index}" if page is not None else f"c:{index}"
                chunks.append(
                    Chunk(
                        chunk_id=locator,
                        text=text,
                        metadata=ChunkMetadata(
                            **doc_meta.model_dump(),
                            chunk_index=index,
                            page=page,
                            header_path=header_path,
                        ),
                    )
                )
                index += 1
        return chunks

    @staticmethod
    def _header_path(metadata: dict[str, str]) -> list[str] | None:
        """헤더 분할 메타에서 상위→하위 헤더 텍스트 목록을 만든다 (없으면 None)."""
        path = [metadata[key] for _, key in _HEADER_LEVELS if key in metadata]
        return path or None

    @staticmethod
    def _estimate_page(ir: MarkdownDoc, text: str) -> int | None:
        """청크 본문 시작 오프셋이 속한 페이지를 추정한다.

        `page_map`이 없으면(DOCX/TXT) None. 헤더가 제거된 본문 텍스트는 원본
        마크다운에 그대로 존재하므로 find로 시작 오프셋을 찾고, 그 오프셋 이하의
        마지막 페이지 경계를 페이지로 본다. 정확한 매핑이 아니라 추정값이다.
        """
        if not ir.page_map:
            return None
        offset = ir.markdown.find(text[:60]) if text else -1
        if offset < 0:
            offset = 0
        page = ir.page_map[0][1]
        for boundary, page_no in ir.page_map:
            if boundary <= offset:
                page = page_no
            else:
                break
        return page
