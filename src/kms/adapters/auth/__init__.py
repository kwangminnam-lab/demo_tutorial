"""인증 어댑터 — 비밀번호 해싱(bcrypt) + 세션 토큰(서명 JWT).

도메인·서비스는 함수/클래스 인터페이스에만 의존하고, 구체 라이브러리(bcrypt·pyjwt)는
이 패키지에 격리한다(ADR-007). 시크릿(JWT secret·해시)은 로그·에러에 싣지 않는다.
"""

from kms.adapters.auth.passwords import hash_password, verify_password
from kms.adapters.auth.tokens import JwtCodec

__all__ = ["hash_password", "verify_password", "JwtCodec"]
