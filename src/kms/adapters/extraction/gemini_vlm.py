"""Gemini 비전(VLM) 기반 필드추출 — 손글씨·스캔 문서용 (ADR-025).

⚠ 외부 전송: 페이지 이미지를 Google(Gemini)로 보낸다. 디지털 PDF는 이 경로를 타지
않는다(로컬 LLM만, 외부 전송 0) — 손글씨/스캔이거나 사용자가 명시적으로 VLM 모드를
켤 때만 쓴다. API 키는 코드/git에 넣지 않는다(.env/시크릿, `Settings.gemini_api_key`).

동작: pymupdf로 페이지를 PNG로 렌더 → Gemini에 이미지+스키마 프롬프트 → JSON
`{fields:[{key,value,page,box_2d:[ymin,xmin,ymax,xmax],confidence}]}` 수신 →
box_2d(정규화 0~1000)를 페이지 크기로 PDF 좌표 bbox로 환산. 손글씨라 `source=
handwriting` + 저신뢰/빈값이면 `needs_review`(자동 확정 금지).

genai 클라이언트는 주입 가능(테스트가 네트워크 없이 카나리 응답을 주입). 키·SDK
미비면 `is_available()=False`(서비스가 디지털만 동작시키거나 명확한 에러).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kms.adapters.extraction.json_util import (
    coerce_float,
    extract_json_object,
    opt_str,
)
from kms.adapters.ingestion.image_pdf import is_page_doc, open_as_pdf
from kms.domain.extraction import (
    BBox,
    ExtractedField,
    ExtractionSchema,
    SchemaField,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PAGES = 8
_DEFAULT_ZOOM = 2.0
_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

_EXTRACT_SYSTEM = (
    "너는 손글씨·스캔 포함 금융 문서에서 지정 필드를 추출하는 비전 도구다. "
    "페이지 이미지를 보고 각 필드의 값을 찾고, 값이 있는 위치의 바운딩 박스를 "
    "box_2d=[ymin,xmin,ymax,xmax] (0~1000 정규화)로, 어느 페이지인지 page(1-base)로 "
    "함께 반환한다. 값을 못 찾으면 value=null. 추정/창작 금지. 오직 JSON만 출력한다."
)


class GeminiVlmExtractor:
    """Gemini 비전으로 페이지 이미지에서 필드를 추출하는 VlmFieldExtractor 구현."""

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        client: Any | None = None,
        max_pages: int = _DEFAULT_MAX_PAGES,
        zoom: float = _DEFAULT_ZOOM,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client  # 주입 시 그대로 사용(테스트). 없으면 lazy 생성.
        self._max_pages = max_pages
        self._zoom = zoom
        self._threshold = confidence_threshold

    def is_available(self) -> bool:
        if self._client is not None:
            return True
        if not self._api_key:
            return False
        try:
            import google.genai  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # ── 추출 ──────────────────────────────────────────────────────────────
    def extract(self, path: Path, schema: ExtractionSchema) -> list[ExtractedField]:
        pages = _render_pages(path, self._max_pages, self._zoom)
        if not pages:
            return [self._empty(f.key) for f in schema.fields]
        prompt = _extract_prompt(schema)
        raw = self._generate(prompt, pages)
        parsed = _parse_fields(raw)
        sizes = {p.number: (p.width, p.height) for p in pages}

        results: list[ExtractedField] = []
        for field in schema.fields:
            payload = parsed.get(field.key)
            if payload is None:
                results.append(self._empty(field.key))
                continue
            results.append(self._build_field(field, payload, sizes))
        return results

    def _build_field(
        self,
        field: SchemaField,
        payload: dict[str, object],
        sizes: dict[int, tuple[float, float]],
    ) -> ExtractedField:
        value = payload.get("value")
        value_str = None if value is None else str(value).strip() or None
        confidence = coerce_float(payload.get("confidence"))
        page = _coerce_int(payload.get("page"))
        bbox = _box_to_bbox(payload.get("box_2d"), page, sizes)
        needs_review = (
            value_str is None or confidence is None or confidence < self._threshold
        )
        return ExtractedField(
            key=field.key,
            value=value_str,
            page=page,
            bbox=bbox,
            evidence_line_ids=[],  # VLM은 라인 번호 없음(좌표는 box_2d 직접).
            source="handwriting",
            confidence=confidence,
            needs_review=needs_review,
        )

    @staticmethod
    def _empty(key: str) -> ExtractedField:
        return ExtractedField(
            key=key, value=None, source="handwriting", needs_review=True
        )

    # ── 스키마 자동 제안 ──────────────────────────────────────────────────
    def propose_schema(
        self, path: Path, *, doc_type: str | None = None
    ) -> list[SchemaField]:
        pages = _render_pages(path, self._max_pages, self._zoom)
        if not pages:
            return []
        kind = f"문서 종류: {doc_type}\n" if doc_type else ""
        prompt = (
            f"{kind}이 문서(이미지)에서 반복 추출 가치가 높은 핵심 필드를 제안하라. "
            "각 필드는 key, type(String|int|float|boolean|date), description, required(boolean). "
            "key(필드명)는 한국어로 작성한다. 동의/비동의·예/아니오 같은 값은 boolean. "
            'JSON만: {"fields":[{"key":...,"type":...,"description":...,"required":...}]}'
        )
        raw = self._generate(prompt, pages)
        return _parse_schema_fields(raw)

    # ── Gemini 호출 ───────────────────────────────────────────────────────
    def _generate(self, prompt: str, pages: list[_Page]) -> str:
        from google.genai import types

        client = self._ensure_client()
        contents: list[Any] = [f"{_EXTRACT_SYSTEM}\n\n{prompt}"]
        for page in pages:
            contents.append(f"[page {page.number}]")
            contents.append(
                types.Part.from_bytes(data=page.png, mime_type="image/png")
            )
        config = types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.0
        )
        response = client.models.generate_content(
            model=self._model, contents=contents, config=config
        )
        return str(response.text or "")


# ── 페이지 렌더 ────────────────────────────────────────────────────────────
class _Page:
    """렌더된 한 페이지 — 번호 + PNG + PDF 좌표 크기(box_2d 환산용)."""

    __slots__ = ("number", "png", "width", "height")

    def __init__(self, number: int, png: bytes, width: float, height: float) -> None:
        self.number = number
        self.png = png
        self.width = width
        self.height = height


def _render_pages(path: Path, max_pages: int, zoom: float) -> list[_Page]:
    """PDF/이미지 페이지를 PNG로 렌더한다(상한 초과는 잘라내고 로깅).

    이미지(스캔 사진)는 PDF로 정규화해 1페이지로 처리한다(좌표 환산 일관).
    """
    if not is_page_doc(path):
        return []
    try:
        import pymupdf  # noqa: F401
    except ImportError as exc:  # noqa: BLE001 — 미설치면 렌더 불가.
        logger.warning("pymupdf 미설치 — VLM 렌더 불가: %s", exc)
        return []
    out: list[_Page] = []
    document = None
    try:
        document = open_as_pdf(path)
        import pymupdf

        total = document.page_count
        for number, page in enumerate(document, start=1):
            if number > max_pages:
                logger.warning(
                    "VLM 입력 페이지 절단: %d/%d (상한 %d)", max_pages, total, max_pages
                )
                break
            matrix = pymupdf.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix)
            rect = page.rect
            out.append(
                _Page(number, pixmap.tobytes("png"), float(rect.width), float(rect.height))
            )
    except Exception as exc:  # noqa: BLE001 — 손상 파일 등은 빈 결과.
        logger.warning("VLM 페이지 렌더 실패: %s", exc)
        return []
    finally:
        if document is not None:
            document.close()
    return out


# ── 프롬프트/파싱 ──────────────────────────────────────────────────────────
def _extract_prompt(schema: ExtractionSchema) -> str:
    field_specs = "\n".join(
        f"- {f.key} (type={f.type}"
        + (f", desc={f.description}" if f.description else "")
        + (", required" if f.required else "")
        + ")"
        for f in schema.fields
    )
    keys = ", ".join(f.key for f in schema.fields)
    return (
        "추출할 필드:\n"
        f"{field_specs}\n\n"
        "각 필드에 value(문자열 또는 null), page(1-base 정수), "
        "box_2d([ymin,xmin,ymax,xmax] 0~1000), confidence(0~1)를 반환하라.\n"
        '출력 JSON: {"fields":[{"key":"<필드명>","value":<값 또는 null>,'
        '"page":<페이지>,"box_2d":[<y0>,<x0>,<y1>,<x1>],"confidence":<0~1>}]}\n'
        f"필드 키는 정확히 다음만 사용: {keys}"
    )


def _parse_fields(raw: str) -> dict[str, dict[str, object]]:
    obj = extract_json_object(raw)
    if obj is None:
        return {}
    fields = obj.get("fields")
    if not isinstance(fields, list):
        return {}
    out: dict[str, dict[str, object]] = {}
    for item in fields:
        if isinstance(item, dict):
            key = item.get("key")
            if isinstance(key, str) and key:
                out[key] = item
    return out


def _parse_schema_fields(raw: str) -> list[SchemaField]:
    obj = extract_json_object(raw)
    if obj is None:
        return []
    fields = obj.get("fields")
    if not isinstance(fields, list):
        return []
    out: list[SchemaField] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key:
            continue
        out.append(
            SchemaField(
                key=key,
                type=str(item.get("type", "text")) or "text",
                description=opt_str(item.get("description")),
                required=bool(item.get("required", False)),
            )
        )
    return out


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _box_to_bbox(
    raw: object, page: int | None, sizes: dict[int, tuple[float, float]]
) -> BBox | None:
    """Gemini box_2d([ymin,xmin,ymax,xmax] 0~1000) → PDF 좌표 bbox(x0,y0,x1,y1)."""
    if page is None or page not in sizes:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    width, height = sizes[page]
    x0 = xmin / 1000.0 * width
    y0 = ymin / 1000.0 * height
    x1 = xmax / 1000.0 * width
    y1 = ymax / 1000.0 * height
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)
