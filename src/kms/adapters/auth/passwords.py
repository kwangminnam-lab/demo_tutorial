"""비밀번호 해싱·검증 — bcrypt 어댑터.

평문 비밀번호는 저장하지 않는다. 생성 시 bcrypt 해시(솔트 내장)만 DB에 넣고,
로그인 시 상수 시간 비교로 검증한다. bcrypt는 입력 72바이트 초과분을 잘라내므로
호출자(서비스)가 비밀번호 길이 정책을 둔다.
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """평문 비밀번호를 bcrypt 해시(문자열)로 만든다."""
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return digest.decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    """평문이 해시와 일치하는지 검증한다.

    `password_hash`가 없으면(비번 미설정 계정) False — 로그인 불가. 해시 형식이
    깨졌으면 예외를 삼키고 False(조용한 실패 아님 — 검증 실패로 명확히 처리).
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 손상된 해시 문자열 — 검증 불가이므로 거부(예외 메시지에 해시 미노출).
        return False
