"""jsonl.py 단위 테스트 — 파싱/추출 결과의 JSONL(섹션·필드당 1줄) 직렬화.

순수 직렬화만 검증한다(I/O·HTTP 없음). 핵심: 줄 수=섹션/필드 수, 각 줄 유효 JSON(NDJSON),
키 스키마 고정, plain text, 한글 보존(ensure_ascii=False), 빈 입력도 최소 1줄.
"""

import json

from kms.adapters.ingestion.ir import MarkdownDoc, Slide, SlideDeck, Table, Workbook
from kms.adapters.ingestion.jsonl import to_extract_jsonl, to_parse_jsonl
from kms.domain.extraction import ExtractedField, ExtractionResult

_PARSE_KEYS = {
    "filename", "doc_type", "section_index", "level", "title", "path", "page", "text",
}
_EXTRACT_KEYS = {
    "doc_id", "schema_id", "key", "value", "page", "bbox", "confidence", "needs_review",
}


def _lines(jsonl: str) -> list[dict]:
    """NDJSON 문자열을 줄별 dict로 파싱한다(유효성 검증 겸용)."""
    assert jsonl.endswith("\n")  # 마지막 줄도 \n 종료(표준 JSONL)
    return [json.loads(line) for line in jsonl.splitlines()]


def test_to_parse_jsonl_markdown_one_line_per_section() -> None:
    markdown = "# 1장\n본문 A\n## 1.1절\n본문 B\n"
    ir = MarkdownDoc(markdown=markdown)

    records = _lines(to_parse_jsonl(ir, "report.md", []))

    # 줄 수 == 섹션 수.
    assert len(records) == 2
    for index, record in enumerate(records):
        assert set(record) == _PARSE_KEYS  # 키 스키마 고정
        assert record["filename"] == "report.md"
        assert record["doc_type"] == "MD"
        assert record["section_index"] == index
    assert records[0]["level"] == 1
    assert records[0]["title"] == "1장"
    assert records[0]["text"] == "본문 A"  # plain text(헤더 기호 없음)
    assert records[1]["path"] == ["1장", "1.1절"]


def test_to_parse_jsonl_preserves_korean_without_escape() -> None:
    ir = MarkdownDoc(markdown="# 계약서\n계약금액 일천만원\n")

    jsonl = to_parse_jsonl(ir, "문서.pdf", [])

    # ensure_ascii=False — 한글이 \uXXXX로 escape되지 않는다.
    assert "계약서" in jsonl
    assert "\\u" not in jsonl


def test_to_parse_jsonl_empty_doc_emits_single_section() -> None:
    ir = MarkdownDoc(markdown="")

    records = _lines(to_parse_jsonl(ir, "empty.txt", []))

    assert len(records) == 1  # 빈 문서도 최소 1줄(조용한 누락 금지)
    assert records[0]["level"] == 0
    assert records[0]["text"] == ""


def test_to_parse_jsonl_slidedeck_one_line_per_slide() -> None:
    deck = SlideDeck(
        slides=[
            Slide(number=1, title="표지", body="회사 소개", notes=None),
            Slide(number=2, title=None, body="매출 현황", notes=None),
        ]
    )

    records = _lines(to_parse_jsonl(deck, "deck.pptx", []))

    assert len(records) == 2
    assert records[0]["title"] == "표지"
    assert records[0]["page"] == 1
    assert records[0]["text"] == "회사 소개"
    assert records[1]["title"] == "슬라이드 2"  # title 없으면 번호 라벨


def test_to_parse_jsonl_workbook_one_line_per_table() -> None:
    workbook = Workbook(
        tables=[
            Table(title="매출", columns=["월", "금액"], rows=[{"월": "1월", "금액": 100}]),
        ]
    )

    records = _lines(to_parse_jsonl(workbook, "book.xlsx", []))

    assert len(records) == 1
    assert records[0]["title"] == "매출"
    assert "1월" in records[0]["text"]


def test_to_extract_jsonl_one_line_per_field() -> None:
    result = ExtractionResult(
        doc_id="abc123",
        schema_id=7,
        fields=[
            ExtractedField(
                key="계약일", value="2026-01-01", page=1,
                bbox=(1.0, 2.0, 3.0, 4.0), confidence=0.9, needs_review=False,
            ),
            ExtractedField(key="계약금액", value=None, needs_review=True),
        ],
    )

    records = _lines(to_extract_jsonl(result))

    assert len(records) == 2  # 필드당 1줄
    for record in records:
        assert set(record) == _EXTRACT_KEYS
        assert record["doc_id"] == "abc123"
        assert record["schema_id"] == 7
    assert records[0]["key"] == "계약일"
    assert records[0]["bbox"] == [1.0, 2.0, 3.0, 4.0]  # tuple → list
    assert records[1]["value"] is None
    assert records[1]["needs_review"] is True


def test_to_extract_jsonl_no_fields_emits_placeholder_line() -> None:
    result = ExtractionResult(doc_id="empty", schema_id=1, fields=[])

    records = _lines(to_extract_jsonl(result))

    assert len(records) == 1  # 필드 0건도 빈 결과 금지
    assert records[0]["doc_id"] == "empty"
    assert records[0]["key"] is None
