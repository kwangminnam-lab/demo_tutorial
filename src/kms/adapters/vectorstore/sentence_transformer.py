"""sentence-transformers 기반 실 임베더 어댑터 (Embedder 구현).

해시 기반 `FakeEmbedder`와 달리 의미 임베딩을 만든다. sentence-transformers·torch는
무겁고(다운로드·GPU·메모리) 미설치 환경·CI를 깨뜨리므로 **lazy import**한다 —
모듈 import만으로는 의존을 요구하지 않고(이 파일 최상단은 stdlib만 import), 생성자에서
모델을 로드할 때 비로소 import한다. 미설치면 설치 안내가 담긴 명확한 RuntimeError를
던진다(조용한 폴백 금지 — CONVENTIONS 에러 처리).

한국어 문서가 많아 기본 모델은 multilingual이다(Settings.embedding_model_name).

**디바이스 가속(GPU)**: `device=None`(기본)이면 CUDA→MPS(Apple)→CPU 순으로 자동
선택한다. K8s에서 GPU 노드(`nvidia.com/gpu`)에 스케줄되고 CUDA 빌드 torch가 깔리면
CUDA를 잡아 적재(임베딩) 처리량이 크게 오른다. GPU가 없으면 CPU로 graceful 폴백
하므로 같은 이미지가 양쪽에서 동작한다. `device`를 명시하면 강제한다("cuda"/"cpu"/"mps").
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: encode 배치 크기 — GPU는 큰 배치에서 throughput 이득. CPU에서도 안전한 기본값.
_ENCODE_BATCH_SIZE = 64


def _resolve_device(explicit: str | None) -> str:
    """사용할 디바이스를 결정한다 — 명시값 우선, 없으면 CUDA→MPS→CPU 자동.

    torch는 lazy import(이미 sentence-transformers가 끌어옴). torch 미설치/탐지
    실패는 "cpu"로 graceful 폴백한다(가속은 보조 — 본 기능은 항상 동작).
    """
    if explicit:
        return explicit
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        # Apple Silicon(MPS) — 로컬 mac 개발 가속.
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
    except Exception as exc:  # noqa: BLE001 — 탐지 실패는 CPU 폴백(가속은 보조).
        logger.debug("디바이스 자동 탐지 실패 — cpu 폴백: %s", exc)
    return "cpu"


class SentenceTransformerEmbedder:
    """sentence-transformers 모델로 텍스트를 임베딩하는 Embedder 구현."""

    def __init__(self, model_name: str, device: str | None = None) -> None:
        # lazy import: 미설치 환경에서도 이 모듈 import·다른 테스트가 깨지지 않게
        # 의존을 생성자에서만 끌어온다. 미설치는 조용히 폴백하지 않고 명확히 알린다.
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "SentenceTransformerEmbedder를 쓰려면 sentence-transformers가 "
                "필요합니다(미설치). `pip install 'kms[ml]'` 또는 "
                "`pip install sentence-transformers`로 설치하세요 "
                "(무거운 ML 의존이라 기본 런타임 의존이 아닌 optional extra입니다)."
            ) from exc
        self._device = _resolve_device(device)
        logger.info("임베딩 모델 로드: %s (device=%s)", model_name, self._device)
        self._model = SentenceTransformer(model_name, device=self._device)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # encode는 numpy ndarray를 반환 — 도메인·다른 어댑터가 SDK 타입에 묶이지
        # 않도록 순수 list[list[float]]로 변환해 반환한다(Embedder 프로토콜 계약).
        # batch_size로 GPU throughput을 살리고, device는 모델 로드 시 고정.
        vectors = self._model.encode(
            texts, batch_size=_ENCODE_BATCH_SIZE, convert_to_numpy=True
        )
        return [[float(value) for value in vector] for vector in vectors]
