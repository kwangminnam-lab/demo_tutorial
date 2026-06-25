"""BAAI/bge-reranker-v2-m3 cross-encoder 기반 Reranker 구현.

sentence-transformers의 `CrossEncoder`로 모델을 로드한다. 첫 호출 시 lazy load —
적재만으로는 가중치를 끌어오지 않는다. RRF top-N 후 chunk 본문을 query와 함께
점수 매겨 정밀 재정렬한다. 외부 API 호출 없음(ADR-007), 로컬 추론만.
"""

from __future__ import annotations

from typing import Any


class BgeReranker:
    """`Reranker` 프로토콜을 만족하는 cross-encoder 리랭커.

    `model_name`은 기본 `BAAI/bge-reranker-v2-m3` (0.6B). 다른 BGE 변종도 동일
    인터페이스. CPU/MPS 자동 선택 — sentence-transformers가 디바이스를 잡는다.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        max_length: int = 512,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._device = device
        self._model: Any = None  # lazy load

    def _ensure_loaded(self) -> Any:
        if self._model is None:
            # CrossEncoder는 첫 사용 시점에만 로드 (적재 코스트가 큼).
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
                device=self._device,
            )
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._ensure_loaded()
        pairs = [(query, p) for p in passages]
        scores = model.predict(pairs)
        # numpy array → 일반 list[float]로 평탄화.
        return [float(s) for s in scores]
