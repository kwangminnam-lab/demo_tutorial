"""LLM 기반 필드추출 — 번호 매긴 라인을 텍스트 LLM(gpt-oss 등)에 투입해 구조화 추출.

핵심(ADR-024 grounding): LLM에 `[id] text` 라인을 주고 `{key, value,
evidence_line_ids, confidence}`를 받는다. 좌표는 LLM이 아니라 `evidence_line_ids`가
가리키는 라인의 bbox에서 역산한다 — 텍스트 LLM만으로 B-Box 근거가 확정된다(이미지
입력 불요). 응답이 JSON이 아닐 수 있어(서버가 response_format 미지원) 방어적으로
파싱한다(코드펜스 제거 + 첫 객체 추출). 파싱 실패는 조용히 넘기지 않고 로깅 후
"값 없음" 결과로 떨어뜨린다(예외로 적재 전체를 죽이지 않음).
"""

from __future__ import annotations

import logging

from kms.adapters.extraction.json_util import (
    coerce_float,
    coerce_int_list,
    extract_json_object,
    opt_str,
)
from kms.adapters.llm.base import LLMClient
from kms.domain.extraction import (
    BBox,
    ExtractedField,
    ExtractionSchema,
    SchemaField,
    TextLine,
)

logger = logging.getLogger(__name__)

# 라인 텍스트를 LLM에 넣을 때의 문자 예산 — 초과분은 잘라내고 로깅한다(조용한 절단 금지).
_DEFAULT_MAX_INPUT_CHARS = 12000
# 신뢰도 임계 — 미만이면 needs_review(자동 확정 금지). 금융 문서 안전장치.
_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

_JSON_RESPONSE_FORMAT: dict[str, object] = {"type": "json_object"}

_EXTRACT_SYSTEM = (
    "너는 금융 문서에서 지정된 필드를 추출하는 도구다. 입력은 `[id] 텍스트` 형식으로 "
    "번호 매긴 문서 라인이다. 각 필드의 값을 찾고, 그 근거가 된 라인 번호를 "
    "evidence_line_ids로 반드시 함께 반환한다. 값을 찾지 못하면 value=null, "
    "evidence_line_ids=[]로 둔다. 추정/창작 금지 — 문서에 있는 값만. "
    "오직 JSON만 출력한다(설명·코드펜스 없이)."
)


