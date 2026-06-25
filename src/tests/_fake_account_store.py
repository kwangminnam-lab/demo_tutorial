"""테스트용 인메모리 계정 저장소 더블 — `AccountRepository` 계약을 DB 없이 만족.

`AccountStore`(auth_service: get_by_email·to_user_context)와 `AccountRepo`
(account_service: get_by_email·get_by_id·list_all·create·delete)를 모두 만족한다.
실 PostgreSQL repo 동작은 `test_account_repo.py`(integration)가 검증하고, 여기서는
인증·멤버 관리 로직을 서버 없이 돌리기 위한 더블이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from kms.domain.access import AccessLevel
from kms.domain.models import UserContext


@dataclass
class FakeAccount:
    """계정 행 더블 — email·password_hash를 가진다(UserAccount 대용)."""

    id: int
    email: str
    department: str
    access_level: int
    password_hash: str | None = None


class FakeAccountStore:
    """dict 기반 인메모리 계정 저장소. repository와 같은 메서드를 노출한다."""

    def __init__(self) -> None:
        self._rows: dict[int, FakeAccount] = {}
        self._next_id = 1

    def get_by_email(self, email: str) -> FakeAccount | None:
        return next((a for a in self._rows.values() if a.email == email), None)

    def get_by_id(self, account_id: int) -> FakeAccount | None:
        return self._rows.get(account_id)

    def list_all(self) -> list[FakeAccount]:
        return sorted(self._rows.values(), key=lambda a: a.email)

    def create(
        self,
        email: str,
        *,
        department: str,
        access_level: int,
        password_hash: str | None,
    ) -> FakeAccount:
        account = FakeAccount(
            id=self._next_id,
            email=email,
            department=department,
            access_level=access_level,
            password_hash=password_hash,
        )
        self._rows[self._next_id] = account
        self._next_id += 1
        return account

    def delete(self, account: FakeAccount) -> None:
        self._rows.pop(account.id, None)

    def to_user_context(self, account: FakeAccount) -> UserContext:
        return UserContext(
            user_id=account.email,
            department=account.department,
            access_level=AccessLevel(account.access_level),
        )
