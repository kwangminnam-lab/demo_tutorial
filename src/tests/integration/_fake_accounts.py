"""API 통합 테스트용 인메모리 계정 repository 더블 (DB 서버 불요).

`AuthService`가 계정 경계에 요구하는 최소 계약(`get_by_email`·`to_user_context`)만
구현한다. 실 `AccountRepository`(PostgreSQL)의 영속·SQL 동작은 별도 통합 테스트
(`test_account_repo.py`, Postgres 필요)가 검증한다 — 여기서는 API 권한·인증 흐름만
서버 없이 돌리기 위한 더블이다(SQLite 제거 대체).
"""

from __future__ import annotations

from kms.domain.access import AccessLevel
from kms.domain.models import UserContext


class FakeAccountRepository:
    """email → UserContext 매핑만 보관하는 인메모리 계정 더블."""

    def __init__(self) -> None:
        self._by_email: dict[str, UserContext] = {}

    def upsert(self, email: str, department: str, access_level: int) -> UserContext:
        ctx = UserContext(
            user_id=email,
            department=department,
            access_level=AccessLevel(access_level),
        )
        self._by_email[email] = ctx
        return ctx

    def get_by_email(self, email: str) -> UserContext | None:
        return self._by_email.get(email)

    def to_user_context(self, account: UserContext) -> UserContext:
        # 더블은 계정 자체가 이미 UserContext다(ORM 변환 없음).
        return account
