"""PaddleLineProvider 단위 테스트 — paddle 미설치 환경에서도 통과(가짜 OCR 주입).

real paddleocr 없이 동작 검증: 가짜 OCR 인스턴스를 `_ocr`에 주입해 `_ensure_ocr`가
paddle import 를 타지 않게 한다. 페이지 렌더는 실제 pymupdf 로 수행하고, OCR 출력은
표준 형식(`[page]`, page=[[box,(text,score)], ...])으로 고정해 픽셀→PDF point 변환·
line_id 일련번호·1-base page 를 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from kms.adapters.ingestion.lines.paddle_lines import PaddleLineProvider


class _FakeOCR:
    """PaddleOCR.ocr() 표준 출력을 흉내내는 가짜(이미지 무시, 고정 결과 반환)."""

    def __init__(self, page_lines: list[tuple[list[list[float]], str]]) -> None:
        # page_lines: [(box=[[x,y]x4], text), ...] — 픽셀 좌표(렌더 zoom 기준).
        self._entries = [[box, (text, 0.99)] for box, text in page_lines]

    def ocr(self, img: Any, cls: bool = True) -> Any:  # noqa: ARG002 — 가짜는 이미지 무시.
        return [self._entries]  # 표준 단일이미지 형식 [page].


def _one_page_pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=200, height=300)
    doc.save(str(path))
    doc.close()


def test_pixels_to_pdf_points_and_ids(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    _one_page_pdf(pdf)

    provider = PaddleLineProvider(zoom=2.0)
    # zoom=2.0 → 픽셀 좌표를 2로 나눈 값이 PDF point.
    provider._ocr = _FakeOCR(
        [
            ([[20, 40], [120, 40], [120, 64], [20, 64]], "계약일자 2026-03-01"),
            ([[20, 80], [160, 80], [160, 104], [20, 104]], "금액 1,200,000,000"),
        ]
    )

    lines = provider.lines(pdf)

    assert [ln.text for ln in lines] == ["계약일자 2026-03-01", "금액 1,200,000,000"]
    assert [ln.line_id for ln in lines] == [0, 1]
    assert {ln.page for ln in lines} == {1}
    # 픽셀(20,40,120,64)/zoom 2.0 = (10,20,60,32).
    assert lines[0].bbox == (10.0, 20.0, 60.0, 32.0)
    assert lines[1].bbox == (10.0, 40.0, 80.0, 52.0)


def test_empty_ocr_returns_empty(tmp_path: Path) -> None:
    pdf = tmp_path / "blank.pdf"
    _one_page_pdf(pdf)
    provider = PaddleLineProvider(zoom=2.0)
    provider._ocr = _FakeOCR([])  # OCR 결과 없음(빈 페이지).
    assert provider.lines(pdf) == []


def test_supports_pdf_and_images() -> None:
    provider = PaddleLineProvider()
    assert provider.supports(Path("a.pdf"))
    assert provider.supports(Path("a.png"))
    assert provider.supports(Path("a.jpg"))
    assert not provider.supports(Path("a.docx"))


def test_is_available_is_bool() -> None:
    # paddle 미설치 환경(Mac)에선 False, 설치 환경(클러스터)에선 True — 타입만 검증.
    assert isinstance(PaddleLineProvider().is_available(), bool)


def test_is_available_off_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # 폐쇄망 보호: env 미설정이면 paddle 설치 여부와 무관하게 OFF(런타임 외부 다운로드 차단).
    monkeypatch.delenv("DOCUX_PADDLE_OCR", raising=False)
    assert PaddleLineProvider().is_available() is False


def test_is_available_gated_on_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # 명시적으로 켜도 paddle 미설치(Mac)면 import 실패로 False — env 게이트만 통과 확인.
    monkeypatch.setenv("DOCUX_PADDLE_OCR", "1")
    assert isinstance(PaddleLineProvider().is_available(), bool)
