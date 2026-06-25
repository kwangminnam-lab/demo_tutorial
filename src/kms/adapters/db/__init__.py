"""PostgreSQL 관계형 영속 계층 (ADR-003).

SSO 계정·access 레벨·부서를 저장한다. ORM 모델은 이 패키지 경계 밖으로
새지 않는다 — services/api는 `AccountRepository.to_user_context`로 받는
도메인 `UserContext`에만 의존한다.
"""

from kms.adapters.db.engine import create_engine, create_sessionmaker
from kms.adapters.db.models import Base, UserAccount
from kms.adapters.db.repository import AccountRepository

__all__ = [
    "AccountRepository",
    "Base",
    "UserAccount",
    "create_engine",
    "create_sessionmaker",
]
