"""Alembic 마이그레이션 환경.

DSN은 `Settings.database_url`(환경 변수/.env)에서 읽어 코드/ini에 시크릿을
박지 않는다. `target_metadata`는 ORM `Base.metadata`로, autogenerate·비교의
기준이 된다.
"""

import os
from logging.config import fileConfig

from alembic import context
from pydantic import ValidationError
from sqlalchemy import engine_from_config, pool

from kms.adapters.db.models import Base
from kms.config.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """마이그레이션 DSN. 기본은 Settings.database_url.

    마이그레이션은 DB만 있으면 충분하므로, 전체 Settings(neo4j 등 필수)가
    구성되지 않은 환경에서는 DATABASE_URL 환경 변수로 대체한다 — 둘 다
    같은 출처(DATABASE_URL)를 읽으므로 값은 일치한다.
    """
    try:
        return get_settings().database_url
    except ValidationError:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise
        return url


# DSN을 주입 (ini의 sqlalchemy.url을 비워 둠 — 시크릿 분리).
config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """엔진 없이 URL만으로 SQL을 발행하는 오프라인 모드."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """engine을 만들어 커넥션에 마이그레이션을 적용하는 온라인 모드."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
