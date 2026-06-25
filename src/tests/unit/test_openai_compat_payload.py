"""OpenAICompatLLM._payload 단위 테스트 — response_format 전달 + 하위호환.

네트워크 없이 payload 구성만 검증한다(시그니처/하위호환 보장 — 실서버 미연결).
"""

from __future__ import annotations

from kms.adapters.llm.base import TokenUsage
from kms.adapters.llm.openai_compat import OpenAICompatLLM, _usage_from


def _llm() -> OpenAICompatLLM:
    return OpenAICompatLLM("http://localhost:8001/v1", "test-model", disable_thinking=False)


def test_response_format_included_when_given() -> None:
    payload = _llm()._payload(
        "q", None, stream=False, response_format={"type": "json_object"}
    )
    assert payload["response_format"] == {"type": "json_object"}


def test_response_format_omitted_by_default() -> None:
    payload = _llm()._payload("q", None, stream=False)
    assert "response_format" not in payload


def test_payload_keeps_core_fields() -> None:
    payload = _llm()._payload("질문", "시스템", stream=True)
    assert payload["model"] == "test-model"
    assert payload["stream"] is True
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": "시스템"}
    assert messages[1] == {"role": "user", "content": "질문"}


def test_stream_payload_requests_usage() -> None:
    # 스트리밍은 마지막 청크에 usage가 실리도록 include_usage 를 요청한다(Cost/Usage용).
    assert _llm()._payload("q", None, stream=True)["stream_options"] == {
        "include_usage": True
    }


def test_non_stream_payload_omits_stream_options() -> None:
    assert "stream_options" not in _llm()._payload("q", None, stream=False)


def test_usage_from_parses_tokens() -> None:
    u = _usage_from({"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})
    assert u == TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20)


def test_usage_from_derives_total_when_missing() -> None:
    u = _usage_from({"prompt_tokens": 5, "completion_tokens": 7})
    assert u == TokenUsage(prompt_tokens=5, completion_tokens=7, total_tokens=12)


def test_usage_from_none_when_absent_or_empty() -> None:
    assert _usage_from(None) is None
    assert _usage_from({}) is None


def test_parse_sse_splits_content_and_usage() -> None:
    # 일반 청크 = 콘텐츠만, usage 청크(choices 빈) = usage만.
    content, usage = _llm()._parse_sse(
        'data: {"choices":[{"delta":{"content":"안"}}]}'
    )
    assert content == "안" and usage is None
    content2, usage2 = _llm()._parse_sse(
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
    )
    assert content2 is None
    assert usage2 == TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5)


def test_parse_sse_ignores_done_and_blank() -> None:
    assert _llm()._parse_sse("data: [DONE]") == (None, None)
    assert _llm()._parse_sse("") == (None, None)
