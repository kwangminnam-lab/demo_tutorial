"""형식별 청커 단위 테스트 (작은 IR 객체만, 바이너리·파일 의존 없음).

검증 핵심:
- 형식별 분할 전략(헤더 계층 / 슬라이드 단위 / 표 행 보존).
- 구조 분할 후 size-cap 2차 분할로 청크 폭발 방지.
- 모든 청크가 doc-level `source`·`access`·`author_department`를 상속 (권한·부서 가중).
"""

import pytest

from kms.adapters.ingestion.chunk import (
    Chunk,
    MarkdownDocChunker,
    SlideDeckChunker,
    WorkbookChunker,
    get_chunker,
)
from kms.adapters.ingestion.chunk.registry import UnsupportedIRError
from kms.adapters.ingestion.ir import MarkdownDoc, Slide, SlideDeck, Table, Workbook
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, SourceType


def _doc_meta() -> DocumentMetadata:
    return DocumentMetadata(
        source=SourceType.ONEDRIVE,
        access=AccessLevel.임직원,
        author="홍길동",
        author_department="영업부",
        source_url="https://example.com/doc",
    )


def _assert_inherits_doc_meta(chunks: list[Chunk]) -> None:
    """모든 청크가 doc-level 메타(권한·부서 가중 필수)를 상속하는지 확인."""
    assert chunks
    for chunk in chunks:
        assert chunk.metadata.source == SourceType.ONEDRIVE
        assert chunk.metadata.access == AccessLevel.임직원
        assert chunk.metadata.author_department == "영업부"


# ---------- MarkdownDoc ----------


def test_markdown_chunker_records_header_path() -> None:
    ir = MarkdownDoc(markdown="# 대제목\n본문A\n## 중제목\n본문B")

    chunks = MarkdownDocChunker().chunk(ir, _doc_meta())

    paths = [c.metadata.header_path for c in chunks]
    assert ["대제목"] in paths
    assert ["대제목", "중제목"] in paths
    _assert_inherits_doc_meta(chunks)


def test_markdown_chunker_size_caps_large_section() -> None:
    big_body = "가" * 500
    ir = MarkdownDoc(markdown=f"# 제목\n{big_body}")

    chunks = MarkdownDocChunker(chunk_size=100, chunk_overlap=10).chunk(ir, _doc_meta())

    assert len(chunks) > 1  # 큰 섹션이 2차 분할됨
    assert all(len(c.text) <= 100 for c in chunks)
    assert all(c.metadata.header_path == ["제목"] for c in chunks)


def test_markdown_chunker_falls_back_without_headers() -> None:
    ir = MarkdownDoc(markdown="헤더가 전혀 없는 평문 본문이다")

    chunks = MarkdownDocChunker().chunk(ir, _doc_meta())

    assert len(chunks) == 1
    assert chunks[0].metadata.header_path is None
    assert chunks[0].text == "헤더가 전혀 없는 평문 본문이다"


def test_markdown_chunker_estimates_page_from_page_map() -> None:
    # 1페이지: offset 0~, 2페이지: "1페이지본문\n" 다음부터.
    page1 = "1페이지본문"
    markdown = f"{page1}\n2페이지본문"
    ir = MarkdownDoc(markdown=markdown, page_map=[(0, 1), (len(page1) + 1, 2)])

    chunks = MarkdownDocChunker().chunk(ir, _doc_meta())

    assert chunks[0].metadata.page == 1


def test_markdown_chunker_no_page_map_leaves_page_none() -> None:
    chunks = MarkdownDocChunker().chunk(MarkdownDoc(markdown="본문"), _doc_meta())

    assert chunks[0].metadata.page is None


# ---------- SlideDeck ----------


def test_slide_chunker_one_chunk_per_slide_with_all_parts() -> None:
    ir = SlideDeck(
        slides=[Slide(number=1, title="제목", body="본문", notes="노트")]
    )

    chunks = SlideDeckChunker().chunk(ir, _doc_meta())

    assert len(chunks) == 1
    only = chunks[0]
    assert only.metadata.slide_no == 1
    assert "제목" in only.text
    assert "본문" in only.text
    assert "노트" in only.text
    _assert_inherits_doc_meta(chunks)


def test_slide_chunker_splits_oversized_slide_keeping_slide_no() -> None:
    ir = SlideDeck(slides=[Slide(number=3, title=None, body="가" * 400, notes=None)])

    chunks = SlideDeckChunker(chunk_size=100, chunk_overlap=10).chunk(ir, _doc_meta())

    assert len(chunks) > 1
    assert all(c.metadata.slide_no == 3 for c in chunks)


def test_slide_chunker_skips_empty_slide() -> None:
    ir = SlideDeck(
        slides=[
            Slide(number=1, title=None, body="", notes=None),
            Slide(number=2, title="유효", body="본문", notes=None),
        ]
    )

    chunks = SlideDeckChunker().chunk(ir, _doc_meta())

    assert [c.metadata.slide_no for c in chunks] == [2]


# ---------- Workbook ----------


def test_workbook_chunker_groups_rows_and_copies_header() -> None:
    rows = [{"이름": f"이름{i}", "값": i} for i in range(5)]
    ir = Workbook(tables=[Table(title="매출", columns=["이름", "값"], rows=rows)])

    chunks = WorkbookChunker(rows_per_chunk=2).chunk(ir, _doc_meta())

    assert len(chunks) == 3  # 5행 / 2 = 3묶음 (2,2,1)
    for chunk in chunks:
        assert chunk.metadata.table_title == "매출"
        assert chunk.metadata.columns == ["이름", "값"]
        # 각 청크 상단에 컬럼 헤더 복사 (자기설명적)
        assert "| 이름 | 값 |" in chunk.text
        assert "| --- | --- |" in chunk.text
    # 첫 청크는 처음 2개 행을 담는다
    assert "이름0" in chunks[0].text
    assert "이름1" in chunks[0].text
    assert "이름2" not in chunks[0].text
    _assert_inherits_doc_meta(chunks)


# ---------- registry ----------


def test_registry_selects_chunker_by_ir_type() -> None:
    assert isinstance(get_chunker(MarkdownDoc(markdown="x")), MarkdownDocChunker)
    assert isinstance(get_chunker(SlideDeck()), SlideDeckChunker)
    assert isinstance(get_chunker(Workbook()), WorkbookChunker)


def test_chunker_rejects_wrong_ir_type() -> None:
    with pytest.raises(TypeError):
        MarkdownDocChunker().chunk(SlideDeck(), _doc_meta())  # type: ignore[arg-type]


def test_registry_raises_on_unsupported_ir() -> None:
    class _UnknownIR:
        pass

    with pytest.raises(UnsupportedIRError):
        get_chunker(_UnknownIR())  # type: ignore[arg-type]
