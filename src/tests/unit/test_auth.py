"""데모 모드(ADR-026) 인증 배선 단위 테스트.

로그인은 제거됐고 인증은 비활성이다. `get_current_user`는 토큰 검증 없이 항상
전체접근(마스터) 컨텍스트를 반환하고, `require_master`·`require_ingest_admin`는
그 컨텍스트로 게이트를 통과시킨다. 옛 401/토큰 검증·`/auth/login` 라우트는 사라졌다.
"""

from fastapi.testclient import TestClient

from kms.api.app import create_app
from kms.api.deps import (
    get_current_user,
    require_ingest_admin,
    require_master,
)
from kms.domain.access import AccessLevel


def test_get_current_user_returns_master_without_token() -> None:
    # Authorization 헤더 없이도 401이 아니라 전체접근 컨텍스트를 반환한다.
    ctx = get_current_user()

    assert ctx.access_level is AccessLevel.마스터
    assert ctx.access_level.is_master is True
    assert ctx.user_id == "demo"


def test_master_context_can_access_every_level() -> None:
    # 최고 등급이라 access 하드필터(<=)가 모든 문서 레벨을 통과시킨다.
    ctx = get_current_user()

    assert ctx.access_level.can_access(AccessLevel.멤버) is True
    assert ctx.access_level.can_access(AccessLevel.마스터) is True


def test_require_master_passes_demo_context() -> None:
    # 게이팅 무력화 — 마스터 컨텍스트라 403 없이 통과한다.
    assert require_master(user=get_current_user()).access_level.is_master is True


def test_require_ingest_admin_passes_demo_context() -> None:
    assert require_ingest_admin(user=get_current_user()).access_level.is_master is True


def test_login_route_removed_returns_404() -> None:
    # 로그인 개념 삭제 — /v1/auth/login 라우트가 더는 없다.
    client = TestClient(create_app())

    resp = client.post("/v1/auth/login", json={"email": "x@y.z", "password": "pw"})

    assert resp.status_code == 404
