"""LineProvider 인터페이스 — 문서 → `TextLine`(텍스트+좌표) 목록.

`PdfParser`(extract/)와 같은 패턴: `is_available()`(의존성)·`supports()`(확장자)·
`lines()`(추출). 좌표를 주는 추출기를 교체 가능하게 인터페이스 뒤로 분리한다
(디지털=pymupdf, 스캔=OCR 등). 빈 결과/실패는 None이 아니라 빈 리스트나 예외로
명확히 — registry가 다음 provider로 폴백한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from kms.domain.extraction import TextLine


@runtime_checkable
class LineProvider(Protocol):
    """문서에서 좌표 있는 라인을 뽑는 계약."""

    name: str

    def is_available(self) -> bool:
        """의존(라이브러리·모델)이 설치돼 동작 가능한가."""
        ...

    def supports(self, path: Path) -> bool:
        """이 확장자를 처리할 수 있는가."""
        ...

    def lines(self, path: Path) -> list[TextLine]:
        """문서 전체의 라인을 문서 순서대로 반환한다(line_id 0..N).

        텍스트 레이어/좌표가 없으면 빈 리스트를 반환한다(registry가 폴백 판단).
        """
        ...
