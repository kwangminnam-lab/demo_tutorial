"""PDF 파서 공통 인터페이스 — 디지털·레이아웃·OCR 3종을 동일 계약으로 묶는다.

각 파서는 `is_available()`로 의존성 설치 여부를 확인하고, `parse()`로 `MarkdownDoc`을
반환한다. 실패 또는 빈 결과면 None — 코디네이터(PdfExtractor)가 다음 파서로 폴백.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from kms.adapters.ingestion.ir import MarkdownDoc


@runtime_checkable
class PdfParser(Protocol):
    """문서 한 건을 `MarkdownDoc`으로 환원하는 파서 계약 (이름은 역사적, 모든 포맷 지원).

    파서마다 `supports()`로 처리 가능한 확장자를 신고하고, 코디네이터가 매칭되는
    파서만 순차 시도한다. PDF 외에도 docx/pptx/xlsx/xlsm/html/txt 등 처리 가능.
    """

    name: str

    def is_available(self) -> bool:
        """필수 의존성(라이브러리·모델)이 설치돼 있는지."""
        ...

    def supports(self, path: Path) -> bool:
        """이 파서가 처리 가능한 확장자인지."""
        ...

    def parse(self, path: Path) -> MarkdownDoc | None:
        """문서를 파싱. 실패 또는 빈 본문이면 None (코디네이터가 폴백)."""
        ...
