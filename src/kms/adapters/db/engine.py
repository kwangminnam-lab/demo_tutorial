"""`Settings.database_url`로 SQLAlchemy engine·sessionmaker를 만드는 헬퍼.

DSN을 한 곳에서만 읽어 커넥션 생성을 격리한다. 호출부는 sessionmaker로
받은 세션을 컨텍스트 매니저(`with`)로 써서 확실히 닫는다.
"""

from sqlalchemy import Engine
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.orm import Session, sessionmaker

from kms.config.settings import Settings


def create_engine(settings: Settings) -> Engine:
    """`settings.database_url` DSN으로 engine을 생성한다."""
    return _sa_create_engine(settings.database_url)


def create_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """engine에 묶인 sessionmaker를 반환한다 (`with maker() as session:`)."""
    return sessionmaker(bind=engine)
