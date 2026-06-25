"""디지털 파서 — Docling. 디지털 PDF(텍스트 레이어 보유)의 단락 요소 감지에 강하다.

문자 인식 없이 PDF 구조에서 단락·헤딩을 추출. 깔끔한 본문이 나오는 디지털 보고서·
백서·제안서에 1순위. 텍스트 레이어가 없는 스캔본은 빈 결과 → 코디네이터가 폴백.
의존성: `docling` (lazy import). 미설치 환경에서는 is_available()이 False.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from kms.adapters.ingestion.extract._image_util import sha8, to_data_url
from kms.adapters.ingestion.ir import DocBlock, MarkdownDoc

logger = logging.getLogger(__name__)


_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".txt", ".md",
    # 스캔/사진 이미지 — docling IMAGE 포맷 + OCR(RapidOCR)로 텍스트+좌표(bbox) 추출.
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
}

# 페이지 경계 sentinel — 본문에 나타날 일 없는 제어문자 조합. docling export 시 페이지
# 사이에 삽입했다가 page_map 산출 후 빈 줄로 치환한다(정확한 페이지별 오프셋 확보).
_PAGE_BREAK = "\x00\x00DOCUX_PAGE_BREAK\x00\x00"

# 스캔/사진 이미지 — 텍스트레이어가 없어 항상 OCR 필요.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
# PDF 텍스트레이어 유무 판정 임계(앞쪽 페이지 추출 문자 수). 이상이면 디지털(OCR 불요).
_DIGITAL_TEXT_MIN_CHARS = 80
# 텍스트레이어 프로브에 볼 최대 페이지 수(전 페이지 스캔 불필요 — 앞쪽만 봐도 충분).
_PROBE_PAGES = 3


def _needs_ocr(path: Path) -> bool:
    """이 문서가 OCR이 필요한가? (스캔본/이미지=True, 디지털 텍스트레이어=False)

    - 이미지 확장자: 항상 OCR.
    - PDF: 앞쪽 몇 페이지의 텍스트레이어를 추출해 충분하면 디지털(OCR 끔) — easyocr/Table
      Former 헛돌이를 피해 빠르게. 텍스트가 거의 없으면 스캔본으로 보고 OCR 켬.
    - 그 외(docx/pptx/xlsx/html/txt/md): 네이티브 디지털 — OCR 불요.
    프로브 실패(손상·미설치)는 안전하게 True(OCR 켬 — 스캔본 텍스트 누락 방지).
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTS:
        return True
    if suffix != ".pdf":
        return False
    try:
        import pymupdf

        chars = 0
        with pymupdf.open(str(path)) as document:
            for number, page in enumerate(document):
                if number >= _PROBE_PAGES:
                    break
                chars += len(page.get_text().strip())
                if chars >= _DIGITAL_TEXT_MIN_CHARS:
                    return False  # 디지털 — OCR 불요.
        return True  # 텍스트레이어 빈약 → 스캔본 → OCR.
    except Exception as exc:  # noqa: BLE001 — 프로브 실패는 안전측(OCR 켬)으로.
        logger.debug("텍스트레이어 프로브 실패(%s) — OCR 켬으로 폴백: %s", path.name, exc)
        return True


