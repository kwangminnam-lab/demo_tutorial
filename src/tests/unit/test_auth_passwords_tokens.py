"""인증 어댑터 단위 테스트 — bcrypt 비밀번호 + 서명 JWT (ADR-017)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kms.adapters.auth.passwords import hash_password, verify_password
from kms.adapters.auth.tokens import JwtCodec
from kms.domain.errors import AuthenticationError


def test_hash_then_verify_roundtrip() -> None:
    h = hash_password("S3cret!pw")
    assert h != "S3cret!pw"  # 평문이 그대로 저장되지 않는다
    assert verify_password("S3cret!pw", h) is True
    assert verify_password("wrong", h) is False


def test_verify_rejects_missing_or_broken_hash() -> None:
    assert verify_password("x", None) is False  # 비번 미설정 계정 → 로그인 불가
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-bcrypt-hash") is False  # 손상 해시 → 거부


def test_hash_is_salted_unique() -> None:
    # 같은 비번도 솔트가 달라 해시가 다르다(레인보우 테이블 방어).
    assert hash_password("same") != hash_password("same")


def test_jwt_issue_then_verify() -> None:
    codec = JwtCodec("secret-key")
    token = codec.issue("user@corp.com")
    assert codec.verify(token) == "user@corp.com"


def test_jwt_rejects_tampered_or_wrong_secret() -> None:
    token = JwtCodec("secret-a").issue("user@corp.com")
    with pytest.raises(AuthenticationError):
        JwtCodec("secret-b").verify(token)  # 다른 키 서명 → 위조 불가
    with pytest.raises(AuthenticationError):
        JwtCodec("secret-a").verify(token + "x")  # 변조 → 거부


def test_jwt_rejects_expired() -> None:
    codec = JwtCodec("secret-key", expire_minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=20)
    token = codec.issue("user@corp.com", now=past)  # 이미 만료된 토큰
    with pytest.raises(AuthenticationError):
        codec.verify(token)


def test_empty_secret_rejected() -> None:
    with pytest.raises(ValueError):
        JwtCodec("")
