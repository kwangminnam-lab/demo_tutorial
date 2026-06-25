"""리랭커 어댑터 — RRF top-k를 query-chunk pairwise 점수로 재정렬.

- `Qwen3Reranker`: Qwen3-Reranker-0.6B (causal LM, yes/no logit) — 기본.
- `BgeReranker`: BAAI/bge-reranker-v2-m3 (cross-encoder) — 대체 구현.
- `FakeReranker`: dev/테스트 결정론 격리용.
"""

from kms.adapters.reranker.base import Reranker
from kms.adapters.reranker.bge_reranker import BgeReranker
from kms.adapters.reranker.fake import FakeReranker
from kms.adapters.reranker.qwen3_reranker import Qwen3Reranker

__all__ = ["Reranker", "BgeReranker", "FakeReranker", "Qwen3Reranker"]