class DoclingDigitalParser:
    """Docling 기반 디지털 문서 파서 — PDF·DOCX·PPTX·XLSX·HTML·TXT 등 통합 처리.

    Docling은 포맷별 어댑터로 단일 IR을 만들고 마크다운 export를 지원한다.
    단락·헤딩 구조가 살아 있는 디지털 파일에 1순위.
    """

    name = "digital"

    def __init__(self) -> None:
        # OCR 모드(True/False)별 converter 캐시. 디지털 문서는 OCR 끄고(빠름), 스캔본/이미지만
        # OCR 켠다 — docling 기본 do_ocr=True 는 텍스트레이어 있는 PDF에도 easyocr/TableFormer
        # 를 매번 돌려 느리고 OOM 위험. 이 코드베이스에서 진짜 OCR 은 docling 이 유일하므로
        # (pymupdf "ocr" 파서는 텍스트레이어 추출) 스캔본엔 반드시 OCR 을 켜야 한다.
        self._converters: dict[bool, Any] = {}  # lazy, key=use_ocr

    def is_available(self) -> bool:
        try:
            import docling.document_converter  # noqa: F401
            return True
        except ImportError:
            return False

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_EXTS

    def _ensure_converter(self, use_ocr: bool) -> Any:
        cached = self._converters.get(use_ocr)
        if cached is not None:
            return cached

        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )

        # Apple Silicon MPS는 float64 미지원 — rt_detr_v2(layout) 모델이 깨진다.
        # CPU 강제로 안정 동작 (~2배 느리지만 결과 동일).
        opts = PdfPipelineOptions()
        opts.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.CPU
        )
        # 디지털 문서(텍스트레이어 있음)는 OCR 불필요 — 끄면 easyocr 로드·실행을 통째로
        # 건너뛰어 파싱이 빨라지고 RAM(OOM)·권한 위험이 사라진다. 스캔본/이미지만 켠다.
        opts.do_ocr = use_ocr
        # 폐쇄망 대비 — 이미지에 구운 docling 모델 경로(DOCLING_ARTIFACTS_PATH)가 있으면
        # 거기서 오프라인 로드한다(HF 다운로드 불가 시 빈 결과 방지). 없으면 기본 동작.
        artifacts = os.environ.get("DOCLING_ARTIFACTS_PATH")
        if artifacts and Path(artifacts).is_dir():
            opts.artifacts_path = artifacts
        # 한국어 OCR — 기본 RapidOCR(중국어 모델)이면 한글이 한자로 깨지므로 설정에 따라
        # 한국어 가능 엔진(ocrmac/easyocr)으로 교체한다. None이면 docling 기본 유지.
        if use_ocr:
            ocr_options = _resolve_ocr_options()
            if ocr_options is not None:
                opts.ocr_options = ocr_options
        # IMAGE 포맷도 등록 — 스캔/사진 이미지를 OCR로 처리해 텍스트 + 요소별 bbox를
        # 얻는다(PDF와 동일 파이프라인 옵션 재사용).
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
            }
        )
        self._converters[use_ocr] = converter
        return converter

    def parse(self, path: Path) -> MarkdownDoc | None:
        if not self.is_available():
            return None
        try:
            converter = self._ensure_converter(_needs_ocr(path))
            result = converter.convert(str(path))
        except Exception as exc:  # noqa: BLE001 — 파싱 실패는 다음 파서로 폴백.
            logger.warning("Docling 파싱 실패: %s", exc)
            return None

        # 페이지 경계를 정확히 잡기 위해 페이지 사이에 sentinel을 넣어 export 한 뒤,
        # sentinel 위치로 page_map(문자 오프셋→페이지)을 만든다. (이전엔 docling pages를
        # 균등분할로 근사하거나 [(0,1)]로 떨어져 전 페이지가 1로 뭉치는 문제가 있었다.)
        try:
            raw = result.document.export_to_markdown(page_break_placeholder=_PAGE_BREAK)
        except TypeError:
            # page_break_placeholder 미지원(구버전 docling) → 평문 export + 근사 폴백.
            raw = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Docling 마크다운 export 실패: %s", exc)
            return None

        if raw is not None:
            markdown, page_map = _split_pages_by_sentinel(raw, _PAGE_BREAK)
        else:
            try:
                markdown = result.document.export_to_markdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Docling 마크다운 export 실패: %s", exc)
                return None
            page_map = _build_page_map_from_docling(result, markdown)

        if not markdown or not markdown.strip():
            return None

        image_blobs = _collect_docling_images(result)
        blocks = _build_blocks_from_docling(result)
        html = _export_docling_html(result)
        return MarkdownDoc(
            markdown=markdown,
            page_map=page_map,
            image_blobs=image_blobs,
            blocks=blocks,
            html=html,
        )


def _split_pages_by_sentinel(
    raw: str, sentinel: str
) -> tuple[str, list[tuple[int, int]]]:
    """sentinel로 구분된 export 결과 → (정제 markdown, page_map).

    page_map은 (문자 오프셋, 1-base 페이지 번호) 쌍으로, 정제된 markdown에서 각 페이지가
    시작하는 정확한 오프셋을 가리킨다. sentinel이 없으면(단일 페이지/미삽입) 전체를 1페이지로
    둔다. 페이지 사이는 빈 줄(`\\n\\n`)로 이어 붙이고 오프셋에 그 길이를 반영한다.
    """
    if sentinel not in raw:
        return raw, [(0, 1)]
    segments = raw.split(sentinel)
    joiner = "\n\n"
    parts: list[str] = []
    page_map: list[tuple[int, int]] = []
    offset = 0
    for index, segment in enumerate(segments):
        if index > 0:
            parts.append(joiner)
            offset += len(joiner)
        page_map.append((offset, index + 1))
        parts.append(segment)
        offset += len(segment)
    return "".join(parts), page_map


