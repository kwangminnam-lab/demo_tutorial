"""비밀번호 로그인 인증 서비스 (ADR-005·011·017).

email+비밀번호를 PostgreSQL 계정(bcrypt 해시)과 대조해 서명 JWT 세션 토큰을
발급하고(`login`), 그 토큰을 검증해 도메인 `UserContext`(부서·access 레벨)로
변환한다(`authenticate`). 토큰 검증은 `JwtIdentityProvider`(서명·만료 검증)에
위임한다. 인증은 항상 활성 — 우회/익명 모드는 없다.
"""

from typing import Any, Protocol

from kms.adapters.auth.passwords import verify_password
from kms.adapters.auth.tokens import JwtCodec
from kms.domain.errors import AuthenticationError
from kms.domain.models import UserContext


class AccountStore(Protocol):
    """계정 조회 경계 — `AuthService`가 의존하는 최소 계약.

    구체 구현은 `AccountRepository`(PostgreSQL). 인터페이스에만 의존하므로
    테스트는 인메모리 더블을 주입할 수 있다(의존성 방향 안쪽, 구현 격리).
    조회된 계정 객체는 `email`·`password_hash` 속성을 갖는다(비번 로그인용).
    """

    def get_by_email(self, email: str) -> Any:
        """email로 계정을 조회한다 (없으면 None). 반환 객체는 password_hash를 가진다."""
        ...

    def to_user_context(self, account: Any) -> UserContext:
        """계정을 도메인 `UserContext`로 변환한다."""
        ...


class IdentityProvider(Protocol):
    """토큰을 검증해 사용자 식별자(email)를 반환하는 IdP 추상 경계.

    검증 실패 시 `AuthenticationError`를 던진다 (조용한 실패 금지).
    구체 구현(OIDC/SAML)은 `adapters` + 후속 phase.
    """

    def verify(self, token: str) -> str:
        """토큰을 검증하고 사용자 email을 반환한다. 실패 시 AuthenticationError."""
        ...


class JwtIdentityProvider:
    """서명 JWT 세션 토큰을 검증하는 IdP — **운영/실 serving 기본**(ADR-017).

    로그인(`AuthService.login`)이 발급한 서명 토큰만 통과시킨다. 서명·만료 검증은
    `JwtCodec`에 위임한다 — 서버 비밀키 없이는 토큰을 만들 수 없어 stub처럼 위조
    불가(보안 리뷰 V1 대응).
    """

    def __init__(self, codec: JwtCodec) -> None:
        self._codec = codec

    def verify(self, token: str) -> str:
        """JWT를 검증해 email을 반환한다. 실패는 AuthenticationError(codec이 던짐)."""
        return self._codec.verify(token)


class AuthService:
    """IdP 검증/비밀번호 로그인을 도메인 `UserContext`·세션 토큰으로 잇는 인증 유스케이스."""

    def __init__(
        self,
        provider: IdentityProvider,
        accounts: AccountStore,
        *,
        codec: JwtCodec | None = None,
    ) -> None:
        self._provider = provider
        self._accounts = accounts
        # codec은 비밀번호 로그인(JWT 발급)에만 필요 — 토큰 검증만 하는 경로엔 불요.
        self._codec = codec

    def authenticate(self, token: str) -> UserContext:
        """토큰 → email 검증 → 계정 조회 → `UserContext`.

        미등록 사용자면 `AuthenticationError`. 토큰 검증 실패는 provider가 던진다.
        """
        email = self._provider.verify(token)
        account = self._accounts.get_by_email(email)
        if account is None:
            raise AuthenticationError("등록되지 않은 사용자")
        return self._accounts.to_user_context(account)

    def login(self, email: str, password: str) -> str:
        """email+비밀번호를 검증하고 서명된 세션 토큰(JWT)을 발급한다.

        계정 부재·비번 불일치·비번 미설정 계정은 **구분하지 않고** 동일 에러로
        거부한다(계정 존재 여부·원인 누설 방지). codec 미주입이면 구성 에러.
        """
        if self._codec is None:
            raise RuntimeError("login에는 JwtCodec이 필요합니다 — 조립 시 codec을 주입하세요.")
        account = self._accounts.get_by_email(email)
        password_hash = getattr(account, "password_hash", None) if account else None
        if account is None or not verify_password(password, password_hash):
            raise AuthenticationError("이메일 또는 비밀번호가 올바르지 않습니다")
        return self._codec.issue(account.email)
