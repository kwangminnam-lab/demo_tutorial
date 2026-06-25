"""FakeReranker — 테스트·dev용 결정론적 Reranker 구현.

query·passage 공통 토큰 개수를 점수로 쓴다. 모델·네트워크 의존 없이 동일 입력
→ 동일 출력. SearchService의 rerank 경로를 결정론적으로 검증한다.
"""

from __future__ import annotations


class FakeReranker:
    """단순 토큰 일치 카운트 기반 reranker (테스트 격리)."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        q_tokens = set(query.split())
        return [
            float(len(q_tokens & set(p.split()))) for p in passages
        ]
