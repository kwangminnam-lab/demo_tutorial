"""Settings.cors_origins 파서 단위 테스트 (ADR-019).

콤마 구분 문자열을 정규화된 오리진 리스트로 바꾸는 순수 프로퍼티를 잠근다 —
공백 트리밍·빈 항목 제거·미설정 시 빈 리스트.
"""

from __future__ import annotations

from kms.config.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql://test",
        "neo4j_uri": "bolt://test",
        "neo4j_user": "u",
        "neo4j_password": "p",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg, arg-type]


def test_unset_yields_empty_list() -> None:
    assert _settings().cors_origins == []


def test_single_origin() -> None:
    s = _settings(cors_allow_origins="https://user.github.io")
    assert s.cors_origins == ["https://user.github.io"]


def test_multiple_origins_trimmed() -> None:
    s = _settings(
        cors_allow_origins=" https://a.io , https://b.io ,, https://c.io "
    )
    assert s.cors_origins == ["https://a.io", "https://b.io", "https://c.io"]
