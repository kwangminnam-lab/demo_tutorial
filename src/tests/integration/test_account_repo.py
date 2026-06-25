"""AccountRepository 통합 테스트 — 실 PostgreSQL에서 검증 (SQLite 제거).

계정 DB는 PostgreSQL 단일이다. 이 테스트는 `TEST_DATABASE_URL`(없으면 `DATABASE_URL`)
DSN의 PostgreSQL에 연결해 repo의 upsert·조회·도메인 변환을 검증한다. DSN 미설정
또는 서버 미도달이면 skip하므로 CI 기본(`pytest -m "not integration"`)에는 영향이
없다 — 로컬은 docker compose 의 postgres 기동 후 실행한다.

테이블은 매 테스트 함수마다 생성·삭제해 격리한다(트랜잭션 잔여 없음).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kms.adapters.db.models import Base
from kms.adapters.db.repository import AccountRepository
from kms.domain.access import AccessLevel

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn or not dsn.startswith("postgresql"):
        pytest.skip("PostgreSQL DSN 미설정(TEST_DATABASE_URL/DATABASE_URL) — 통합 테스트 skip")
    try:
        engine = create_engine(dsn)
        # 도달성 확인 — 미도달이면 skip(서버 없으면 의미 없음).
        with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001 — 연결 실패는 skip 사유.
        pytest.skip(f"PostgreSQL 미도달 — 통합 테스트 skip ({type(exc).__name__})")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    try:
        with maker() as db_session:
            yield db_session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_upsert_then_get_by_email_returns_same_user(session: Session) -> None:
    repo = AccountRepository(session)

    repo.upsert(email="a@corp.com", department="research", access_level=1)
    session.commit()

    found = repo.get_by_email("a@corp.com")
    assert found is not None
    assert found.email == "a@corp.com"
    assert found.department == "research"
    assert found.access_level == 1


def test_get_by_email_unknown_returns_none(session: Session) -> None:
    repo = AccountRepository(session)
    assert repo.get_by_email("missing@corp.com") is None


def test_upsert_updates_existing_account(session: Session) -> None:
    repo = AccountRepository(session)

    created = repo.upsert(email="b@corp.com", department="sales", access_level=1)
    updated = repo.upsert(email="b@corp.com", department="exec", access_level=2)
    session.commit()

    assert created.id == updated.id  # 동일 행 갱신 (멱등)
    assert updated.department == "exec"
    assert updated.access_level == 2


def test_to_user_context_maps_access_level_to_enum(session: Session) -> None:
    repo = AccountRepository(session)
    account = repo.upsert(email="ceo@corp.com", department="exec", access_level=2)

    ctx = repo.to_user_context(account)

    assert ctx.user_id == "ceo@corp.com"
    assert ctx.department == "exec"
    assert ctx.access_level is AccessLevel.사장
    assert ctx.access_level == 2
