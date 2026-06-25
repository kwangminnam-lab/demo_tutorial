"""멤버 관리 유스케이스 (ADR-017) — 마스터가 멤버 계정을 추가·조회·삭제한다.

마스터 전용이라는 **인가**는 api 경계(`require_master`)가 강제한다. 이 서비스는
도메인 규칙만 본다: 이메일 형식·중복 거부, 비밀번호 길이 정책, 마스터 계정 삭제
보호. 비밀번호는 평문으로 보관하지 않고 bcrypt 해시만 저장한다(passwords 어댑터).
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel

from kms.adapters.auth.passwords import hash_password
from kms.domain.access import AccessLevel
from kms.domain.errors import MemberValidationError

# 최소 비밀번호 길이 — bcrypt 72바이트 상한 안에서 약한 비번을 거른다.
_MIN_PASSWORD_LEN = 8
# 단순 이메일 형식 검사(로컬@도메인.tld). 엄밀 RFC 검증 대신 명백한 오타만 거른다.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberView(BaseModel):
    """멤버 목록·생성 응답 — **비밀번호 해시는 절대 포함하지 않는다**."""

    id: int
    email: str
    department: str
    access_level: int
    is_master: bool


class AccountRepo(Protocol):
    """계정 저장소 경계 — account_service가 의존하는 최소 계약(repository 구현)."""

    def get_by_email(self, email: str) -> Any: ...
    def get_by_id(self, account_id: int) -> Any: ...
    def list_all(self) -> list[Any]: ...
    def create(
        self, email: str, *, department: str, access_level: int, password_hash: str | None
    ) -> Any: ...
    def delete(self, account: Any) -> None: ...


class AccountService:
    """마스터의 멤버 계정 관리 유스케이스. 세션 기반 repo를 주입받는다."""

    def __init__(self, accounts: AccountRepo) -> None:
        self._accounts = accounts

    def add_member(self, email: str, password: str, department: str) -> MemberView:
        """멤버(access=멤버) 계정을 추가한다. 형식·중복·약한 비번은 거부한다."""
        email = email.strip().lower()
        department = department.strip()
        if not _EMAIL_RE.match(email):
            raise MemberValidationError("이메일 형식이 올바르지 않습니다")
        if len(password) < _MIN_PASSWORD_LEN:
            raise MemberValidationError(
                f"비밀번호는 최소 {_MIN_PASSWORD_LEN}자 이상이어야 합니다"
            )
        if not department:
            raise MemberValidationError("부서를 입력하세요")
        if self._accounts.get_by_email(email) is not None:
            raise MemberValidationError("이미 존재하는 이메일입니다")
        account = self._accounts.create(
            email,
            department=department,
            access_level=int(AccessLevel.멤버),
            password_hash=hash_password(password),
        )
        return _to_view(account)

    def list_members(self) -> list[MemberView]:
        """모든 계정을 비밀번호 해시 없는 뷰로 반환한다(마스터 포함)."""
        return [_to_view(account) for account in self._accounts.list_all()]

    def delete_member(self, account_id: int) -> None:
        """멤버를 삭제한다. 미존재는 거부, **마스터 계정은 삭제할 수 없다**(잠금 방지)."""
        account = self._accounts.get_by_id(account_id)
        if account is None:
            raise MemberValidationError("존재하지 않는 계정입니다")
        if int(account.access_level) == int(AccessLevel.마스터):
            raise MemberValidationError("마스터 계정은 삭제할 수 없습니다")
        self._accounts.delete(account)


def _to_view(account: Any) -> MemberView:
    level = int(account.access_level)
    return MemberView(
        id=int(account.id),
        email=account.email,
        department=account.department,
        access_level=level,
        is_master=level == int(AccessLevel.마스터),
    )