def _resolve_ocr_options() -> Any:
    """설정(parse_ocr_engine/lang)에 따라 docling OCR 옵션을 만든다(한국어).

    "auto"면 가용 엔진을 자동 선택: ocrmac(macOS Vision) → easyocr(ko) → None(docling
    기본 RapidOCR 폴백). 선택 엔진의 의존이 없으면 다음 후보로 떨어진다(조용한 실패 아님 —
    로깅). None을 반환하면 호출부가 docling 기본 OCR을 그대로 쓴다.
    """
    from kms.config.settings import get_settings

    settings = get_settings()
    engine = (settings.parse_ocr_engine or "auto").strip().lower()
    langs = [code.strip() for code in settings.parse_ocr_lang.split(",") if code.strip()]

    def _has(module: str) -> bool:
        import importlib.util

        return importlib.util.find_spec(module) is not None

    if engine == "auto":
        if _has("ocrmac"):
            engine = "ocrmac"
        elif _has("easyocr"):
            engine = "easyocr"
        else:
            logger.info("OCR: 한국어 엔진(ocrmac/easyocr) 미설치 — RapidOCR 기본 사용(한글 부정확).")
            return None

    try:
        if engine in ("ocrmac", "mac"):
            from docling.datamodel.pipeline_options import OcrMacOptions

            mac_map = {"ko": "ko-KR", "en": "en-US", "ja": "ja-JP", "zh": "zh-Hans"}
            return OcrMacOptions(lang=[mac_map.get(code, code) for code in langs])
        if engine == "easyocr":
            from docling.datamodel.pipeline_options import EasyOcrOptions

            # 폐쇄망: 런타임 다운로드 불가. model_storage_directory 미지정 시 docling이
            # 읽기전용 DOCLING_ARTIFACTS_PATH/EasyOcr 로 다운로드 시도 → Permission denied
            # (이미지 비루트 + chmod a+rX). 빌드때 구운 경로(EASYOCR_MODULE_PATH/model)를
            # 명시하고 download_enabled=False 로 다운로드를 막는다. env 미설정(로컬)은 기본 유지.
            kwargs: dict[str, Any] = {"lang": langs}
            baked = os.environ.get("EASYOCR_MODULE_PATH")
            if baked:
                kwargs["model_storage_directory"] = str(Path(baked) / "model")
                kwargs["download_enabled"] = False
            return EasyOcrOptions(**kwargs)
        if engine == "rapidocr":
            return None  # docling 기본(RapidOCR) 유지.
    except ImportError as exc:
        logger.warning("OCR 엔진 '%s' 의존 미설치(%s) — docling 기본 OCR로 폴백.", engine, exc)
        return None
    logger.warning("알 수 없는 OCR 엔진 '%s' — docling 기본 OCR로 폴백.", engine)
    return None


def _build_blocks_from_docling(result: Any) -> list[DocBlock]:
    """Docling 결과의 요소(문단·표·제목)별 텍스트 + 페이지 + 정규화 bbox를 모은다.

    파싱 미리보기에서 "코드 hover → 원본의 실제 문단/표 위치 강조"용. bbox는 페이지
    크기 대비 top-left 정규화(x,y,w,h ∈ [0,1]). 좌표 미가용 요소는 bbox=None.
    실패는 본문(markdown)에 영향 없도록 조용히 빈 리스트로 떨어지지 않고 부분 수집한다.
    """
    blocks: list[DocBlock] = []
    try:
        doc = result.document
        sizes: dict[int, tuple[float, float]] = {}
        for no, page in getattr(doc, "pages", {}).items():
            size = getattr(page, "size", None)
            if size is not None:
                sizes[int(no)] = (float(size.width), float(size.height))
        for item, _level in doc.iterate_items():
            prov = getattr(item, "prov", None)
            if not prov:
                continue
            pv = prov[0]
            page_no = getattr(pv, "page_no", None)
            if page_no is None:
                continue
            label = str(getattr(item, "label", "") or "text")
            text = str(getattr(item, "text", "") or "").strip()
            is_table = "table" in label.lower()
            if not text and not is_table:
                continue  # 좌표만 있고 본문 없는 비표 요소는 건너뜀.
            if not text:
                text = "[표]"
            bbox = _normalize_bbox(getattr(pv, "bbox", None), sizes.get(int(page_no)))
            blocks.append(
                DocBlock(page=int(page_no), text=text, kind=label, bbox=bbox)
            )
    except Exception as exc:  # noqa: BLE001 — 위치정보 수집 실패는 본문 파싱과 무관.
        logger.debug("Docling 블록(bbox) 수집 실패: %s", exc)
    return blocks


