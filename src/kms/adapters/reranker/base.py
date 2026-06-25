"""Reranker 프로토콜 — query·passage 쌍을 받아 relevance score 목록을 반환.

cross-encoder 계열 모델(BGE Reranker v2 m3 등)이 핵심 구현. 어댑터 격리(ADR-007)로
실 모델 의존성을 외부로 빼고, dev/테스트는 FakeReranker로 결정론 검증.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    """query 한 건 + passage N건을 받아 relevance score N개를 반환한다."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        """passages 순서 그대로 점수 리스트를 반환한다 (큰 값일수록 관련도 높음)."""
        ...
