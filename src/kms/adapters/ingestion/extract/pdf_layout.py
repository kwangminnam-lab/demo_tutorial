"""레이아웃 파서 — Open-Parse. 단락·표·이미지·목록·제목 요소를 비주얼 레이아웃으로 감지.

페이지의 시각 구조를 분석해 헤딩/본문/표/이미지/리스트를 분류한다. 표·이미지가 많은
브로셔·기획서·기술 문서에서 디지털 파서보다 의미 단위 보존이 강하다. 의존성: `openparse`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kms.adapters.ingestion.extract._image_util import sha8, to_data_url
from kms.adapters.ingestion.ir import MarkdownDoc

logger = logging.getLogger(__name__)


_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt",
}


class OpenParseLayoutParser:
    """Open-Parse 레이아웃 분석 기반 문서 파서 — PDF + 기타 시각 레이아웃 보존.

    Open-Parse는 PDF에 가장 최적화돼 있지만 일부 buckets에서 html/docx도 받는다.
    실패 시 None → 코디네이터가 다음 파서로 폴백.
    """

    name = "layout"

    def __init__(self) -> None:
        self._parser: Any = None  # lazy

    def is_available(self) -> bool:
        try:
            import openparse  # noqa: F401
            return True
        except ImportError:
            return False

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_EXTS

    def _ensure_parser(self) -> Any:
        if self._parser is None:
            import openparse
            self._parser = openparse.DocumentParser()
        return self._parser

    def parse(self, path: Path) -> MarkdownDoc | None:
        if not self.is_available():
            return None
        try:
            parser = self._ensure_parser()
            parsed = parser.parse(str(path))
        except Exception as exc:  # noqa: BLE001 — 폴백 유도.
            logger.warning("Open-Parse 파싱 실패: %s", exc)
            return None

        nodes = getattr(parsed, "nodes", None) or []
        if not nodes:
            return None

        parts: list[str] = []
        page_map: list[tuple[int, int]] = []
        offset = 0
        seen_page = -1
        for node in nodes:
            page_no = _node_page(node)
            text = _node_to_markdown(node)
            if not text:
                continue
            if page_no != seen_page:
                page_map.append((offset, page_no if page_no > 0 else len(page_map) + 1))
                seen_page = page_no
            parts.append(text)
            parts.append("\n\n")
            offset += len(text) + 2

        markdown = "".join(parts)
        if not markdown.strip():
            return None

        # 이미지 blob은 Open-Parse 노드에 포함될 수도, 아닐 수도 있다 — 가능한 만큼만.
        image_blobs = _collect_openparse_images(nodes)
        if not page_map:
            page_map = [(0, 1)]
        return MarkdownDoc(markdown=markdown, page_map=page_map, image_blobs=image_blobs)


def _node_page(node: Any) -> int:
    """노드의 페이지 번호를 안전하게 뽑는다 (버전별 속성 차이 가드)."""
    for attr in ("page", "page_number", "page_no"):
        val = getattr(node, attr, None)
        if isinstance(val, int):
            return val
    bbox = getattr(node, "bbox", None)
    if bbox is not None and hasattr(bbox, "page"):
        page = bbox.page
        if isinstance(page, int):
            return page
    return 0


def _node_to_markdown(node: Any) -> str:
    """Open-Parse 노드를 마크다운 한 조각으로 변환 (타입별 처리)."""
    # 1) 노드 자체가 markdown 메서드를 제공하면 사용 (가장 충실).
    for attr in ("to_markdown", "markdown"):
        val = getattr(node, attr, None)
        if callable(val):
            try:
                rendered = val()
                if isinstance(rendered, str):
                    return rendered.strip()
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(val, str):
            return val.strip()
    # 2) text 속성 폴백.
    text = getattr(node, "text", None)
    if isinstance(text, str):
        return text.strip()
    return ""


def _collect_openparse_images(nodes: list[Any]) -> dict[str, str]:
    """노드 트리에서 이미지 blob을 모은다 (가능 범위 내 — 실패는 무시)."""
    blobs: dict[str, str] = {}
    for node in nodes:
        for attr in ("image_bytes", "bytes", "data"):
            val = getattr(node, attr, None)
            if isinstance(val, (bytes, bytearray)) and val:
                digest = sha8(bytes(val))
                if digest not in blobs:
                    blobs[digest] = to_data_url(bytes(val))
                break
    return blobs
