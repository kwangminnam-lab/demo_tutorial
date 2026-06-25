"""PDF용 OCR 파서 — pymupdf 기반. 스캔본·텍스트 레이어 없는 PDF의 단락 요소 감지.

pymupdf4llm으로 마크다운 변환을 우선 시도하고, 실패하면 페이지 평문 추출로 폴백.
페이지 경계는 `page_map`(문자 오프셋→페이지)에 기록. 이미지는 page.get_images()로
추출해 sha8 마커로 본문에 박는다. 의존성: `pymupdf`, `pymupdf4llm` (기본 설치).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
import pymupdf4llm

from kms.adapters.ingestion.extract._image_util import sha8, to_data_url
from kms.adapters.ingestion.ir import MarkdownDoc

logger = logging.getLogger(__name__)


class PymupdfOcrParser:
    """pymupdf 기반 PDF OCR 파서 — 스캔본·텍스트 레이어 없는 PDF용."""

    name = "ocr"

    def is_available(self) -> bool:
        return True  # pymupdf는 기본 설치 의존성.

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path) -> MarkdownDoc | None:
        image_markers, image_blobs = _collect_images(path)
        try:
            pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        except Exception as exc:  # noqa: BLE001 — pymupdf4llm 실패 시 평문 폴백.
            logger.warning(
                "pymupdf4llm 변환 실패, 평문 추출로 폴백",
                extra={"path": str(path)},
                exc_info=exc,
            )
            return _plain_fallback(path, image_markers, image_blobs)
        return _assemble(pages, image_markers, image_blobs)


def _assemble(
    pages: list[dict[str, object]],
    image_markers: dict[int, list[str]],
    image_blobs: dict[str, str],
) -> MarkdownDoc:
    parts: list[str] = []
    page_map: list[tuple[int, int]] = []
    offset = 0
    for number, page in enumerate(pages, start=1):
        text = str(page.get("text", ""))
        marker_lines = image_markers.get(number, [])
        if marker_lines:
            text = f"{text}\n" + "\n".join(marker_lines) + "\n"
        page_map.append((offset, number))
        parts.append(text)
        offset += len(text)
    return MarkdownDoc(
        markdown="".join(parts), page_map=page_map, image_blobs=image_blobs
    )


def _plain_fallback(
    path: Path,
    image_markers: dict[int, list[str]],
    image_blobs: dict[str, str],
) -> MarkdownDoc:
    parts: list[str] = []
    page_map: list[tuple[int, int]] = []
    offset = 0
    with pymupdf.open(str(path)) as document:
        for number, page in enumerate(document, start=1):
            text = page.get_text()
            marker_lines = image_markers.get(number, [])
            if marker_lines:
                text = f"{text}\n" + "\n".join(marker_lines) + "\n"
            page_map.append((offset, number))
            parts.append(text)
            offset += len(text)
    return MarkdownDoc(
        markdown="".join(parts), page_map=page_map, image_blobs=image_blobs
    )


def _collect_images(path: Path) -> tuple[dict[int, list[str]], dict[str, str]]:
    """페이지별 이미지 마커 + sha→data URL 맵을 동시에 만든다.

    같은 페이지 내 동일 sha는 dedupe + `xN` 카운트로 표시.
    """
    markers: dict[int, list[str]] = {}
    blobs: dict[str, str] = {}
    try:
        with pymupdf.open(str(path)) as document:
            for number, page in enumerate(document, start=1):
                counts: dict[str, int] = {}
                order: list[str] = []
                for info in page.get_images(full=True):
                    xref = info[0]
                    try:
                        image = document.extract_image(xref)
                        blob_bytes = image.get("image", b"")
                        ext = image.get("ext", "")
                    except Exception as exc:  # noqa: BLE001 — 손상 이미지는 마커 생략.
                        logger.debug("PDF 이미지 추출 실패: %s", exc)
                        continue
                    if not blob_bytes:
                        continue
                    digest = sha8(blob_bytes)
                    if digest not in counts:
                        order.append(digest)
                    counts[digest] = counts.get(digest, 0) + 1
                    if digest not in blobs:
                        mime = f"image/{ext}" if ext else None
                        blobs[digest] = to_data_url(blob_bytes, mime)
                if not order:
                    continue
                lines: list[str] = []
                for digest in order:
                    suffix = f" x{counts[digest]}" if counts[digest] > 1 else ""
                    lines.append(f"[IMAGE p={number} sha={digest}{suffix}]")
                markers[number] = lines
    except Exception as exc:  # noqa: BLE001 — 파일 자체 손상이면 마커 없이 본문만.
        logger.debug("PDF 이미지 스캔 실패: %s", exc)
        return {}, {}
    return markers, blobs
