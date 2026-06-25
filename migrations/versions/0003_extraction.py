"""field extraction tables (ADR-024 phase 1)

금융 문서 IDP(필드추출) phase 1: 추출 스키마(필드 템플릿)와 추출 결과(값+근거
라인+bbox)를 영속하는 두 테이블을 추가한다. `fields`는 가변 필드 집합이라 JSONB로
저장한다. 기존 테이블·공개 인터페이스는 건드리지 않는다(가산 변경). 전진 전용.

Revision ID: 0003_extraction
Revises: 0002_account_password_member
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_extraction"
down_revision: str | None = "0002_account_password_member"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_schema",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=True),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("auto_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_extraction_schema_name"), "extraction_schema", ["name"], unique=False
    )

    op.create_table(
        "extraction_result",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("doc_id", sa.String(), nullable=False),
        sa.Column("schema_id", sa.Integer(), nullable=True),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_extraction_result_doc_id"),
        "extraction_result",
        ["doc_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_extraction_result_doc_id"), table_name="extraction_result")
    op.drop_table("extraction_result")
    op.drop_index(op.f("ix_extraction_schema_name"), table_name="extraction_schema")
    op.drop_table("extraction_schema")
