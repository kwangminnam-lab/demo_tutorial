"""세션 토큰 — 서명된 JWT(HS256) 발급·검증 어댑터.

로그인 성공 시 `issue`로 사용자 email을 sub claim에 담아 만료(exp)·발급(iat)과 함께
HMAC 서명한다. 매 요청에서 `verify`가 서명·만료를 검증해 email을 복원한다 — 서버가
비밀키를 알아야만 만들 수 있으므로 stub 토큰처럼 위조할 수 없다(보안 리뷰 V1 대응).

시크릿(`secret`)은 에러 메시지·로그에 싣지 않는다. 검증 실패는 도메인
`AuthenticationError`로 변환해 호출자(deps)가 401로 매핑한다(조용한 실패 금지).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from kms.domain.errors import AuthenticationError

_ALGORITHM = "HS256"


class JwtCodec:
    """email ↔ 서명 JWT 변환기. 비밀키와 만료 정책을 보유한다."""

    def __init__(self, secret: str, *, expire_minutes: int = 720) -> None:
        if not secret:
            # 빈 시크릿은 위조 가능 — 조립 단계에서 명확히 거부한다.
            raise ValueError("JwtCodec에는 비어 있지 않은 secret이 필요합니다.")
        self._secret = secret
        self._expire = timedelta(minutes=expire_minutes)

    def issue(self, email: str, *, now: datetime | None = None) -> str:
        """email을 sub로 하는 서명 JWT를 발급한다(exp = now + expire_minutes)."""
        issued_at = now or datetime.now(UTC)
        payload = {
            "sub": email,
            "iat": issued_at,
            "exp": issued_at + self._expire,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> str:
        """JWT를 검증해 email(sub)을 반환한다. 실패는 AuthenticationError."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=[_ALGORITHM])
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("세션이 만료되었습니다") from exc
        except jwt.InvalidTokenError as exc:
            # 서명 불일치·형식 오류 등 — 토큰 값을 메시지에 넣지 않는다(유출 방지).
            raise AuthenticationError("유효하지 않은 토큰") from exc
        email = payload.get("sub")
        if not email or not isinstance(email, str):
            raise AuthenticationError("토큰에 사용자 식별자가 없음")
        return email
