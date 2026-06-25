"""pdf_digital `_needs_ocr` 단위 테스트 — 디지털은 OCR 끔(빠름), 스캔본/이미지만 켬.

회귀: docling 기본 do_ocr=True 가 텍스트레이어 있는 PDF 에도 easyocr/TableFormer 를 매번
돌려 느리고 OOM·권한오류를 냈다. 텍스트레이어 프로브로 디지털을 가려 OCR 을 건너뛴다.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from kms.adapters.ingestion.extract.pdf_digital import _needs_ocr


def test_image_always_needs_ocr() -> None:
    assert _needs_ocr(Path("scan.png")) is True
    assert _needs_ocr(Path("photo.JPG")) is True


def test_non_pdf_native_digital_skips_ocr() -> None:
    # docx/pptx/xlsx/html/txt 는 네이티브 디지털 — OCR 불요.
    for name in ("a.docx", "b.pptx", "c.xlsx", "d.html", "e.txt", "f.md"):
        assert _needs_ocr(Path(name)) is False


def _make_pdf(path: Path, lines: list[str]) -> None:
    # 기본 base14 폰트는 CJK 글리프가 없어 한글이 안 박힌다 → 프로브용은 ASCII 본문 사용.
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72.0
    for line in lines:
        page.insert_text((72, y), line)
        y += 16.0
    doc.save(str(path))
    doc.close()


def test_digital_pdf_with_text_skips_ocr(tmp_path: Path) -> None:
    pdf = tmp_path / "digital.pdf"
    # 텍스트레이어가 임계(80자) 이상이면 디지털 → OCR 끔.
    _make_pdf(
        pdf,
        [
            "Financial product terms and conditions sample paragraph one.",
            "Interest rate, fees, and early repayment clauses are listed here.",
        ],
    )
    assert _needs_ocr(pdf) is False


def test_scanned_pdf_without_text_needs_ocr(tmp_path: Path) -> None:
    pdf = tmp_path / "scanned.pdf"
    _make_pdf(pdf, [])  # 텍스트레이어 없음 → 스캔본 취급.
    assert _needs_ocr(pdf) is True


def test_probe_failure_falls_back_to_ocr(tmp_path: Path) -> None:
    # 손상/비PDF 바이트를 .pdf 로 위장 → 프로브 실패 → 안전측(OCR 켬).
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a real pdf")
    assert _needs_ocr(broken) is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
