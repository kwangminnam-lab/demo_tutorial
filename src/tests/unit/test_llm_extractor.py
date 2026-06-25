"""LlmFieldExtractor 단위 테스트 — JSON 파싱·grounding(bbox 역참조)·신뢰도 임계.

LLM은 카나리 JSON을 돌려주는 결정론 더블로 대체한다(모델·네트워크 없음).
"""

from __future__ import annotations

from kms.adapters.extraction.llm_extractor import LlmFieldExtractor
from kms.domain.extraction import ExtractionSchema, SchemaField, TextLine


class _CannedLLM:
    """generate가 미리 정한 문자열을 그대로 반환하는 LLM 더블."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.last_response_format: dict[str, object] | None = None

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        self.last_response_format = response_format
        return self.response

    def stream(self, prompt: str, *, system: str | None = None):  # type: ignore[no-untyped-def]
        yield self.response


def _lines() -> list[TextLine]:
    return [
        TextLine(line_id=0, text="계약일 2026-03-01", page=1, bbox=(10, 20, 100, 32)),
        TextLine(line_id=1, text="계약금액 1,200,000,000", page=1, bbox=(10, 40, 120, 52)),
        TextLine(line_id=2, text="당사자 홍길동", page=2, bbox=(10, 20, 80, 32)),
    ]


def _schema() -> ExtractionSchema:
    return ExtractionSchema(
        name="계약서",
        fields=[SchemaField(key="계약일", type="date"), SchemaField(key="계약금액", type="money")],
    )


def test_extract_maps_value_and_bbox() -> None:
    response = (
        '{"fields":['
        '{"key":"계약일","value":"2026-03-01","evidence_line_ids":[0],"confidence":0.95},'
        '{"key":"계약금액","value":"1,200,000,000","evidence_line_ids":[1],"confidence":0.9}'
        "]}"
    )
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), _schema())

    by_key = {f.key: f for f in fields}
    assert by_key["계약일"].value == "2026-03-01"
    assert by_key["계약일"].page == 1
    assert by_key["계약일"].bbox == (10, 20, 100, 32)
    assert by_key["계약일"].evidence_line_ids == [0]
    assert by_key["계약일"].needs_review is False  # confidence 0.95 ≥ 0.6
    assert by_key["계약금액"].value == "1,200,000,000"


def test_response_format_is_json_object() -> None:
    llm = _CannedLLM('{"fields":[]}')
    LlmFieldExtractor(llm).extract(_lines(), _schema())
    assert llm.last_response_format == {"type": "json_object"}


def test_low_confidence_sets_needs_review() -> None:
    response = '{"fields":[{"key":"계약일","value":"x","evidence_line_ids":[0],"confidence":0.3}]}'
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), ExtractionSchema(name="s", fields=[SchemaField(key="계약일")]))
    assert fields[0].needs_review is True


def test_missing_field_becomes_empty_needs_review() -> None:
    # 응답에 계약금액이 없음 → 빈 필드 + needs_review.
    response = '{"fields":[{"key":"계약일","value":"2026-03-01","evidence_line_ids":[0],"confidence":0.9}]}'
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), _schema())
    by_key = {f.key: f for f in fields}
    assert by_key["계약금액"].value is None
    assert by_key["계약금액"].needs_review is True


def test_defensive_parse_code_fence_and_junk() -> None:
    response = (
        "설명 텍스트\n```json\n"
        '{"fields":[{"key":"계약일","value":"2026-03-01","evidence_line_ids":[0],"confidence":0.8}]}'
        "\n```\n끝"
    )
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), ExtractionSchema(name="s", fields=[SchemaField(key="계약일")]))
    assert fields[0].value == "2026-03-01"


def test_unparseable_response_yields_empty_fields() -> None:
    extractor = LlmFieldExtractor(_CannedLLM("죄송합니다 JSON을 못 만들었습니다"))
    fields = extractor.extract(_lines(), _schema())
    assert all(f.value is None and f.needs_review for f in fields)


def test_invalid_evidence_ids_dropped() -> None:
    # 존재하지 않는 라인 99 → bbox/page None, evidence_line_ids에서 제거.
    response = '{"fields":[{"key":"계약일","value":"v","evidence_line_ids":[99],"confidence":0.9}]}'
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), ExtractionSchema(name="s", fields=[SchemaField(key="계약일")]))
    assert fields[0].evidence_line_ids == []
    assert fields[0].page is None
    assert fields[0].bbox is None


def test_evidence_union_bbox_same_page() -> None:
    # 두 근거 라인(같은 페이지) → 합집합 사각형.
    response = '{"fields":[{"key":"계약일","value":"v","evidence_line_ids":[0,1],"confidence":0.9}]}'
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.extract(_lines(), ExtractionSchema(name="s", fields=[SchemaField(key="계약일")]))
    assert fields[0].bbox == (10, 20, 120, 52)  # min/min/max/max of lines 0,1


def test_empty_lines_returns_empty_fields_without_llm() -> None:
    llm = _CannedLLM("should not be called")
    extractor = LlmFieldExtractor(llm)
    fields = extractor.extract([], _schema())
    assert len(fields) == 2
    assert all(f.value is None and f.needs_review for f in fields)
    assert llm.last_response_format is None  # generate 미호출.


def test_propose_schema_parses_fields() -> None:
    response = (
        '{"fields":['
        '{"key":"계약일","type":"date","description":"계약 체결일","required":true},'
        '{"key":"금액","type":"money","required":false}'
        "]}"
    )
    extractor = LlmFieldExtractor(_CannedLLM(response))
    fields = extractor.propose_schema(_lines(), doc_type="계약서")
    by_key = {f.key: f for f in fields}
    assert by_key["계약일"].type == "date"
    assert by_key["계약일"].required is True
    assert by_key["금액"].type == "money"
