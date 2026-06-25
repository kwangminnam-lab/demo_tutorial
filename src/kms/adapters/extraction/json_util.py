"""LLM/VLM 응답의 방어적 JSON 파싱 유틸 — 텍스트·비전 추출기 공용.

서버/모델이 코드펜스나 잡텍스트를 섞어도 첫 JSON 객체를 뽑고, 느슨한 타입을
안전하게 강제한다(파싱 실패는 호출자가 빈 결과로 처리 — 조용한 실패 아님).
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# 후행 쉼표(`,}` / `,]`) — LLM이 흔히 넣는 비표준 JSON.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# 중첩 없는 객체(필드 레코드 `{...}`) — 살베이지에서 개별 파싱.
_FLAT_OBJ_RE = re.compile(r"\{[^{}]*\}")


def _loads_lenient(s: str) -> object | None:
    """엄격 파싱 실패 시 후행 쉼표를 떼고 한 번 더 시도."""
    for cand in (s, _TRAILING_COMMA_RE.sub(r"\1", s)):
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def extract_json_object(raw: str) -> dict[str, object] | None:
    """LLM/VLM 응답에서 JSON 객체를 뽑는다(코드펜스·후행쉼표·잘림 방어).

    1) 첫 `{`~마지막 `}`를 (후행쉼표 정리 포함) 파싱.
    2) 실패 시 **살베이지** — 중첩 없는 객체들(필드 레코드)을 개별 파싱해 `{"fields":[...]}`
       로 복구한다(모델 출력이 잘리거나 외곽이 깨져도 완성된 필드는 살린다).
    """
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.lstrip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    if start == -1:
        return None

    end = text.rfind("}")
    if end > start:
        obj = _loads_lenient(text[start : end + 1])
        if isinstance(obj, dict):
            return obj

    # 살베이지: 완성된 필드 객체만 개별 복구(잘림/외곽 손상 견고).
    records = [
        o
        for m in _FLAT_OBJ_RE.finditer(text[start:])
        if isinstance(o := _loads_lenient(m.group(0)), dict)
    ]
    fields = [o for o in records if "key" in o]
    if fields:
        logger.warning("추출 응답 JSON 손상 — 필드 %d건 살베이지 복구", len(fields))
        return {"fields": fields}
    logger.warning("추출 응답 JSON 파싱 실패(살베이지도 0건)")
    return None


def coerce_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


def coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
