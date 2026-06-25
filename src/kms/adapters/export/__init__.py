"""문서 내보내기 어댑터 — 답변/요약을 PDF·DOCX·TXT 바이트로 (ADR-008).

`export(content, fmt)`가 단일 진입점이다. `content`는 평문 또는 `Answer`이며,
`Answer`는 출처 인용을 포함해 렌더링한다(근거 추적성 — ADR-007).
"""

from kms.adapters.export.base import ExportError, ExportFormat, render_answer
from kms.adapters.export.exporter import export

__all__ = [
    "ExportError",
    "ExportFormat",
    "export",
    "render_answer",
]
