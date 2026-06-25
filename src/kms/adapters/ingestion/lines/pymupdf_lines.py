"""디지털 PDF 라인 추출 — pymupdf `get_text("dict")`로 라인 텍스트+bbox를 뽑는다.

텍스트 레이어가 있는 디지털 PDF 전용(born-digital). 스캔본(텍스트 레이어 없음)은
빈 리스트가 나오며, 이 경우 OCR 기반 provider가 필요하다(phase 1 범위 밖 — registry
주석). 좌표는 PDF 좌표계(point)로, B-Box 시각화·근거 표시에 그대로 쓴다.

의존: `pymupdf`(기본 설치 — extract/pdf_ocr 와 동일).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf

from kms.domain.extraction import BBox, TextLine

logger = logging.getLogger(__name__)


class PymupdfLineProvider:
    """디지털 PDF에서 라인 단위 텍스트+bbox를 추출하는 LineProvider."""

    name = "pymupdf"

    def is_available(self) -> bool:
        return True  # pymupdf는 기본 설치 의존성.

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def lines(self, path: Path) -> list[TextLine]:
        out: list[TextLine] = []
        line_id = 0
        with pymupdf.open(str(path)) as document:
            for number, page in enumerate(document, start=1):
                try:
                    data = page.get_text("dict")
                except Exception as exc:  # noqa: BLE001 — 페이지 파싱 실패는 건너뛴다.
                    logger.warning("PDF 라인 추출 실패 (page=%s): %s", number, exc)
                    continue
                for block in data.get("blocks", []):
                    # type 0 = 텍스트 블록(1 = 이미지). 이미지 블록은 라인 없음.
                    if block.get("type", 0) != 0:
                        continue
                    for line in block.get("lines", []):
                        text = _line_text(line)
                        if not text:
                            continue
                        out.append(
                            TextLine(
                                line_id=line_id,
                                text=text,
                                page=number,
                                bbox=_bbox(line.get("bbox")),
                            )
                        )
                        line_id += 1
        return out


def _line_text(line: dict[str, object]) -> str:
    """라인의 span 텍스트를 이어 붙여 한 줄 텍스트를 만든다(공백 정리)."""
    spans = line.get("spans", [])
    if not isinstance(spans, list):
        return ""
    parts = [str(span.get("text", "")) for span in spans if isinstance(span, dict)]
    return " ".join("".join(parts).split())


def _bbox(raw: object) -> BBox:
    """pymupdf bbox(시퀀스 4값)를 float 4-튜플로 정규화한다."""
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        x0, y0, x1, y1 = raw
        return (float(x0), float(y0), float(x1), float(y1))
    return (0.0, 0.0, 0.0, 0.0)
