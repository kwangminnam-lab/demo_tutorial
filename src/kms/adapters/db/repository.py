"""계정 영속 + 도메인 변환을 담당하는 repository.

ORM(`UserAccount`)을 경계 밖으로 내보내지 않고 `to_user_context`로
도메인 `UserContext`만 노출한다 — services/api가 SQLAlchemy에 의존하지
않게 한다 (의존성 방향 안쪽).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from kms.adapters.db.models import UserAccount
from kms.domain.access import AccessLevel
from kms.domain.models import UserContext


class AccountRepository:
    """SSO 계정 CRUD + 도메인 변환. 세션은 생성자로 주입한다."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, email: str) -> UserAccount | None:
        """email로 계정을 조회한다 (없으면 None)."""
        return self._session.scalar(
            select(UserAccount).where(UserAccount.email == email)
        )

    def upsert(self, email: str, department: str, access_level: int) -> UserAccount:
        """email 기준으로 계정을 갱신하거나 신규 생성한다 (멱등)."""
        account = self.get_by_email(email)
        if account is None:
            account = UserAccount(
                email=email, department=department, access_level=access_level
            )
            self._session.add(account)
        else:
            account.department = department
            account.access_level = access_level
        self._session.flush()
        return account

    def create(
        self,
        email: str,
        *,
        department: str,
        access_level: int,
        password_hash: str | None,
    ) -> UserAccount:
        """신규 계정을 만든다(멤버 추가용). email 중복은 UNIQUE 제약으로 거부된다.

        호출자(서비스)가 사전 중복 검사를 하지만, 경합 시 DB 제약이 최종 방어다.
        평문 비밀번호는 받지 않는다 — 호출자가 bcrypt 해시를 넘긴다.
        """
        account = UserAccount(
            email=email,
            department=department,
            access_level=access_level,
            password_hash=password_hash,
        )
        self._session.add(account)
        self._session.flush()
        return account

    def list_all(self) -> list[UserAccount]:
        """모든 계정을 email 오름차순으로 반환한다 (멤버 목록 화면용)."""
        return list(
            self._session.scalars(select(UserAccount).order_by(UserAccount.email))
        )

    def get_by_id(self, account_id: int) -> UserAccount | None:
        """기본키로 계정을 조회한다 (없으면 None)."""
        return self._session.get(UserAccount, account_id)

    def delete(self, account: UserAccount) -> None:
        """계정을 삭제한다 (멤버 삭제용). 마스터 보호는 호출자(서비스)가 강제한다."""
        self._session.delete(account)
        self._session.flush()

    def to_user_context(self, account: UserAccount) -> UserContext:
        """ORM 계정을 도메인 `UserContext`로 변환 (access_level int → AccessLevel)."""
        return UserContext(
            user_id=account.email,
            department=account.department,
            access_level=AccessLevel(account.access_level),
        )
