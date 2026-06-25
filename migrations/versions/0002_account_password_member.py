"""account password + role overhaul (ADR-017)

`users`에 `password_hash`(nullable) 추가. 역할 개편으로 기존 멤버(access_level=1,
구 임직원) 계정을 삭제한다 — 마스터가 멤버를 새로 추가하는 모델로 전환. 마스터
계정(access_level=2)은 유지하되 비밀번호는 앱 기동 시드가 채운다(마이그레이션에
시크릿/평문 비번 금지). 전진 전용.

Revision ID: 0002_account_password_member
Revises: 0001_create_users
Create Date: 2026-06-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_account_password_member"
down_revision: str | None = "0001_create_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    # 기존 멤버(구 임직원, access_level=1) 계정 제거 — 마스터가 새로 추가한다.
    op.execute("DELETE FROM users WHERE access_level = 1")


def downgrade() -> None:
    op.drop_column("users", "password_hash")
