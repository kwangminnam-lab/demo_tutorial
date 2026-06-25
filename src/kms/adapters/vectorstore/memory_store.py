"""InMemory 벡터 저장소 — 프로세스 메모리에 코사인 유사도 색인 (서버 불요).

테스트·dev 기본 구현이다(ChromaDB ephemeral 모드를 대체). 외부 의존 없이
색인·검색·`where` 필터 로직을 결정론적으로 검증한다. `OpenSearchVectorStore`와
같은 인터페이스(base.VectorStore) + 같은 `where` 방언을 만족한다.

권한 정책 판단은 search-service 책임 — 여기서는 `where` 방언을 그대로 평가하는
메커니즘만 제공한다(`access<=N` 범위 비교 등).
"""

from __future__ import annotations

import math
from typing import Any

from kms.adapters.ingestion.chunk.models import Chunk
from kms.adapters.vectorstore.base import chunk_to_meta
from kms.adapters.vectorstore.embedder import Embedder


class InMemoryVectorStore:
    """dict 기반 인메모리 벡터 저장소. `VectorStore` 프로토콜을 만족한다."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        # chunk_id → (embedding, text, metadata). 같은 id는 덮어쓴다(멱등 upsert).
        self._store: dict[str, tuple[list[float], str, dict[str, Any]]] = {}

    def index(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        embeddings = self._embedder.embed([chunk.text for chunk in chunks])
        for chunk, embedding in zip(chunks, embeddings):
            self._store[chunk.chunk_id] = (
                list(embedding),
                chunk.text,
                chunk_to_meta(chunk.metadata),
            )

    def query(
        self,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, dict[str, Any], float]]:
        scored: list[tuple[str, str, dict[str, Any], float]] = []
        for chunk_id, (vector, text, meta) in self._store.items():
            if where is not None and not _match_where(meta, where):
                continue
            scored.append((chunk_id, text, meta, _cosine(embedding, vector)))
        scored.sort(key=lambda item: item[3], reverse=True)
        return scored[:top_k]

    def get(self, chunk_ids: list[str]) -> list[tuple[str, str, dict[str, Any]]]:
        out: list[tuple[str, str, dict[str, Any]]] = []
        for chunk_id in chunk_ids:
            item = self._store.get(chunk_id)
            if item is not None:
                _vector, text, meta = item
                out.append((chunk_id, text, meta))
        return out

    def ping(self) -> None:
        """인메모리 저장소는 외부 의존이 없어 항상 도달 가능(no-op)."""
        return None

    def delete_by_source_url(self, source_url: str) -> int:
        victims = [
            chunk_id
            for chunk_id, (_v, _t, meta) in self._store.items()
            if meta.get("source_url") == source_url
        ]
        for chunk_id in victims:
            del self._store[chunk_id]
        return len(victims)


def _cosine(a: list[float], b: list[float]) -> float:
    """코사인 유사도. 영벡터(노름 0)는 0.0 — source_url 보강의 0-벡터 호출 호환."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _match_where(meta: dict[str, Any], where: dict[str, Any]) -> bool:
    """메타가 `where` 방언(base 모듈 docstring)을 만족하는지 평가한다.

    `$and`/`$or`는 절을 재귀 결합하고, 그 외 키는 `필드: 조건`으로 본다.
    조건이 연산자 dict면 각 연산자를 적용, 아니면 등호 비교.
    """
    for key, condition in where.items():
        if key == "$and":
            if not all(_match_where(meta, clause) for clause in condition):
                return False
        elif key == "$or":
            if not any(_match_where(meta, clause) for clause in condition):
                return False
        elif not _match_field(meta.get(key), condition):
            return False
    return True


def _match_field(value: Any, condition: Any) -> bool:
    """단일 필드 값이 조건(연산자 dict 또는 스칼라 등호)을 만족하는지."""
    if not isinstance(condition, dict):
        return bool(value == condition)
    for op, operand in condition.items():
        if op == "$eq" and not value == operand:
            return False
        if op == "$ne" and not value != operand:
            return False
        if op == "$in" and value not in operand:
            return False
        if op == "$nin" and value in operand:
            return False
        # 순서 비교는 값이 None이면(필드 부재) 미충족 처리.
        if op in ("$lte", "$gte", "$lt", "$gt"):
            if value is None or not _compare(op, value, operand):
                return False
    return True


def _compare(op: str, value: Any, operand: Any) -> bool:
    if op == "$lte":
        return bool(value <= operand)
    if op == "$gte":
        return bool(value >= operand)
    if op == "$lt":
        return bool(value < operand)
    return bool(value > operand)  # $gt
