"""Workbook 청커 — 표 행 보존 (행 그룹 + 컬럼 헤더 복사).

시트(table)별로 행을 `xlsx_rows_per_chunk`개씩 묶어 청크한다. 각 청크 상단에
컬럼 헤더(마크다운 표 헤더+구분선)를 복사해, 청크 하나만 봐도 어떤 컬럼인지
알 수 있게 자기설명적으로 만든다. `table_title`·`columns`를 메타에 기록한다.

마크다운 표는 손으로 렌더한다 (pandas `to_markdown`은 선언되지 않은 `tabulate`
의존을 끌어들이므로 피한다 — 출력은 동일한 마크다운 표 형식).
"""

from typing import Any

from kms.adapters.ingestion.chunk.models import Chunk, ChunkMetadata
from kms.adapters.ingestion.ir import IR, Workbook
from kms.domain.models import DocumentMetadata


class WorkbookChunker:
    """XLSX 표를 행 그룹으로 청킹하고 각 청크에 컬럼 헤더를 복사한다."""

    def __init__(self, rows_per_chunk: int = 20) -> None:
        self._rows_per_chunk = rows_per_chunk

    def chunk(self, ir: IR, doc_meta: DocumentMetadata) -> list[Chunk]:
        if not isinstance(ir, Workbook):
            raise TypeError(f"WorkbookChunker는 Workbook만 처리한다: {type(ir)}")

        chunks: list[Chunk] = []
        index = 0
        for table in ir.tables:
            header = self._render_header(table.columns)
            for start in range(0, len(table.rows), self._rows_per_chunk):
                group = table.rows[start : start + self._rows_per_chunk]
                body = self._render_rows(table.columns, group)
                chunks.append(
                    Chunk(
                        chunk_id=f"t:{table.title}:{index}",
                        text=f"{header}\n{body}",
                        metadata=ChunkMetadata(
                            **doc_meta.model_dump(),
                            chunk_index=index,
                            table_title=table.title,
                            columns=table.columns,
                        ),
                    )
                )
                index += 1
        return chunks

    @staticmethod
    def _render_header(columns: list[str]) -> str:
        """`| a | b |\\n| --- | --- |` 형태의 마크다운 표 헤더+구분선."""
        head = "| " + " | ".join(columns) + " |"
        rule = "| " + " | ".join("---" for _ in columns) + " |"
        return f"{head}\n{rule}"

    @staticmethod
    def _render_rows(columns: list[str], rows: list[dict[str, Any]]) -> str:
        """행들을 마크다운 표 본문으로 렌더한다 (셀 내 줄바꿈은 공백으로 치환)."""
        lines = []
        for row in rows:
            cells = [str(row.get(column, "")).replace("\n", " ") for column in columns]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)
