"""테스트용 JWT 세션 토큰 헬퍼 — 옛 `stub:<email>` 토큰을 대체한다.

인증은 항상 활성(우회/익명 모드 없음)이므로, 테스트도 운영과 동일하게 서명 JWT로
인증한다. 고정 시크릿의 `JwtCodec`으로 토큰을 발급(`issue_token`)하고, 같은 codec을
`JwtIdentityProvider`에 주입해 검증한다. 모듈명이 `_` 접두라 pytest가 수집하지 않는다.
"""

from __future__ import annotations

from kms.adapters.auth.tokens import JwtCodec

# 32바이트 이상 — InsecureKeyLengthWarning 회피(테스트 전용 시크릿, 코드 외부 노출 무관).
TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"

# 모든 테스트가 공유하는 codec — 발급(issue)과 검증(verify)에 같은 인스턴스를 쓴다.
TEST_CODEC = JwtCodec(TEST_JWT_SECRET, expire_minutes=720)


def issue_token(email: str) -> str:
    """email로 서명된 테스트 세션 토큰(JWT)을 발급한다."""
    return TEST_CODEC.issue(email)
