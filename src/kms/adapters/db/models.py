"""SSO 계정 ORM 모델 (SQLAlchemy 2.x `Mapped` 스타일).

`access_level`은 `AccessLevel`의 정수 값을 저장한다 (도메인 enum을 DB에
직접 묶지 않고, 변환은 repository에서 한다). 이 모델은 adapters/db 경계
밖으로 노출하지 않는다.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """선언적 매핑의 베이스 — alembic이 메타데이터를 읽는다."""


class UserAccount(Base):
    """사용자 계정. 부서(부서 가중)·access 레벨(권한 하드 필터)·비밀번호 해시를 영속한다."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    department: Mapped[str] = mapped_column(String, nullable=False)
    # AccessLevel의 정수 값 (1=멤버, 2=마스터). 변환은 repository에서.
    access_level: Mapped[int] = mapped_column(Integer, nullable=False)
    # bcrypt 비밀번호 해시. nullable — 레거시(비번 없는) 계정 허용(로그인 불가).
    # 평문 비밀번호는 절대 저장하지 않는다(해시만).
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class ExtractionSchemaRow(Base):
    """추출 스키마(필드 목록 템플릿) — 문서종류별 재사용 (ADR-024 phase 1).

    `fields`는 SchemaField 목록을 JSONB로 영속한다(가변 필드 집합이라 정규화보다
    문서형 저장이 단순). 도메인 변환은 repository에서 한다.
    """

    __tablename__ = "extraction_schema"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    doc_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # [{key, type, description, required}, ...]
    fields: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    auto_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExtractionResultRow(Base):
    """추출 결과(문서 1건 × 스키마 1개) — 값+근거 라인+bbox+신뢰도 (ADR-024 phase 1).

    `fields`는 ExtractedField 목록을 JSONB로 영속한다. `doc_id`는 원문(kms_files)과
    연결하는 콘텐츠 해시다(FK 제약은 두지 않음 — kms_files는 raw SQL 테이블).
    """

    __tablename__ = "extraction_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    schema_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{key, value, page, bbox, evidence_line_ids, source, confidence, needs_review}, ...]
    fields: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
