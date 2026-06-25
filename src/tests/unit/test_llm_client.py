"""LLMClient 단위 테스트 — OpenAICompatLLM(실 HTTP 없음) + StubLLM 테스트 더블.

검증 핵심:
- `OpenAICompatLLM`이 `LLMClient` 프로토콜을 만족 (실 HTTP 호출 없음).
- `OpenAICompatLLM._payload`의 thinking 토글.
- `StubLLM`(테스트 더블)의 결정론·출처 인용·스트림 분할 — RAG 테스트가 의존하므로 잠근다.
"""

from kms.adapters.llm import LLMClient, OpenAICompatLLM
from tests._stub_llm import StubLLM


def test_stub_generate_is_deterministic() -> None:
    stub = StubLLM()
    prompt = "다음 근거로 요약하라: [보고서.pdf] 매출이 늘었다 [회의록.docx]"

    first = stub.generate(prompt)
    second = stub.generate(prompt)

    assert first == second  # 같은 입력 → 항상 같은 출력


def test_stub_generate_cites_source_markers() -> None:
    stub = StubLLM()
    prompt = "질문에 답하라. 근거: [a.pdf] 본문 [b.txt]"

    answer = stub.generate(prompt)

    assert "[a.pdf]" in answer  # 출처 마커를 그대로 인용
    assert "[b.txt]" in answer


def test_stub_stream_concatenates_to_generate() -> None:
    stub = StubLLM(chunk_size=4)
    prompt = "근거 [doc1] [doc2] 로 요약"

    streamed = "".join(stub.stream(prompt))

    assert streamed == stub.generate(prompt)  # 조각을 합치면 단발 결과와 동일


def test_stub_stream_yields_multiple_chunks() -> None:
    stub = StubLLM(chunk_size=4)

    chunks = list(stub.stream("근거 [doc1] [doc2] 로 요약"))

    assert len(chunks) > 1  # 한 덩어리가 아니라 조각으로 나눠 yield


def test_openai_compat_satisfies_llmclient_protocol() -> None:
    # 인스턴스화만으로는 네트워크 연결이 일어나지 않는다(호출 시점에 연결).
    client = OpenAICompatLLM("http://localhost:8001/v1", "gemma")

    assert isinstance(client, LLMClient)  # 인터페이스 일치 (실 HTTP 없음)


def test_payload_disables_thinking_when_configured() -> None:
    """disable_thinking=True면 chat_template_kwargs로 thinking을 끈다(답변 잘림 방지)."""
    client = OpenAICompatLLM(
        "http://localhost:8001/v1", "gemma", disable_thinking=True
    )

    payload = client._payload("질문", "지시", stream=False)

    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_payload_omits_thinking_flag_by_default() -> None:
    """기본(disable_thinking=False)에선 thinking 관련 키를 넣지 않는다."""
    client = OpenAICompatLLM("http://localhost:8001/v1", "gemma")

    payload = client._payload("질문", None, stream=False)

    assert "chat_template_kwargs" not in payload
