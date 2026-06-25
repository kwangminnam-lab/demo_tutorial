"""테스트 전용 결정론적 LLM 스텁 — 모델·네트워크 없이 RAG 체인을 검증한다.

프로덕션 LLM 어댑터에는 fake가 없다(실 구현만: OpenAICompatLLM + 라우터 provider).
테스트는 비결정적·서버 의존을 피해야 하므로 여기 테스트 더블을 둔다(CONVENTIONS:
LLM은 모킹/계약 테스트). 프롬프트의 출처 마커(`[...]`)를 추출해 그대로 인용하는
고정 요약을 만든다 — 같은 입력→같은 출력이라 프롬프트 구성·인용 형식 검증에 충분.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# RAG 컨텍스트의 출처 마커(예: `[보고서.pdf]`, `[1]`) — 중첩 없는 대괄호 토큰.
_MARKER_RE = re.compile(r"\[[^\[\]]+\]")


class StubLLM:
    """결정론적 LLM 스텁. `LLMClient` 프로토콜을 만족한다(모델·네트워크 없음)."""

    def __init__(self, chunk_size: int = 16) -> None:
        # stream이 generate 결과를 나눌 조각 크기. 합치면 generate와 동일.
        self._chunk_size = chunk_size

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        markers = _MARKER_RE.findall(prompt)
        if markers:
            citations = " ".join(markers)
            return f"요약: 근거 {citations}"
        return f"요약: {prompt.strip()[:200]}"

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        text = self.generate(prompt, system=system)
        for start in range(0, len(text), self._chunk_size):
            yield text[start : start + self._chunk_size]
