"""이미지/PDF를 pymupdf PDF Document로 정규화 — 스캔 이미지 추출·렌더 공용 (ADR-025).

스캔 사진(PNG/JPG 등)을 `convert_to_pdf`로 1페이지 PDF로 바꿔, PDF용 경로(좌표·
annotation·VLM 렌더)를 그대로 재사용한다. 이미지는 텍스트 레이어가 없어 디지털 라인
추출은 빈 결과 → VLM(Gemini) 경로가 처리한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 추출·렌더가 지원하는 이미지 확장자(스캔 사진).
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}

# 페이지 단위 추출/렌더가 다루는 확장자 = PDF + 이미지.
PAGE_DOC_EXTS = {".pdf"} | IMAGE_EXTS


def is_page_doc(path: Path) -> bool:
    """PDF 또는 이미지(페이지 렌더 가능 문서)인가."""
    return path.suffix.lower() in PAGE_DOC_EXTS


def open_as_pdf(path: Path) -> Any:
    """PDF/이미지를 pymupdf PDF Document로 연다(호출자가 close 책임).

    - PDF: 그대로 연다.
    - 이미지: `convert_to_pdf`로 1페이지 PDF로 변환해 연다(좌표/annotation 지원).
    """
    import pymupdf

    if path.suffix.lower() == ".pdf":
        return pymupdf.open(str(path))
    src = pymupdf.open(str(path))
    if src.is_pdf:
        return src
    try:
        pdf_bytes = src.convert_to_pdf()
    finally:
        src.close()
    return pymupdf.open("pdf", pdf_bytes)
