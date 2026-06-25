"""extract_json_object — 코드펜스·후행쉼표·잘림 방어 + 필드 살베이지 회귀 테스트."""

from __future__ import annotations

from kms.adapters.extraction.json_util import extract_json_object


def test_clean_json() -> None:
    obj = extract_json_object('{"fields":[{"key":"a","value":"1"}]}')
    assert obj == {"fields": [{"key": "a", "value": "1"}]}


def test_code_fence() -> None:
    obj = extract_json_object('```json\n{"fields":[{"key":"a"}]}\n```')
    assert obj == {"fields": [{"key": "a"}]}


def test_trailing_comma() -> None:
    # LLM이 흔히 넣는 후행 쉼표(`},]`) — 정리 후 파싱.
    obj = extract_json_object('{"fields":[{"key":"a","value":"1"},]}')
    assert obj == {"fields": [{"key": "a", "value": "1"}]}


def test_truncated_recovers_complete_fields() -> None:
    # 외곽이 닫히지 않은(잘린) 출력 — 완성된 필드만 살베이지.
    raw = '{"fields":[{"key":"a","value":"1"},{"key":"b","value":"2"'
    obj = extract_json_object(raw)
    assert obj is not None
    assert obj["fields"] == [{"key": "a", "value": "1"}]


def test_unparseable_returns_none() -> None:
    assert extract_json_object("그냥 텍스트, JSON 아님") is None
    assert extract_json_object("") is None
