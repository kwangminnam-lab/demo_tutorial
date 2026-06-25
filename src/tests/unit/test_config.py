import pytest
from pydantic import ValidationError

from kms.config import Settings, get_settings


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/kms")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")


def test_settings_reads_injected_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("VECTOR_BACKEND", "postgres")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cuda")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.database_url == "postgresql://localhost/kms"
    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password == "secret"
    assert settings.vector_backend == "postgres"
    assert settings.embedding_device == "cuda"


def test_llm_model_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # 코드 기본값(`_env_file=None`이라 .env 무시) — settings.py의 정의와 일치해야 한다.
    assert settings.llm_model_path == "./local_models/gemma_model_e4b_mlx_q4"


def test_search_backend_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.search_backend == "memory"
    assert settings.vector_backend == "memory"
    # 새 임베딩 디바이스 필드는 기본 None(자동 선택) — conftest가 건드리지 않는다.
    assert settings.embedding_device is None


def test_missing_required_fields_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    get_settings.cache_clear()

    assert get_settings() is get_settings()
