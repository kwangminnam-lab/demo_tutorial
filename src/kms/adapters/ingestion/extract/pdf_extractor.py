"""문서 추출 코디네이터 — 다중 파서를 우선순위대로 시도해 첫 성공 결과를 반환한다.

이름은 역사적으로 `PdfExtractor`이지만 실제로는 PDF뿐 아니라 docx/pptx/xlsx/xlsm/
html/txt 등 모든 지원 포맷을 처리한다 (`supports()`로 파서별 자율 선택).

파서 우선순위:
  1) 디지털 파서 (Docling) — 디지털 문서 전반 (단락·헤딩 구조 보존)
  2) 레이아웃 파서 (Open-Parse) — 시각 레이아웃 분석 (표·이미지·목록)
  3) OCR 파서 (pymupdf) — 스캔본 PDF 전용
  4) 평문 폴백 (PlainTextFallbackParser) — Docling/Open-Parse 미설치 환경의 비-PDF 보장

각 파서는 `is_available()`(의존성)과 `supports()`(확장자)를 모두 만족할 때만 호출된다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kms.adapters.ingestion.extract.pdf_base import PdfParser
from kms.adapters.ingestion.extract.pdf_digital import DoclingDigitalParser
from kms.adapters.ingestion.extract.pdf_layout import OpenParseLayoutParser
from kms.adapters.ingestion.extract.pdf_ocr import PymupdfOcrParser
from kms.adapters.ingestion.extract.plaintext_fallback import PlainTextFallbackParser
from kms.adapters.ingestion.ir import MarkdownDoc

logger = logging.getLogger(__name__)

_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt", ".md",
}


def _default_parsers() -> list[PdfParser]:
    """기본 우선순위: 디지털 → 레이아웃 → OCR → 평문 폴백."""
    return [
        DoclingDigitalParser(),
        OpenParseLayoutParser(),
        PymupdfOcrParser(),
        PlainTextFallbackParser(),
    ]


class PdfExtractor:
    """다중 포맷 추출 코디네이터.

    PDF·DOCX·PPTX·XLSX·XLSM·HTML·TXT 등 지원 포맷 모두 처리. 파서별로 `supports()`·
    `is_available()`를 만족하는 첫 번째 파서가 우선 호출되며, 실패·빈 결과면 다음 파서로
    폴백한다. 모두 실패하면 `RuntimeError` (조용한 실패 금지).
    """

    def __init__(self, parsers: list[PdfParser] | None = None) -> None:
        self._parsers = list(parsers) if parsers is not None else _default_parsers()

    def supports(self, path: Path) -> bool:
        # 적어도 한 파서가 supports() True면 처리 가능.
        return any(p.supports(path) for p in self._parsers)

    def extract(self, path: Path) -> MarkdownDoc:
        last_err: Exception | None = None
        any_tried = False
        for parser in self._parsers:
            try:
                if not parser.is_available():
                    continue
                if not parser.supports(path):
                    continue
                any_tried = True
                result = parser.parse(path)
            except Exception as exc:  # noqa: BLE001 — 파서별 실패는 다음으로.
                logger.warning("문서 파서 실패 (%s): %s", parser.name, exc)
                last_err = exc
                continue
            if result is None:
                continue
            if not result.markdown.strip():
                continue
            return result
        if not any_tried:
            raise RuntimeError(f"지원하는 파서 없음 ({path.name})")
        if last_err is not None:
            raise RuntimeError(
                f"모든 파서 실패 ({path.name}): 마지막 에러={type(last_err).__name__}"
            ) from last_err
        raise RuntimeError(f"모든 파서가 빈 결과 ({path.name})")
