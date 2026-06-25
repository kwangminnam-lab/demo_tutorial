"""추출기 registry — 모든 지원 확장자가 단일 코디네이터(PdfExtractor)를 사용한다.

기존 형식별 추출기(DocxExtractor·PptxExtractor·XlsxExtractor·TxtExtractor)는 제거됨.
PdfExtractor가 내부적으로 디지털(Docling)·레이아웃(Open-Parse)·OCR(pymupdf)·평문
폴백 4 파서를 순차 시도해 모든 포맷을 처리한다.
"""

from pathlib import Path

from kms.adapters.ingestion.extract.base import Extractor
from kms.adapters.ingestion.extract.pdf_extractor import PdfExtractor

#: 모든 지원 확장자가 같은 인스턴스를 공유 — 무상태이므로 안전.
_COORDINATOR: Extractor = PdfExtractor()

_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt", ".md",
}


class UnsupportedFormatError(Exception):
    """등록된 추출기가 없는 형식 — 적재 거부 사유(도메인 에러)."""


def get_extractor(path: Path) -> Extractor:
    """경로 확장자에 맞는 추출기를 반환한다. 없으면 `UnsupportedFormatError`."""
    if path.suffix.lower() not in _SUPPORTED_EXTS:
        raise UnsupportedFormatError(f"지원하지 않는 형식: {path.suffix}")
    return _COORDINATOR
