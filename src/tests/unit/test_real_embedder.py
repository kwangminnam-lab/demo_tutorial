"""SentenceTransformerEmbedder(실 임베더) 단위 테스트 (step 1).

무거운 ML 의존(sentence-transformers·torch)은 미설치 환경이 정상이므로, 실모델을
요구하는 테스트는 `importorskip`으로 건너뛴다. 어댑터 모듈 import·factory 연결처럼
의존 없이 검증 가능한 lazy import 보호는 미설치 환경에서도 단언한다.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest

from kms.config.settings import Settings
from kms.factory import build_embedder


def _settings(**overrides: object) -> Settings:
    """필수 필드를 채운 테스트용 Settings (env 비의존)."""
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg, arg-type]


def test_adapter_module_imports_without_requiring_sentence_transformers() -> None:
    # lazy import 보호: 어댑터 모듈 import만으로 sentence_transformers를 요구하지
    # 않아야 한다(미설치 환경에서도 이 import가 성공해야 한다).
    module = importlib.import_module("kms.adapters.vectorstore.sentence_transformer")
    assert hasattr(module, "SentenceTransformerEmbedder")


def test_factory_sentence_transformers_errors_clearly_when_uninstalled() -> None:
    # factory가 backend 이름으로 실 임베더를 만들되, 미설치면 사용 시점(생성자)에
    # 설치 안내가 담긴 명확한 에러를 낸다(조용한 폴백 금지).
    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("sentence_transformers 설치됨 — 무거운 모델 로드는 skip")
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        build_embedder(_settings(embedder_backend="sentence_transformers"))


def test_real_embedder_dimension_and_determinism() -> None:
    # 설치된 환경에서만: 작은 텍스트 임베딩의 차원·결정성(같은 입력 같은 벡터) 확인.
    pytest.importorskip("sentence_transformers")
    from kms.adapters.vectorstore.sentence_transformer import (
        SentenceTransformerEmbedder,
    )

    embedder = SentenceTransformerEmbedder(_settings().embedding_model_name)
    vectors = embedder.embed(["안녕하세요", "hello world"])

    assert len(vectors) == 2
    dim = len(vectors[0])
    assert dim > 0
    assert len(vectors[1]) == dim
    assert all(isinstance(value, float) for value in vectors[0])

    # 결정성: 같은 입력은 같은 벡터.
    again = embedder.embed(["안녕하세요"])
    assert again[0] == vectors[0]
