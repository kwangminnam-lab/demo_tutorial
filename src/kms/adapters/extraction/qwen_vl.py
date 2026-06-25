"""사내 Qwen3-VL(vLLM, OpenAI 호환) 비전 필드추출 — 손글씨·스캔 문서용.

Gemini(외부 전송) 대신 **사내 vLLM 엔드포인트**로 페이지 이미지를 보고 필드를 뽑는다.
추론이 사내망에서 끝나 외부 전송 0(폐쇄망 안전). `Settings.vlm_base_url`(OpenAI 호환
`/v1`)·`vlm_model`로 호출한다. 사내 서버라 보통 키 불요(`vlm_api_key` 있으면 Bearer).

동작: pymupdf로 페이지를 PNG로 렌더 → base64 image_url 로 chat completions 호출 →
JSON `{fields:[{key,value,page,bbox:[x_min,y_min,x_max,y_max],confidence}]}` 수신 →
bbox(0~1000, x먼저)를 페이지 크기로 PDF 좌표 bbox로 환산. 손글씨라 `source=handwriting`
+ 저신뢰/빈값이면 `needs_review`(자동 확정 금지).

`responder`(테스트 주입)가 있으면 네트워크 없이 카나리 응답을 쓴다. base_url/키는
로깅·예외 메시지에 싣지 않는다(시크릿 무로깅).
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from kms.adapters.extraction.json_util import (
    coerce_float,
    extract_json_object,
    opt_str,
)
from kms.adapters.ingestion.image_pdf import is_page_doc, open_as_pdf
from kms.adapters.observability.base import OcrMetricsSink
from kms.adapters.observability.mlflow_tracker import NoopOcrTracker
from kms.domain.extraction import (
    BBox,
    ExtractedField,
    ExtractionSchema,
    SchemaField,
)

logger = logging.getLogger(__name__)

# 페이지 상한 — VLM에 보내는 이미지 수. 전 페이지(8)를 한 프롬프트에 넣으면 8×~1천 비전
# 토큰 = prompt ~1.7만 → prefill만 ~85초(200 tok/s). 데모 금융문서는 대개 1~3장이라 4로
# 낮춰 prefill을 절반↓. 더 긴 문서는 호출부에서 max_pages를 올려 처리.
_DEFAULT_MAX_PAGES = 4
# 페이지 렌더 배율 — 값 인식 정확도의 1순위 레버. 백엔드가 이 배율로 PDF를 PNG로 렌더하고,
# vLLM이 그 위에 max_pixels(서빙 args)로 캡한다. zoom이 낮으면 렌더 픽셀이 max_pixels보다
# 작아 캡이 무용 → VLM이 흐린 이미지를 봐 작은 글자·손글씨를 오독한다.
# A4(595×842pt) 기준: zoom 2.0 → 1190×1684 ≈ 2.0M px(서빙 max_pixels 2.62M 캡 안). 인쇄+손글씨
# 혼재 스캔의 가독성을 위해 2.0으로(토큰 ↑ → prefill 약간 느리나 bf16+청킹이 흡수). 더 또렷이
# 필요하면 2.2(≈2.42M)까지. (서빙 max_pixels보다 렌더가 작으면 그 max_pixels는 의미 없음.)
_DEFAULT_ZOOM = 2.0
_DEFAULT_CONFIDENCE_THRESHOLD = 0.6
_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_TIMEOUT = 120.0
# 필드(key-value) 묶음 크기 — VLM은 필드마다 JSON 1개를 출력해 **디코드 시간이 필드 수에
# 비례**한다(필드 많을수록 느림). 묶음 초과 시 필드를 쪼개 **동시** 호출한다. 같은 페이지
# 이미지라 vLLM prefix 캐시로 이미지 prefill은 거의 재사용 → 벽시계 ≈ 가장 느린 한 묶음.
_CHUNK_SIZE = 6
# 동시 VLM 호출 상한 — 단일 GPU 보호(과도한 동시요청은 KV 압박). 묶음 수와 이 값의 min.
_MAX_CONCURRENCY = 4

_EXTRACT_SYSTEM = (
    "너는 인쇄 스캔에 손글씨가 섞인 금융 문서에서 지정 필드를 추출하는 비전 도구다. "
    "페이지 이미지를 보고 각 필드의 값을 **문서에 적힌 글자 그대로** 읽는다. "
    "값이 있는 위치의 바운딩 박스를 bbox=[x_min,y_min,x_max,y_max] "
    "(좌상단 원점, x=가로·y=세로, 0~1000 정규화)로, 어느 페이지인지 page(1-base)로 함께 반환한다. "
    "읽기 규칙: ①인쇄체와 손글씨가 한 문서에 섞일 수 있다 — 손글씨는 획을 신중히 판독한다. "
    "②숫자는 자릿수·콤마·소수점·단위를 정확히(0↔O, 1↔l, 5↔S 혼동 주의). "
    "③글자가 흐리거나 가려져 확실치 않으면 추정해 confidence가 가장 높은 값을 준다. "
    "창작 금지. 오직 JSON만 출력한다."
)


class QwenVlmExtractor:
    """사내 Qwen3-VL(vLLM) 비전으로 페이지 이미지에서 필드를 추출하는 VlmFieldExtractor 구현."""

    def __init__(
        self,
        base_url: str | None,
        model: str,
        *,
        api_key: str | None = None,
        responder: Callable[[str, list["_Page"]], str] | None = None,
        max_pages: int = _DEFAULT_MAX_PAGES,
        zoom: float = _DEFAULT_ZOOM,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        timeout: float = _DEFAULT_TIMEOUT,
        tracker: OcrMetricsSink | None = None,
    ) -> None:
        # base_url은 `/v1`까지 포함한다(예: http://qwen3-vl-4b:8000/v1).
        self._base_url = base_url.rstrip("/") if base_url else None
        self._model = model
        self._api_key = api_key
        self._responder = responder  # 주입 시 네트워크 없이 사용(테스트).
        self._max_pages = max_pages
        self._zoom = zoom
        self._threshold = confidence_threshold
        self._max_tokens = max_tokens
        self._timeout = timeout
        # AI OCR 성능 추적(MLflow). 미설정이면 Noop(무동작) — 추출에 영향 없음.
        self._tracker: OcrMetricsSink = tracker or NoopOcrTracker()

    def is_available(self) -> bool:
        return self._responder is not None or bool(self._base_url)

    # ── 추출 ──────────────────────────────────────────────────────────────
    def extract(self, path: Path, schema: ExtractionSchema) -> list[ExtractedField]:
        # 단계별 타이밍 계측 — "2분 초과/결과없음"이 어디서 새는지(렌더/VLM/grounding)
        # 매 호출 로그로 드러낸다(추측 없이 병목 특정). 측정만, 동작 변경 없음.
        import time

        t0 = time.perf_counter()
        pages = _render_pages(path, self._max_pages, self._zoom)
        t_render = time.perf_counter()
        if not pages:
            return [self._empty(f.key) for f in schema.fields]
        # 필드 수가 많으면 묶음으로 쪼개 동시 호출(디코드 시간이 필드 수에 비례하므로).
        raws = self._extract_raw(schema.fields, pages)
        t_vlm = time.perf_counter()
        parsed: dict[str, dict[str, object]] = {}
        for raw in raws:
            parsed.update(_parse_fields(raw))
        raw = "\n---\n".join(raws)  # 진단/타이밍 로깅용 합본.
        sizes = {p.number: (p.width, p.height) for p in pages}
        # 키 정확매칭 실패 대비 — VLM이 키를 공백·대소문자만 다르게 돌려줘도 매핑되게
        # 정규화 인덱스를 함께 둔다(스키마 키는 사용자 정의라 모델 출력과 미세차 흔함).
        norm_index = {_norm(k): v for k, v in parsed.items()}

        results: list[ExtractedField] = []
        for field in schema.fields:
            payload = parsed.get(field.key) or norm_index.get(_norm(field.key))
            if payload is None:
                results.append(self._empty(field.key))
                continue
            results.append(self._build_field(field, payload, sizes))
        # 진단(조용한 실패 금지): 값이 하나도 안 잡히면 원시 응답·키 대조를 남겨 정체를
        # 드러낸다(파싱 실패/키 불일치/모델이 value=null 중 무엇인지 다음 실행에 보임).
        if not any(r.value for r in results):
            logger.warning(
                "VLM 추출 값 0건 — 진단: schema_keys=%s parsed_keys=%s raw_head=%r",
                [f.key for f in schema.fields],
                list(parsed.keys()),
                raw[:600],
            )
        # 하이라이트 정확도: VLM의 러프 box 대신, 추출한 값 텍스트를 페이지 OCR/텍스트
        # 라인에서 찾아 그 라인 bbox로 교체(매칭 실패 시 VLM box 유지). 디지털은 정확,
        # 스캔은 OCR 정밀도만큼. (값 읽기는 VLM, 위치는 라인 — grounding 분리.)
        grounded = _ground_with_lines(path, results)
        t_ground = time.perf_counter()
        logger.info(
            "VLM 추출 타이밍(s): render=%.1f vlm=%.1f ground=%.1f total=%.1f "
            "(pages=%d, fields=%d, chunks=%d, raw_chars=%d)",
            t_render - t0,
            t_vlm - t_render,
            t_ground - t_vlm,
            t_ground - t0,
            len(pages),
            len(schema.fields),
            len(raws),
            len(raw),
        )
        # AI OCR 성능 추적(MLflow) — 추출 1회 = run 1건. 설정별 지연·추출품질 비교용.
        # best-effort(tracker가 실패를 삼킴) — 추출 결과엔 영향 없음.
        self._log_metrics(path, schema, grounded, pages, raws, (t0, t_render, t_vlm, t_ground))
        return grounded

    def _log_metrics(
        self,
        path: Path,
        schema: ExtractionSchema,
        results: list[ExtractedField],
        pages: list["_Page"],
        raws: list[str],
        marks: tuple[float, float, float, float],
    ) -> None:
        """AI OCR 한 번의 지연·추출품질 지표를 MLflow run 1건으로 남긴다(관측 전용)."""
        t0, t_render, t_vlm, t_ground = marks
        total = len(schema.fields)
        filled = sum(1 for r in results if r.value)
        confs = [r.confidence for r in results if r.confidence is not None]
        elapsed = t_ground - t0
        metrics: dict[str, float] = {
            "render_s": t_render - t0,
            "vlm_s": t_vlm - t_render,
            "ground_s": t_ground - t_vlm,
            "total_s": elapsed,
            "fields_total": float(total),
            "fields_filled": float(filled),
            "fill_rate": (filled / total) if total else 0.0,
            "needs_review": float(sum(1 for r in results if r.needs_review)),
            "grounded_box": float(sum(1 for r in results if r.bbox is not None)),
            "avg_confidence": (sum(confs) / len(confs)) if confs else 0.0,
            "fields_per_s": (total / elapsed) if elapsed > 0 else 0.0,
        }
        params: dict[str, object] = {
            "model": self._model,
            "zoom": self._zoom,
            "max_pages": self._max_pages,
            "chunk_size": _CHUNK_SIZE,
            "max_concurrency": _MAX_CONCURRENCY,
            "pages_used": len(pages),
            "chunks": len(raws),
            "field_count": total,
        }
        self._tracker.log_ocr_run(
            run_name=path.name,
            params=params,
            metrics=metrics,
            tags={"document": path.name, "model": self._model},
        )

    def _extract_raw(
        self, fields: list[SchemaField], pages: list["_Page"]
    ) -> list[str]:
        """필드를 _CHUNK_SIZE 묶음으로 쪼개 **동시** VLM 호출하고 원시응답 리스트를 반환.

        한 묶음(또는 그 이하)이면 단일 호출(스레드 없음). 여러 묶음이면 ThreadPoolExecutor로
        동시 호출 — `_generate`는 무상태(self 설정만 읽음)라 스레드 안전. 같은 페이지 이미지를
        보내므로 vLLM prefix 캐시가 이미지 prefill을 재사용한다. 한 묶음이 실패(예외)하면 전체
        실패로 전파(조용한 실패 금지 — 부분 결과를 정답인 척 돌려주지 않는다).
        """
        chunks = [
            fields[i : i + _CHUNK_SIZE] for i in range(0, len(fields), _CHUNK_SIZE)
        ]
        if len(chunks) <= 1:
            return [self._generate(_extract_prompt(fields), pages)]
        from concurrent.futures import ThreadPoolExecutor

        prompts = [_extract_prompt(chunk) for chunk in chunks]
        with ThreadPoolExecutor(
            max_workers=min(len(chunks), _MAX_CONCURRENCY)
        ) as executor:
            return list(executor.map(lambda p: self._generate(p, pages), prompts))

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
        bbox = _box_to_bbox(payload.get("bbox") or payload.get("box_2d"), page, sizes)
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

    # ── vLLM(OpenAI 호환) 호출 ────────────────────────────────────────────
    def _generate(self, prompt: str, pages: list["_Page"]) -> str:
        if self._responder is not None:
            return self._responder(prompt, pages)
        if not self._base_url:
            return ""
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for page in pages:
            b64 = base64.b64encode(page.png).decode("ascii")
            content.append({"type": "text", "text": f"[page {page.number}]"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        # response_format(guided json)은 일부 vLLM/멀티모달 조합서 400을 유발한다 — 쓰지
        # 않고 프롬프트로 JSON을 요구한 뒤 robust 파서(extract_json_object)로 뽑는다.
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": content},
            ],
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # 도달 불가·4xx/5xx는 예외로 전파(조용한 실패 금지). 메시지에 base_url/키는 노출하지
        # 않되, 서버가 준 에러 본문 일부는 진단 위해 싣는다(키 비포함).
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"VLM 추론 서버 오류 {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            return ""
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        return str(message.get("content") or "")


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
def _extract_prompt(fields: list[SchemaField]) -> str:
    field_specs = "\n".join(
        f"- {f.key} (type={f.type}"
        + (f", desc={f.description}" if f.description else "")
        + (", required" if f.required else "")
        + ")"
        for f in fields
    )
    keys = ", ".join(f.key for f in fields)
    return (
        "추출할 필드:\n"
        f"{field_specs}\n\n"
        "value는 문서에 적힌 **그대로**(숫자·단위·구두점 포함) 옮겨라(요약·환산 금지). "
        "필드 type 에 맞춰 읽되 표기는 문서 그대로 둔다 — date=적힌 날짜 그대로(구분자 포함), "
        "int/float=숫자·콤마·소수점·단위 그대로, boolean=동의/예→true·비동의/아니오→false. "
        "각 필드에 value(문자열 또는 null), page(1-base 정수), "
        "bbox([x_min,y_min,x_max,y_max] 0~1000, 좌상단 원점·x가로·y세로), "
        "confidence(0~1)를 반환하라.\n"
        '출력 JSON: {"fields":[{"key":"<필드명>","value":<값 또는 null>,'
        '"page":<페이지>,"bbox":[<x_min>,<y_min>,<x_max>,<y_max>],"confidence":<0~1>}]}\n'
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
    """bbox([x_min,y_min,x_max,y_max] 0~1000, 좌상단 원점) → PDF 좌표 bbox(x0,y0,x1,y1).

    Qwen-VL 관례는 x를 먼저 쓴다([x_min,y_min,...]) — Gemini의 y먼저(box_2d)와 다르다.
    축을 맞추지 않으면 박스가 세로로/엉뚱한 위치로 그려진다.
    """
    if page is None or page not in sizes:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x_min, y_min, x_max, y_max = (float(v) for v in raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    width, height = sizes[page]
    x0 = x_min / 1000.0 * width
    y0 = y_min / 1000.0 * height
    x1 = x_max / 1000.0 * width
    y1 = y_max / 1000.0 * height
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """매칭용 정규화 — 공백 제거 + 소문자(한글/숫자 위치 매칭 견고)."""
    return _WS_RE.sub("", text).lower()


def _ground_with_lines(
    path: Path, fields: list[ExtractedField]
) -> list[ExtractedField]:
    """추출 값 텍스트를 페이지 라인(텍스트레이어/OCR)에서 찾아 bbox를 정확히 교체한다.

    VLM이 돌려준 bbox는 러프하다. 같은 페이지 라인 중 **값을 포함하는 라인**의 bbox로
    바꾼다(여러 개면 합집합). 매칭 실패·라인 없음(순수 손글씨)·짧은 값은 VLM box 유지.
    실패는 조용히 원본 유지(로깅) — 값 추출 자체엔 영향 없음.
    """
    if not any(f.value for f in fields):
        return fields
    try:
        from kms.adapters.ingestion.lines.registry import LineProviderRegistry

        lines = LineProviderRegistry().extract_lines(path)
    except Exception as exc:  # noqa: BLE001 — grounding 실패는 VLM box로 폴백.
        logger.debug("라인 grounding 생략(라인 추출 실패): %s", exc)
        return fields
    if not lines:
        return fields

    by_page: dict[int, list[tuple[str, BBox]]] = {}
    for ln in lines:
        if ln.bbox is not None:
            by_page.setdefault(ln.page, []).append((_norm(ln.text), ln.bbox))

    grounded: list[ExtractedField] = []
    for f in fields:
        box = _match_value_box(f, by_page)
        grounded.append(f.model_copy(update={"bbox": box}) if box else f)
    return grounded


def _match_value_box(
    field: ExtractedField, by_page: dict[int, list[tuple[str, BBox]]]
) -> BBox | None:
    """필드 값을 (해당 페이지 우선) 라인에서 찾아 매칭 라인 bbox 합집합을 반환. 없으면 None."""
    if not field.value:
        return None
    needle = _norm(field.value)
    if len(needle) < 3:  # 너무 짧은 값(예/3 등)은 오매칭 위험 → grounding 안 함.
        return None
    pages = [field.page] if field.page in by_page else list(by_page.keys())
    for page in pages:
        matched = [bbox for text, bbox in by_page.get(page, []) if needle in text]
        if matched and len(matched) <= 3:  # 너무 많이 걸리면(모호) 건너뜀.
            x0 = min(b[0] for b in matched)
            y0 = min(b[1] for b in matched)
            x1 = max(b[2] for b in matched)
            y1 = max(b[3] for b in matched)
            return (x0, y0, x1, y1)
    return None
