"""AuthService.login + AccountService(멤버 관리) 단위 테스트 (ADR-017).

인메모리 `FakeAccountStore`로 DB 없이 로그인·멤버 추가/삭제 규칙을 검증한다.
"""

from __future__ import annotations

import pytest

from kms.adapters.auth.passwords import hash_password
from kms.adapters.auth.tokens import JwtCodec
from kms.domain.access import AccessLevel
from kms.domain.errors import AuthenticationError, MemberValidationError
from kms.services.account_service import AccountService
from kms.services.auth_service import AuthService, JwtIdentityProvider
from tests._fake_account_store import FakeAccountStore


def _store_with_master() -> FakeAccountStore:
    store = FakeAccountStore()
    store.create(
        "master@docux.local",
        department="관리",
        access_level=int(AccessLevel.마스터),
        password_hash=hash_password("ChangeMe!1234"),
    )
    return store


def _auth(store: FakeAccountStore) -> AuthService:
    codec = JwtCodec("test-secret")
    return AuthService(JwtIdentityProvider(codec), store, codec=codec)


# ── login ──────────────────────────────────────────────────────────────
def test_login_success_issues_verifiable_token() -> None:
    store = _store_with_master()
    auth = _auth(store)
    token = auth.login("master@docux.local", "ChangeMe!1234")
    user = auth.authenticate(token)  # 발급 토큰이 그대로 인증된다
    assert user.user_id == "master@docux.local"
    assert user.access_level.is_master is True


def test_login_wrong_password_or_unknown_email_rejected() -> None:
    auth = _auth(_store_with_master())
    with pytest.raises(AuthenticationError):
        auth.login("master@docux.local", "wrong")
    with pytest.raises(AuthenticationError):
        auth.login("ghost@docux.local", "whatever")  # 미존재 — 동일 에러(누설 방지)


# ── member management ──────────────────────────────────────────────────
def test_add_member_creates_member_level_account() -> None:
    store = _store_with_master()
    svc = AccountService(store)
    view = svc.add_member("alice@corp.com", "pw-at-least-8", "영업")
    assert view.is_master is False
    assert view.access_level == int(AccessLevel.멤버)
    # 추가된 멤버로 로그인 가능
    assert _auth(store).login("alice@corp.com", "pw-at-least-8")


def test_add_member_rejects_dup_weak_and_bad_email() -> None:
    svc = AccountService(_store_with_master())
    svc.add_member("alice@corp.com", "pw-at-least-8", "영업")
    with pytest.raises(MemberValidationError):
        svc.add_member("alice@corp.com", "pw-at-least-8", "영업")  # 중복
    with pytest.raises(MemberValidationError):
        svc.add_member("bob@corp.com", "short", "영업")  # 약한 비번
    with pytest.raises(MemberValidationError):
        svc.add_member("not-an-email", "pw-at-least-8", "영업")  # 형식


def test_list_members_excludes_password_hash() -> None:
    store = _store_with_master()
    AccountService(store).add_member("alice@corp.com", "pw-at-least-8", "영업")
    views = AccountService(store).list_members()
    assert {v.email for v in views} == {"master@docux.local", "alice@corp.com"}
    # MemberView 스키마에 password_hash 필드 자체가 없다.
    assert "password_hash" not in views[0].model_dump()


def test_delete_member_but_not_master() -> None:
    store = _store_with_master()
    svc = AccountService(store)
    member = svc.add_member("alice@corp.com", "pw-at-least-8", "영업")
    master_id = store.get_by_email("master@docux.local").id  # type: ignore[union-attr]

    svc.delete_member(member.id)  # 멤버 삭제 OK
    assert store.get_by_id(member.id) is None
    with pytest.raises(MemberValidationError):
        svc.delete_member(master_id)  # 마스터 삭제 거부(잠금 방지)
    with pytest.raises(MemberValidationError):
        svc.delete_member(99999)  # 미존재