class LlmFieldExtractor:
    """텍스트 LLM으로 스키마 필드를 추출하는 FieldExtractor 구현."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_input_chars: int = _DEFAULT_MAX_INPUT_CHARS,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._llm = llm
        self._max_input_chars = max_input_chars
        self._threshold = confidence_threshold

    # ── 추출 ──────────────────────────────────────────────────────────────
    def extract(
        self, lines: list[TextLine], schema: ExtractionSchema
    ) -> list[ExtractedField]:
        if not lines:
            return [self._empty_field(f.key) for f in schema.fields]

        numbered = self._numbered_lines(lines)
        prompt = self._extract_prompt(numbered, schema)
        raw = self._llm.generate(
            prompt, system=_EXTRACT_SYSTEM, response_format=_JSON_RESPONSE_FORMAT
        )
        parsed = _parse_fields(raw)
        by_id = {line.line_id: line for line in lines}

        results: list[ExtractedField] = []
        for field in schema.fields:
            payload = parsed.get(field.key)
            if payload is None:
                results.append(self._empty_field(field.key))
                continue
            results.append(self._build_field(field, payload, by_id))
        return results

    def _build_field(
        self,
        field: SchemaField,
        payload: dict[str, object],
        by_id: dict[int, TextLine],
    ) -> ExtractedField:
        value = payload.get("value")
        value_str = None if value is None else str(value).strip() or None
        evidence_ids = coerce_int_list(payload.get("evidence_line_ids"))
        confidence = coerce_float(payload.get("confidence"))
        page, bbox = _evidence_location(evidence_ids, by_id)
        needs_review = (
            value_str is None
            or confidence is None
            or confidence < self._threshold
        )
        # 근거 라인이 실제 존재하는 것만 남긴다(LLM이 없는 id를 줄 수 있음).
        valid_ids = [i for i in evidence_ids if i in by_id]
        return ExtractedField(
            key=field.key,
            value=value_str,
            page=page,
            bbox=bbox,
            evidence_line_ids=valid_ids,
            source="print",  # phase 1은 디지털/인쇄만. 손글씨 tier는 후속.
            confidence=confidence,
            needs_review=needs_review,
        )

    @staticmethod
    def _empty_field(key: str) -> ExtractedField:
        return ExtractedField(key=key, value=None, needs_review=True)

    # ── 스키마 자동 제안 ──────────────────────────────────────────────────
    def propose_schema(
        self, lines: list[TextLine], *, doc_type: str | None = None
    ) -> list[SchemaField]:
        if not lines:
            return []
        numbered = self._numbered_lines(lines)
        kind = f"문서 종류: {doc_type}\n" if doc_type else ""
        prompt = (
            f"{kind}다음 문서에서 반복 추출 가치가 높은 핵심 필드를 제안하라. "
            "각 필드는 key(필드명), type(String|int|float|boolean|date), "
            "description(추출 지침), required(boolean)를 가진다. "
            "key(필드명)는 한국어로 작성한다. "
            "동의/비동의·예/아니오 같은 값은 boolean 으로 제안한다. "
            'JSON만 출력: {"fields":[{"key":...,"type":...,"description":...,"required":...}]}\n\n'
            f"{numbered}"
        )
        raw = self._llm.generate(
            prompt,
            system="너는 문서에서 추출 스키마(필드 목록)를 설계하는 도구다. JSON만 출력한다.",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        return _parse_schema_fields(raw)

    # ── 프롬프트 빌드 ─────────────────────────────────────────────────────
    def _numbered_lines(self, lines: list[TextLine]) -> str:
        rendered: list[str] = []
        used = 0
        truncated = 0
        for line in lines:
            entry = f"[{line.line_id}] {line.text}"
            if used + len(entry) + 1 > self._max_input_chars:
                truncated = len(lines) - len(rendered)
                break
            rendered.append(entry)
            used += len(entry) + 1
        if truncated:
            logger.warning(
                "추출 입력 라인 절단: %d/%d 라인만 사용(문자 예산 %d 초과)",
                len(rendered),
                len(lines),
                self._max_input_chars,
            )
        return "\n".join(rendered)

    @staticmethod
    def _extract_prompt(numbered: str, schema: ExtractionSchema) -> str:
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
            "각 필드에 대해 value(문자열 또는 null), evidence_line_ids(정수 배열), "
            "confidence(0~1)를 반환하라. 출력 JSON 형식:\n"
            '{"fields":[{"key":"<필드명>","value":<값 또는 null>,'
            '"evidence_line_ids":[<라인번호>],"confidence":<0~1>}]}\n'
            f"필드 키는 정확히 다음만 사용: {keys}\n\n"
            "문서 라인:\n"
            f"{numbered}"
        )


# ── JSON 방어 파싱 (공용 유틸은 json_util) ────────────────────────────────
def _parse_fields(raw: str) -> dict[str, dict[str, object]]:
    """추출 응답 → {key: {value, evidence_line_ids, confidence}} 맵."""
    obj = extract_json_object(raw)
    if obj is None:
        return {}
    fields = obj.get("fields")
    if not isinstance(fields, list):
        return {}
    out: dict[str, dict[str, object]] = {}
    for item in fields:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            out[key] = item
    return out


def _parse_schema_fields(raw: str) -> list[SchemaField]:
    """스키마 제안 응답 → SchemaField 목록(검증 실패 항목은 건너뜀)."""
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


def _evidence_location(
    evidence_ids: list[int], by_id: dict[int, TextLine]
) -> tuple[int | None, BBox | None]:
    """근거 라인들에서 페이지 + 합집합 bbox를 역산한다(첫 유효 근거의 페이지 기준)."""
    valid = [by_id[i] for i in evidence_ids if i in by_id]
    if not valid:
        return None, None
    page = valid[0].page
    same_page = [line for line in valid if line.page == page]
    x0 = min(line.bbox[0] for line in same_page)
    y0 = min(line.bbox[1] for line in same_page)
    x1 = max(line.bbox[2] for line in same_page)
    y1 = max(line.bbox[3] for line in same_page)
    return page, (x0, y0, x1, y1)
