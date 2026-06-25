"""추출 근거(B-Box) 렌더 — 추출 필드의 bbox를 PDF 페이지에 그려 PNG data URL 생성.

`_page_render`(텍스트 검색 기반 diff 하이라이트)와 달리, 여기는 이미 좌표(bbox)를
가진 추출 필드를 직접 사각형으로 칠한다(검색 불요). 디지털 PDF 전용(phase 1). 실패·
미지원은 빈 dict로 graceful — 추출 값 자체는 영향받지 않는다(조용한 실패 아님: 로깅).

색: needs_review(손글씨·저신뢰)면 빨강, 확정이면 초록. 본문 가독 위해 형광 opacity.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from kms.adapters.ingestion.image_pdf import is_page_doc, open_as_pdf
from kms.domain.extraction import ExtractedField

logger = logging.getLogger(__name__)

_ZOOM = 2.0
_OPACITY = 0.35
_COLOR_OK = (0.16, 0.78, 0.35)  # 초록 — 확정
_COLOR_REVIEW = (0.86, 0.15, 0.15)  # 빨강 — 확인 필요


def render_field_evidence(
    path: Path, fields: list[ExtractedField]
) -> dict[int, str]:
    """필드 bbox가 있는 페이지만 골라 사각형을 칠한 PNG data URL을 반환한다.

    반환: {페이지 번호(1-base): data URL}. PDF/이미지가 아니거나 bbox가 없으면 빈 dict.
    """
    if not is_page_doc(path):
        return {}
    # 페이지별 (bbox, needs_review) 모으기.
    by_page: dict[int, list[tuple[tuple[float, float, float, float], bool]]] = {}
    for field in fields:
        if field.page is None or field.bbox is None:
            continue
        by_page.setdefault(field.page, []).append((field.bbox, field.needs_review))
    if not by_page:
        return {}

    try:
        import pymupdf  # noqa: F401
    except ImportError as exc:  # noqa: BLE001 — 미설치는 근거 렌더만 생략.
        logger.warning("pymupdf 미설치 — 근거 렌더 생략: %s", exc)
        return {}

    out: dict[int, str] = {}
    document = None
    try:
        # 이미지는 PDF로 정규화해야 annotation(하이라이트)이 가능.
        document = open_as_pdf(path)
        for page_no, rects in by_page.items():
            if page_no < 1 or page_no > document.page_count:
                continue
            try:
                out[page_no] = _render_page(document[page_no - 1], rects)
            except Exception as exc:  # noqa: BLE001 — 페이지별 실패는 생략.
                logger.warning("근거 페이지 렌더 실패 (page=%s): %s", page_no, exc)
    except Exception as exc:  # noqa: BLE001 — 파일 손상 등은 빈 결과.
        logger.warning("근거 렌더 실패: %s", exc)
        return {}
    finally:
        if document is not None:
            document.close()
    return out


def _render_page(
    page: object,
    rects: list[tuple[tuple[float, float, float, float], bool]],
) -> str:
    import pymupdf

    for (x0, y0, x1, y1), needs_review in rects:
        rect = pymupdf.Rect(x0, y0, x1, y1)
        color = _COLOR_REVIEW if needs_review else _COLOR_OK
        # 직사각형 annotation — highlight_annot는 quadpoints로 그려 마름모처럼 보이므로
        # rect_annot로 실제 사각형(테두리 + 반투명 채움)을 그린다.
        annot = page.add_rect_annot(rect)  # type: ignore[attr-defined]
        annot.set_colors(stroke=color, fill=color)
        annot.set_opacity(_OPACITY)
        annot.set_border(width=1.5)
        annot.update()
    matrix = pymupdf.Matrix(_ZOOM, _ZOOM)
    pixmap = page.get_pixmap(matrix=matrix)  # type: ignore[attr-defined]
    png = pixmap.tobytes("png")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
