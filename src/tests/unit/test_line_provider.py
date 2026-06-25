"""PymupdfLineProvider + registry 단위 테스트 — 디지털 PDF 라인+bbox 추출.

pymupdf로 텍스트 PDF를 생성해 라인 텍스트·페이지·bbox·line_id 일련번호를 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from kms.adapters.ingestion.lines.pymupdf_lines import PymupdfLineProvider
from kms.adapters.ingestion.lines.registry import LineProviderRegistry
from kms.domain.extraction import TextLine


def _make_pdf(path: Path, pages: list[list[str]]) -> None:
    """pages[i] = i+1페이지의 줄 텍스트 목록으로 PDF를 만든다."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        y = 72.0
        for text in lines:
            page.insert_text((72.0, y), text, fontsize=12)
            y += 24.0
    doc.save(str(path))
    doc.close()


def test_lines_extract_text_page_bbox(tmp_path: Path) -> None:
    # 기본 PDF 폰트는 한글 글리프가 없어 placeholder로 나오므로 ASCII로 검증한다
    # (provider 로직은 폰트/언어 무관 — line_id·page·bbox·텍스트 매핑이 핵심).
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, [["ContractDate 2026-03-01", "Amount 1,200,000,000"], ["Party Hong"]])

    lines = PymupdfLineProvider().lines(pdf)

    texts = [ln.text for ln in lines]
    assert any("2026-03-01" in t for t in texts)
    assert any("Party Hong" in t for t in texts)
    # line_id는 0부터 연속.
    assert [ln.line_id for ln in lines] == list(range(len(lines)))
    # 페이지는 1-base이고 2페이지의 라인이 존재.
    assert {ln.page for ln in lines} == {1, 2}
    # bbox는 4-튜플, 양수 폭.
    for ln in lines:
        x0, y0, x1, y1 = ln.bbox
        assert x1 > x0 and y1 > y0


def test_supports_only_pdf(tmp_path: Path) -> None:
    provider = PymupdfLineProvider()
    assert provider.supports(Path("a.pdf"))
    assert not provider.supports(Path("a.docx"))
    assert provider.is_available()


def test_registry_resolve_and_unsupported(tmp_path: Path) -> None:
    registry = LineProviderRegistry()
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf, [["hello"]])
    assert registry.supports(pdf)
    provider = registry.resolve(pdf)
    assert provider.name == "pymupdf"

    # 미지원 확장자는 RuntimeError(조용한 실패 금지).
    assert not registry.supports(Path("y.docx"))
    with pytest.raises(RuntimeError):
        registry.resolve(Path("y.docx"))


def test_scanned_like_pdf_returns_empty(tmp_path: Path) -> None:
    """텍스트 없는(이미지만 가정) 빈 페이지는 라인 0건 — 스캔본 미지원 신호."""
    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()  # 텍스트 없는 빈 페이지.
    doc.save(str(pdf))
    doc.close()
    assert PymupdfLineProvider().lines(pdf) == []


# ── extract_lines 캐스케이드(디지털 → OCR) ──────────────────────────────────


class _FakeProvider:
    """테스트용 LineProvider 더블 — 가용/지원/반환 라인과 호출 여부를 제어한다."""

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        ext: str = ".pdf",
        lines_out: list[TextLine] | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._ext = ext
        self._lines = lines_out or []
        self.called = False

    def is_available(self) -> bool:
        return self._available

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == self._ext

    def lines(self, path: Path) -> list[TextLine]:
        self.called = True
        return self._lines


def _line(i: int) -> TextLine:
    return TextLine(line_id=i, text=f"line{i}", page=1, bbox=(0.0, 0.0, 1.0, 1.0))


def test_extract_lines_cascades_to_second_when_first_empty() -> None:
    first = _FakeProvider("digital", lines_out=[])          # 디지털 빈 결과(스캔본).
    second = _FakeProvider("ocr", lines_out=[_line(0)])     # OCR 라인 반환.
    registry = LineProviderRegistry(providers=[first, second])

    out = registry.extract_lines(Path("scan.pdf"))

    assert [ln.text for ln in out] == ["line0"]
    assert first.called and second.called  # 첫째 빈 결과 → 둘째로 캐스케이드.


def test_extract_lines_stops_at_first_nonempty() -> None:
    first = _FakeProvider("digital", lines_out=[_line(0)])  # 디지털이 라인 반환 → 종료.
    second = _FakeProvider("ocr", lines_out=[_line(9)])
    registry = LineProviderRegistry(providers=[first, second])

    out = registry.extract_lines(Path("doc.pdf"))

    assert [ln.text for ln in out] == ["line0"]
    assert first.called and not second.called  # 둘째(OCR)는 호출되지 않음(성능).


def test_extract_lines_empty_when_all_empty_or_unavailable() -> None:
    first = _FakeProvider("digital", lines_out=[])
    second = _FakeProvider("ocr", available=False, lines_out=[_line(0)])  # 미가용(미설치).
    registry = LineProviderRegistry(providers=[first, second])

    out = registry.extract_lines(Path("scan.pdf"))

    assert out == []
    assert not second.called  # 미가용 provider 는 호출 안 함.
