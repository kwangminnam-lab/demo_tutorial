"""스캔/이미지 라인 추출 — PaddleOCR로 인쇄체 텍스트+좌표(bbox)를 뽑는다 (클러스터 전용).

텍스트 레이어가 없는 스캔 PDF/이미지를 페이지 렌더 → PaddleOCR(한글 인쇄체) → 라인별
텍스트와 픽셀 박스를 얻고, 렌더 zoom 으로 나눠 PDF point 좌표로 환산한다(시스템 전체가
PDF point bbox 전제 — domain/extraction.py BBox / _evidence_render). 출력은 PymupdfLineProvider
와 동일한 TextLine 형식(line_id 전역 0..N, page 1-base).

의존: paddleocr(+paddlepaddle) — 무거워 base 의존이 아니다(ARM Mac 휠 불안정). 클러스터
이미지에만 설치하고 `is_available()` lazy import 로 가드한다(미설치 환경=Mac 에선 False →
registry 가 건너뜀 = 클러스터 전용). 빈 결과/실패는 빈 리스트/로깅으로 명확히(조용한 실패 아님).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kms.adapters.ingestion.image_pdf import IMAGE_EXTS, open_as_pdf
from kms.domain.extraction import BBox, TextLine

logger = logging.getLogger(__name__)

# 렌더 배율 — OCR 정확도 위해 확대(≈144dpi). 픽셀 좌표를 이 값으로 나눠 PDF point 환산.
_DEFAULT_ZOOM = 2.0
# 지원 확장자: 스캔 PDF + 이미지(open_as_pdf 로 통합).
_SUPPORTED_EXTS = {".pdf"} | IMAGE_EXTS


class PaddleLineProvider:
    """PaddleOCR 로 스캔/이미지에서 라인 텍스트+bbox 를 추출하는 LineProvider(클러스터 전용)."""

    name = "paddle"

    def __init__(self, lang: str = "korean", zoom: float = _DEFAULT_ZOOM) -> None:
        self._lang = lang
        self._zoom = zoom
        self._ocr: Any = None  # lazy 1회 생성(무거운 모델 로드).

    def is_available(self) -> bool:
        # 폐쇄망 보호: PaddleOCR 첫 init 가 외부(paddleocr.bj.bcebos.com)서 모델을 **런타임
        # 다운로드**한다. 폐쇄망에선 이게 hang/지연돼 grounding(→추출 결과)이 막힌다. 그래서
        # **기본 OFF** — 모델을 이미지에 사전 bake 하고 DOCUX_PADDLE_OCR=1 로 명시한 환경에서만
        # 활성한다. OFF면 registry 가 건너뛰어 grounding 은 pymupdf/VLM box 로 폴백(무회귀).
        if os.environ.get("DOCUX_PADDLE_OCR", "").strip().lower() not in ("1", "true", "yes"):
            return False
        try:
            import paddleocr  # noqa: F401

            return True
        except ImportError:
            return False

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_EXTS

    def _ensure_ocr(self) -> Any:
        """PaddleOCR 인스턴스를 1회 생성해 재사용한다(모델 로드가 무겁다)."""
        if self._ocr is None:
            from paddleocr import PaddleOCR

            # use_angle_cls: 회전 보정. show_log False: 로그 소음 억제.
            self._ocr = PaddleOCR(use_angle_cls=True, lang=self._lang, show_log=False)
        return self._ocr

    def lines(self, path: Path) -> list[TextLine]:
        import numpy as np
        import pymupdf

        ocr = self._ensure_ocr()
        matrix = pymupdf.Matrix(self._zoom, self._zoom)
        out: list[TextLine] = []
        line_id = 0
        document = open_as_pdf(path)
        try:
            for number, page in enumerate(document, start=1):
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                # pymupdf samples 는 RGB — PaddleOCR(cv2 계열)은 BGR 기대. 3채널이면 뒤집는다.
                if img.shape[2] >= 3:
                    img = np.ascontiguousarray(img[:, :, 2::-1])
                try:
                    result = ocr.ocr(img, cls=True)
                except Exception as exc:  # noqa: BLE001 — 페이지 OCR 실패는 건너뛴다(다음 페이지 진행).
                    logger.warning("PaddleOCR 페이지 실패 (page=%s): %s", number, exc)
                    continue
                for box, text in _iter_lines(result):
                    clean = " ".join(text.split())
                    if not clean:
                        continue
                    out.append(
                        TextLine(
                            line_id=line_id,
                            text=clean,
                            page=number,
                            bbox=self._to_points(box),
                        )
                    )
                    line_id += 1
        finally:
            document.close()
        return out

    def _to_points(self, box: Any) -> BBox:
        """PaddleOCR 픽셀 quad([[x,y] x4])를 축정렬 bbox 로 모은 뒤 zoom 으로 나눠 PDF point."""
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        z = self._zoom
        return (min(xs) / z, min(ys) / z, max(xs) / z, max(ys) / z)


def _iter_lines(result: Any) -> Iterator[tuple[Any, str]]:
    """PaddleOCR `.ocr()` 결과를 (box, text) 로 정규화한다.

    단일 이미지 입력의 표준 형식은 `[page]`, page = `[[box, (text, score)], ...]`.
    빈 페이지는 `[None]` 이 올 수 있다 — 안전 처리(조용히 건너뜀, 형식 불일치는 무시).
    """
    if not result:
        return
    page = result[0]
    if not page:
        return
    for entry in page:
        if not entry or len(entry) < 2:
            continue
        box = entry[0]
        payload = entry[1]
        text = payload[0] if isinstance(payload, (list, tuple)) else payload
        yield box, str(text)
