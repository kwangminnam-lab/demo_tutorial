"""`Extractor` 프로토콜 — 형식별 추출기의 공통 계약.

신규 형식·소스 종류는 이 프로토콜을 구현하고 registry에 등록만 하면 된다
(코어 색인 로직 수정 금지 — CONVENTIONS 적재 규약).
"""

from pathlib import Path
from typing import Protocol

from kms.adapters.ingestion.ir import IR


class Extractor(Protocol):
    """원본 파일을 IR로 변환하는 추출기."""

    def supports(self, path: Path) -> bool:
        """이 추출기가 해당 경로(확장자)를 처리할 수 있으면 True."""
        ...

    def extract(self, path: Path) -> IR:
        """원본 파일을 파싱해 IR(`MarkdownDoc`/`SlideDeck`/`Workbook`)로 반환한다."""
        ...
