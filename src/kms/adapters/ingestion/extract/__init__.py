"""문서 추출 모듈 — 단일 코디네이터(PdfExtractor)가 모든 포맷을 3-파서 + 폴백으로 처리.

기존 형식별 추출기(DocxExtractor·PptxExtractor·XlsxExtractor·TxtExtractor)는 제거됨.
추출 결과는 중간표현(IR: `MarkdownDoc`)으로 통일된다.
"""

from kms.adapters.ingestion.extract.base import Extractor
from kms.adapters.ingestion.extract.pdf_digital import DoclingDigitalParser
from kms.adapters.ingestion.extract.pdf_extractor import PdfExtractor
from kms.adapters.ingestion.extract.pdf_layout import OpenParseLayoutParser
from kms.adapters.ingestion.extract.pdf_ocr import PymupdfOcrParser
from kms.adapters.ingestion.extract.plaintext_fallback import PlainTextFallbackParser
from kms.adapters.ingestion.extract.registry import (
    UnsupportedFormatError,
    get_extractor,
)

__all__ = [
    "Extractor",
    "PdfExtractor",
    "DoclingDigitalParser",
    "OpenParseLayoutParser",
    "PymupdfOcrParser",
    "PlainTextFallbackParser",
    "get_extractor",
    "UnsupportedFormatError",
]
