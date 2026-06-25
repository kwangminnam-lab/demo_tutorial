"""임베딩 인터페이스 + 결정론적 Fake 기본 구현.

실 임베딩 모델은 다운로드·GPU·지연을 유발해 자동 테스트·CI를 막는다. 그래서
임베딩을 `Embedder` 프로토콜 뒤로 격리하고, 기본·테스트 경로에는 해시 기반
결정론적 `FakeEmbedder`를 쓴다. 실모델은 후속 phase에서 config 뒤로 주입한다
(ADR-007 LLM 어댑터 격리와 같은 원칙 — 구현 교체 가능하게 인터페이스로 분리).
"""

from __future__ import annotations

import hashlib
import random
from typing import Protocol


class Embedder(Protocol):
    """텍스트 목록을 고정 차원 임베딩 벡터로 변환한다."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class FakeEmbedder:
    """해시 기반 결정론적 임베더 — 같은 텍스트는 항상 같은 벡터.

    실모델 없이 색인·검색 로직을 결정론적으로 테스트하기 위한 기본 구현.
    의미적 유사도는 없지만, 같은 텍스트로 만든 쿼리 벡터는 색인된 그 청크와
    정확히 일치(코사인 거리 0)하므로 검색 경로 검증에는 충분하다. 인스턴스가
    달라도 같은 입력이면 같은 벡터를 낸다 (SHA-256 시드 → 고정 PRNG 시퀀스).
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        # 파이썬 내장 hash()는 프로세스마다 솔트가 달라 비결정적이므로 SHA-256을 쓴다.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
