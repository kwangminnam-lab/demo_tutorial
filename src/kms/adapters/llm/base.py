"""LLM 클라이언트 인터페이스 — 단발 생성과 토큰 스트리밍 계약.

ADR-007: 민감한 사내 자료를 외부 API로 보내지 않으려 LLM은 로컬 Gemma 모델을
OpenAI 호환 로컬 추론 서버(Ollama/vLLM 등) 경유로 호출한다(`OpenAICompatLLM`).
프로덕션에는 fake LLM이 없다 — 실서버 연결은 다운로드·GPU·지연을 유발해 자동
테스트·CI를 막으므로, 테스트는 모델·네트워크 없는 결정론적 스텁
(`tests/_stub_llm.StubLLM`)을 이 프로토콜 뒤로 주입한다(구현 교체 가능).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TokenUsage:
    """LLM 호출 1건의 토큰 사용량 — 관측(Langfuse Usage/Cost)용.

    OpenAI 호환 응답의 `usage`(prompt/completion/total_tokens)를 담는다. 서버가
    usage를 안 주면 호출자는 `None`을 쓴다(추적은 토큰 없이 진행 — 조용한 실패 아님).
    cost는 Langfuse가 model 가격표로 환산하므로 여기엔 토큰만 둔다.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@runtime_checkable
class LLMClient(Protocol):
    """LLM 호출 계약 — 컨텍스트 주입 RAG(ADR-007)의 LLM 경계."""

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        """프롬프트(+선택 `system`)로 완성 텍스트를 한 번에 반환한다.

        `response_format`은 OpenAI 호환 구조화 출력 힌트다(예: `{"type":"json_object"}`).
        서버가 지원하면 JSON 강제, 미지원이면 무시될 수 있어 호출자가 방어적으로
        파싱한다(필드추출 등). 미지정(None)이면 기존 동작과 동일하다(하위 호환).
        """
        ...

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """완성 텍스트를 조각(토큰/청크)으로 나눠 순서대로 yield한다.

        조각을 모두 이어 붙이면 `generate`와 동일한 텍스트가 된다.
        """
        ...
