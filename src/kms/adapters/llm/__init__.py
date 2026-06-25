"""LLM 어댑터 — 로컬 Gemma 추론 서버 클라이언트 (ADR-007).

`LLMClient` 프로토콜 뒤로 실구현만 둔다: `OpenAICompatLLM`(OpenAI 호환 로컬 추론
서버). 멀티 프로바이더는 `router.LLMRouter`가 요청별 provider/api_key로 라우팅한다.
외부 상용 LLM API는 기본 경로가 아니다 — 민감 자료를 외부로 보내지 않기 위함
(ADR-007). 테스트는 모델·네트워크 없는 스텁(`tests/_stub_llm.StubLLM`)을 주입한다.
"""

from kms.adapters.llm.base import LLMClient
from kms.adapters.llm.openai_compat import OpenAICompatLLM

__all__ = [
    "LLMClient",
    "OpenAICompatLLM",
]
