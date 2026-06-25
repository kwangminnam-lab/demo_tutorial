"""파싱/추출 결과의 JSONL(NDJSON) 직렬화 — 순수 함수(adapters 레이어).

사용자 결정: **.jsonl, 섹션(heading)당 1줄**. 미리보기용 `.json`(parse 응답의
`json_data`)은 그대로 두고, 다운로드·내보내기용 줄단위 직렬화를 별도로 더한다.

- 파싱(`to_parse_jsonl`): 자연 단위 = **섹션**(MarkdownDoc은 heading 경계, SlideDeck은
  슬라이드, Workbook은 표). 한 단위 = JSON 한 줄.
- 추출(`to_extract_jsonl`): 자연 단위 = **필드**(섹션 개념이 아님). 한 필드 = 한 줄.

파일 쓰기·HTTP 반환은 이 모듈 책임이 아니다(라우트=api, 영속=step 5). 여기선 문자열만
만든다. 빈 입력도 최소 1줄을 낸다(조용한 누락 금지). 한국어는 `ensure_ascii=False`로 보존.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kms.domain.extraction import ExtractionResult

from .ir import MarkdownDoc, SlideDeck, Workbook
from .structure import build_sections, markdown_to_text

#: 본문 없는(빈) 문서의 단일 섹션 기본 제목 — structure._DEFAULT_TITLE와 동일 라벨.
_DEFAULT_TITLE = "문서"


def to_parse_jsonl(
    ir: MarkdownDoc | SlideDeck | Workbook,
    filename: str,
    page_map: list[tuple[int, int]],
) -> str:
    """파싱 IR을 JSONL(섹션당 1줄)로 직렬화한다.

    각 줄(레코드) 스키마(키 고정):
        {"filename", "doc_type", "section_index", "level", "title", "path",
         "page", "text"}
    `text`는 plain text(markdown→text 적용). 마지막 줄도 `\\n`으로 끝낸다(표준 JSONL).
    빈 문서도 최소 1줄(단일 섹션)을 낸다.
    """
    doc_type = _doc_type(filename)
    records = _parse_records(ir, filename, doc_type, page_map)
    if not records:
        records = [_empty_record(filename, doc_type)]
    return _dump_lines(records)


def to_extract_jsonl(result: ExtractionResult) -> str:
    """추출 결과를 JSONL(필드당 1줄)로 직렬화한다.

    각 줄(레코드) 스키마(키 고정):
        {"doc_id", "schema_id", "key", "value", "page", "bbox", "confidence",
         "needs_review"}
    필드가 하나도 없으면 메타만 담은 1줄을 낸다(빈 결과 금지).
    """
    records: list[dict[str, Any]] = [
        {
            "doc_id": result.doc_id,
            "schema_id": result.schema_id,
            "key": field.key,
            "value": field.value,
            "page": field.page,
            "bbox": list(field.bbox) if field.bbox is not None else None,
            "confidence": field.confidence,
            "needs_review": field.needs_review,
        }
        for field in result.fields
    ]
    if not records:
        # 필드 0건 결과도 줄을 비우지 않는다 — doc_id·schema_id만 담은 placeholder 1줄.
        records = [
            {
                "doc_id": result.doc_id,
                "schema_id": result.schema_id,
                "key": None,
                "value": None,
                "page": None,
                "bbox": None,
                "confidence": None,
                "needs_review": False,
            }
        ]
    return _dump_lines(records)


def _parse_records(
    ir: MarkdownDoc | SlideDeck | Workbook,
    filename: str,
    doc_type: str,
    page_map: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    """IR 형식별로 줄(레코드) 목록을 만든다."""
    if isinstance(ir, MarkdownDoc):
        return [
            _record(
                filename, doc_type, index,
                level=section.level,
                title=section.title,
                path=section.path,
                page=section.page,
                text=section.text,
            )
            for index, section in enumerate(build_sections(ir.markdown, page_map))
        ]
    if isinstance(ir, SlideDeck):
        return [
            _record(
                filename, doc_type, index,
                level=1,
                title=slide.title or f"슬라이드 {slide.number}",
                path=[slide.title or f"슬라이드 {slide.number}"],
                page=slide.number,
                text=markdown_to_text(slide.body),
            )
            for index, slide in enumerate(ir.slides)
        ]
    if isinstance(ir, Workbook):
        return [
            _record(
                filename, doc_type, index,
                level=1,
                title=table.title,
                path=[table.title],
                page=None,
                text=_table_text(table.columns, table.rows),
            )
            for index, table in enumerate(ir.tables)
        ]
    return []


def _record(
    filename: str,
    doc_type: str,
    index: int,
    *,
    level: int,
    title: str,
    path: list[str],
    page: int | None,
    text: str,
) -> dict[str, Any]:
    """파싱 레코드 한 줄(키 고정)."""
    return {
        "filename": filename,
        "doc_type": doc_type,
        "section_index": index,
        "level": level,
        "title": title,
        "path": path,
        "page": page,
        "text": text,
    }


def _empty_record(filename: str, doc_type: str) -> dict[str, Any]:
    """본문 없는 문서의 단일 섹션 레코드(빈 결과 금지)."""
    return _record(
        filename, doc_type, 0,
        level=0, title=_DEFAULT_TITLE, path=[], page=None, text="",
    )


def _table_text(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Workbook 표를 ` | ` 구분 plain text로 평탄화."""
    lines: list[str] = []
    if columns:
        lines.append(" | ".join(columns))
    for row in rows:
        lines.append(" | ".join(str(row.get(column, "")) for column in columns))
    return "\n".join(lines)


def _doc_type(filename: str) -> str:
    """파일명 확장자를 대문자 doc_type으로(예: report.docx → DOCX)."""
    return Path(filename).suffix.lstrip(".").upper()


def _dump_lines(records: list[dict[str, Any]]) -> str:
    """레코드 목록을 NDJSON 문자열로(줄마다 `\\n`, 마지막 줄도 종료)."""
    return "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
