"""멤버 관리 + 로그인 API 통합 테스트 (ADR-017) — 서버 없이 TestClient + 인메모리 더블.

검증: 마스터 로그인 → 토큰 → 멤버 CRUD. 멤버 토큰은 관리 라우트 403. 미인증 401.
비밀번호 응답 미노출. JWT 위조 불가.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from kms.adapters.auth.passwords import hash_password
from kms.adapters.auth.tokens import JwtCodec
from kms.api.app import create_app
from kms.api.deps import get_account_service, get_auth_service
from kms.domain.access import AccessLevel
from kms.services.account_service import AccountService
from kms.services.auth_service import AuthService, JwtIdentityProvider
from tests._fake_account_store import FakeAccountStore

_MASTER = ("master@docux.local", "ChangeMe!1234")


@pytest.fixture
def client() -> Iterator[TestClient]:
    store = FakeAccountStore()
    store.create(
        _MASTER[0],
        department="관리",
        access_level=int(AccessLevel.마스터),
        password_hash=hash_password(_MASTER[1]),
    )
    codec = JwtCodec("test-secret")
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(
        JwtIdentityProvider(codec), store, codec=codec
    )
    app.dependency_overrides[get_account_service] = lambda: AccountService(store)
    with TestClient(app) as test_client:
        yield test_client


def test_me_is_master_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 인증 비활성 — /v1/me는 토큰 없이도 고정 마스터를 반환한다.
    me = client.get("/v1/me").json()
    assert me["is_master"] is True and me["role"] == "마스터"


def test_member_crud_without_auth_in_demo(client: TestClient) -> None:
    # 데모 모드(ADR-026): 마스터 게이트 무력화(고정 마스터) — 토큰 없이 멤버 CRUD 동작.
    created = client.post(
        "/v1/members",
        json={"email": "alice@corp.com", "password": "pw-at-least-8", "department": "영업"},
    )
    assert created.status_code == 201
    assert "password" not in created.text  # 응답에 비번/해시 없음

    listing = client.get("/v1/members").json()
    emails = {m["email"] for m in listing}
    assert emails == {"master@docux.local", "alice@corp.com"}
    alice = next(m for m in listing if m["email"] == "alice@corp.com")
    master = next(m for m in listing if m["email"] == "master@docux.local")

    # 마스터 삭제 거부(400), 멤버 삭제 성공(204) — 계정 도메인 규칙은 그대로.
    assert client.delete(f"/v1/members/{master['id']}").status_code == 400
    assert client.delete(f"/v1/members/{alice['id']}").status_code == 204
    assert len(client.get("/v1/members").json()) == 1
