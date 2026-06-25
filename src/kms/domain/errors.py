"""도메인 에러 — 시스템 에러와 구분되는, 비즈니스 규칙 위반/거부.

이 계층은 외부 의존이 없다. api 경계가 이 에러들을 HTTP 상태로 변환한다
(예: `AuthenticationError` → 401). 조용히 삼키지 않고 명확히 전파한다.
"""


class AuthenticationError(Exception):
    """인증 실패 — 토큰 검증 실패 또는 미등록 사용자.

    메시지에 토큰/시크릿을 넣지 마라 (로그·응답 유출 방지).
    """


class AuthorizationError(Exception):
    """인가 실패 — 인증은 됐으나 요청 자원에 대한 권한이 없음(접근 거부).

    권한 인지 하드 필터 위반(예: 권한 밖 문서 비교 시도)에 쓴다. api 경계가
    이 에러를 HTTP 403으로 변환한다. 메시지에 문서 내용/시크릿을 넣지 마라.
    """


class MetadataValidationError(Exception):
    """적재 메타데이터 검증 실패 — 필수 필드(`source`·`access`) 누락·형식 오류.

    적재 거부 사유(도메인 에러)다. 색인 전에 막아 출처·권한 필터가 불가능한
    자료가 들어오지 않게 한다 (CONVENTIONS 메타데이터 스키마 검증).
    """


class MemberValidationError(Exception):
    """멤버 추가/삭제 규칙 위반 — 이메일 형식·중복, 약한 비밀번호, 마스터 삭제 시도 등.

    마스터의 멤버 관리(ADR-017) 거부 사유다. api 경계가 HTTP 400으로 변환한다.
    메시지에 비밀번호/해시를 넣지 마라.
    """