def _normalize_bbox(
    bbox: Any, size: tuple[float, float] | None
) -> tuple[float, float, float, float] | None:
    """Docling BBox → 페이지 대비 정규화 top-left (x, y, w, h ∈ [0,1]). 미가용 시 None."""
    if bbox is None or size is None:
        return None
    width, height = size
    if width <= 0 or height <= 0:
        return None
    try:
        left = float(bbox.l)
        right = float(bbox.r)
        top = float(bbox.t)
        bottom = float(bbox.b)
    except (AttributeError, TypeError, ValueError):
        return None
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if "BOTTOM" in origin:
        # BOTTOMLEFT: y는 아래에서 위로 증가, t가 위쪽 모서리(큰 값).
        y_top = height - top
        box_h = top - bottom
    else:  # TOPLEFT
        y_top = top
        box_h = bottom - top
    x = left / width
    w = (right - left) / width
    y = y_top / height
    h = box_h / height

    def clamp01(v: float) -> float:
        return max(0.0, min(1.0, v))

    x, y = clamp01(x), clamp01(y)
    w, h = clamp01(w), clamp01(h)
    if w <= 0 or h <= 0:
        return None
    return (round(x, 5), round(y, 5), round(w, 5), round(h, 5))


def _build_page_map_from_docling(result: Any, markdown: str) -> list[tuple[int, int]]:
    """Docling 결과에서 페이지 경계 → 문자 오프셋 맵을 만든다.

    Docling 구조에 페이지 단위 export가 분리되지 않을 때는 전체 본문을 1페이지로 둔다
    (chunker가 헤더 기준 분할을 우선하므로 페이지 정확도 손실은 영향 작음).
    """
    try:
        pages = getattr(result.document, "pages", None)
        if pages and hasattr(pages, "__len__") and len(pages) > 0:
            # 페이지 경계를 균등 분할로 근사 (Docling 1.x는 페이지별 텍스트 분리 API가 불안정).
            n = len(pages)
            step = max(1, len(markdown) // n)
            return [(i * step, i + 1) for i in range(n)]
    except Exception:  # noqa: BLE001
        pass
    return [(0, 1)]


def _export_docling_html(result: Any) -> str:
    """Docling 결과 → 충실도 높은 HTML(표·구조 보존, 이미지 inline data URL).

    markdown→HTML 폴백보다 표/레이아웃 보존이 좋다(파싱 프리뷰용). 실패(구버전 docling·
    옵션 미지원)는 빈 문자열로 떨어뜨려 호출부가 markdown 폴백을 쓰게 한다(조용한 실패 아님 —
    빈 값은 폴백 신호이고 디버그 로깅을 남긴다).
    """
    try:
        from docling_core.types.doc import ImageRefMode

        return str(result.document.export_to_html(image_mode=ImageRefMode.EMBEDDED))
    except Exception as exc:  # noqa: BLE001 — HTML export 실패는 markdown 폴백으로 처리.
        logger.debug("Docling HTML export 실패(→markdown 폴백): %s", exc)
        return ""


def _collect_docling_images(result: Any) -> dict[str, str]:
    """Docling 결과에서 이미지 blob을 sha8 키 data URL로 모은다."""
    blobs: dict[str, str] = {}
    try:
        pictures = getattr(result.document, "pictures", None) or []
        for pic in pictures:
            blob = _extract_docling_image_bytes(pic)
            if not blob:
                continue
            digest = sha8(blob)
            if digest not in blobs:
                blobs[digest] = to_data_url(blob)
    except Exception as exc:  # noqa: BLE001 — 이미지 추출 실패는 본문 영향 없음.
        logger.debug("Docling 이미지 수집 실패: %s", exc)
    return blobs


def _extract_docling_image_bytes(pic: Any) -> bytes | None:
    """Docling Picture 객체에서 raw bytes를 뽑는다 (버전별 속성 차이 가드)."""
    for attr in ("data", "image", "bytes"):
        val = getattr(pic, attr, None)
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)
        if val is not None and hasattr(val, "tobytes"):
            return bytes(val.tobytes())
    return None
