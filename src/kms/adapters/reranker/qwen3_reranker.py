"""Qwen3-Reranker-0.6B 기반 Reranker 구현.

Qwen3-Reranker는 cross-encoder(분류 헤드)가 아니라 **causal LM**이다 — query·document를
지시문 템플릿에 넣어 "yes"/"no" 토큰의 마지막 위치 logit을 비교해 관련도를 매긴다
(공식 사용법). 따라서 sentence-transformers `CrossEncoder`로는 못 올리고 transformers
`AutoModelForCausalLM`로 직접 로드한다. 첫 호출 시 lazy load — 적재만으론 가중치를
끌어오지 않는다. 외부 API 호출 없음(ADR-007), 로컬 추론만.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 기본 지시문 — 사내 문서 검색 관련도 판정용(공식 템플릿의 <Instruct> 슬롯).
_DEFAULT_INSTRUCTION = (
    "Given a user search query, retrieve documents from the knowledge base "
    "that are relevant to the query."
)
# 공식 Qwen3-Reranker 프롬프트 래퍼 — system은 yes/no만 답하도록 고정.
_PREFIX = (
    '<|im_start|>system\nJudge whether the Document meets the requirements '
    'based on the Query and the Instruct provided. Note that the answer can '
    'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def _resolve_device(explicit: str | None) -> str:
    """디바이스 결정 — 명시값 우선, 없으면 cuda→mps→cpu 자동."""
    if explicit:
        return explicit
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
    except Exception as exc:  # noqa: BLE001 — torch 미설치/probe 실패는 cpu 폴백 사유 로깅
        logger.debug("리랭커 디바이스 probe 실패 — cpu 폴백 (%s)", type(exc).__name__)
    return "cpu"


class Qwen3Reranker:
    """`Reranker` 프로토콜을 만족하는 Qwen3-Reranker(causal LM) 리랭커.

    `model_name`은 기본 `Qwen/Qwen3-Reranker-0.6B`. score()는 query·passage 쌍마다
    "yes" 토큰 확률(0~1)을 반환한다 — 큰 값일수록 관련도 높음.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Reranker-0.6B",
        *,
        max_length: int = 4096,
        device: str | None = None,
        instruction: str = _DEFAULT_INSTRUCTION,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._device = _resolve_device(device)
        self._instruction = instruction
        self._model: Any = None  # lazy load
        self._tokenizer: Any = None
        self._yes_id: int = -1
        self._no_id: int = -1
        self._prefix_ids: list[int] = []
        self._suffix_ids: list[int] = []

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        # transformers는 첫 사용 시점에만 로드 (가중치 적재 코스트가 큼).
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tok: Any = AutoTokenizer.from_pretrained(self._model_name, padding_side="left")
        model: Any = AutoModelForCausalLM.from_pretrained(self._model_name)
        self._tokenizer = tok
        self._model = model.to(self._device).eval()
        self._yes_id = int(tok.convert_tokens_to_ids("yes"))
        self._no_id = int(tok.convert_tokens_to_ids("no"))
        self._prefix_ids = tok.encode(_PREFIX, add_special_tokens=False)
        self._suffix_ids = tok.encode(_SUFFIX, add_special_tokens=False)

    def _pair_text(self, query: str, passage: str) -> str:
        return f"<Instruct>: {self._instruction}\n<Query>: {query}\n<Document>: {passage}"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        self._ensure_loaded()
        import torch

        tok = self._tokenizer
        # 본문만 길이 제한 토큰화(prefix/suffix 여유 확보) 후 래퍼 토큰을 앞뒤로 붙인다.
        budget = self._max_length - len(self._prefix_ids) - len(self._suffix_ids)
        rows: list[list[int]] = []
        for passage in passages:
            body = tok.encode(
                self._pair_text(query, passage),
                add_special_tokens=False,
                truncation=True,
                max_length=max(budget, 1),
            )
            rows.append(self._prefix_ids + body + self._suffix_ids)
        batch = tok.pad({"input_ids": rows}, padding=True, return_tensors="pt").to(self._device)
        with torch.no_grad():
            logits = self._model(**batch).logits[:, -1, :]  # 마지막 위치 다음 토큰 분포
            # yes/no 두 logit만 골라 softmax → yes 확률을 관련도 점수로.
            pair = torch.stack([logits[:, self._no_id], logits[:, self._yes_id]], dim=1)
            probs = torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp()
        return [float(p) for p in probs]
