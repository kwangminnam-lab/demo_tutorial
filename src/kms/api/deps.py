"""FastAPI 인증 의존성 — 진입 경계는 얇게 유지한다.

검증 로직은 `auth_service`에 있다. 여기서는 헤더 파싱과 도메인
`AuthenticationError` → `HTTPException(401)` 변환만 한다 (조용한 실패 금지).
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, HTTPException, status

from kms.adapters.llm.router import LLMRouter
from kms.domain.access import AccessLevel
from kms.domain.models import DocumentMetadata, UserContext
from kms.services.account_service import AccountService
from kms.services.auth_service import AuthService
from kms.services.diff_service import DiffService
from kms.services.extraction_service import ExtractionService
from kms.services.health import HealthService
from kms.services.ingest_jobs import IngestJobStore
from kms.services.ingestion_service import IngestionService
from kms.services.rag_service import RAGService
from kms.services.search_service import SearchService

# diff 라우트가 doc_id를 (파일 경로, 메타데이터)로 해소하는 콜러블 계약.
# 미등록 doc_id면 `KeyError`를 던진다(라우트가 404로 변환). 구체 구현(적재 이력
# 기반 조회 등)은 앱 조립 시 주입한다 — 진입 경계는 해소 방법을 모른다.
DocumentResolver = Callable[[str], tuple[Path, DocumentMetadata]]

# 적재 관리자 게이트(ADR-005·step 6): 상세 RBAC는 후속이므로 access 레벨로 단순화한다.
# 사장(최상위 레벨) 미만은 적재를 거부한다.
_INGEST_MIN_ACCESS = AccessLevel.사장


def get_auth_service() -> AuthService:
    """`AuthService`를 제공하는 의존성 placeholder.

    실제 인스턴스(provider + DB 세션 기반 repository + JwtCodec)는 앱 조립 시점에
    `app.dependency_overrides`로 주입한다. 미주입 상태로 호출되면 명확히
    실패시킨다 (조용한 기본값 금지).
    """
    raise RuntimeError(
        "get_auth_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_account_service() -> AccountService:
    """`AccountService`(멤버 관리)를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_account_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_search_service() -> SearchService:
    """`SearchService`를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_search_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_ingestion_service() -> IngestionService:
    """`IngestionService`를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_ingestion_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


# 비동기 적재(202+폴링)용 프로세스 전역 작업 store — 설정 비의존 in-memory 상태라
# placeholder가 아니라 실 싱글톤으로 둔다(앱 조립 불요). 테스트는 override로 격리한다.
_ingest_job_store = IngestJobStore()


def get_ingest_job_store() -> IngestJobStore:
    """프로세스 전역 적재 작업 store를 제공하는 의존성."""
    return _ingest_job_store


def get_rag_service() -> RAGService:
    """`RAGService`를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_rag_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_diff_service() -> DiffService:
    """`DiffService`를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_diff_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_extraction_service() -> ExtractionService:
    """`ExtractionService`(필드추출)를 제공하는 의존성 placeholder.

    DB 세션에 묶인 repo가 필요해 계정 서비스처럼 composition root(serve_api)에서
    요청별로 주입한다. 미주입 호출은 명확히 실패한다(조용한 기본값 금지)."""
    raise RuntimeError(
        "get_extraction_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_health_service() -> HealthService:
    """`HealthService`를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_health_service가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_llm_router() -> LLMRouter:
    """멀티 프로바이더 LLM 라우터 placeholder (앱 조립 시 override).

    요청별 provider/api_key를 받아 적절한 LLMClient를 만들어 RAGService에 넘긴다.
    기본 fallback은 로컬 Gemma(OpenAI 호환 서버).
    """
    raise RuntimeError(
        "get_llm_router가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


def get_document_resolver() -> DocumentResolver:
    """doc_id → (경로, 메타) 해소기를 제공하는 의존성 placeholder (앱 조립 시 override)."""
    raise RuntimeError(
        "get_document_resolver가 구성되지 않았습니다 — 앱 조립 시 dependency_overrides로 주입하세요."
    )


# 데모 고정 컨텍스트(ADR-026): 최고 권한(마스터) — search access 하드필터가 `<=`로
# 동작하므로 최고 등급이어야 전체 문서가 보인다. department는 빈 문자열(부서 가중 무영향).
_DEMO_USER = UserContext(
    user_id="demo",
    department="",
    access_level=AccessLevel.마스터,
)


def get_current_user() -> UserContext:
    """데모 모드(ADR-026): 인증 비활성 — 항상 전체접근(마스터) 컨텍스트를 반환한다.

    토큰을 **검증하지 않는다**(검증 후 실패를 무시하는 조용한 실패가 아니라, 아예
    검증을 하지 않는다). Authorization 헤더가 없어도 401을 내지 않는다. 운영 모드
    복원 시 이 함수를 토큰 검증 경로(`auth_service.authenticate`)로 되돌리면 된다 —
    access 하드필터·부서 가중 등 하위 배선은 그대로 두어 복원 비용을 낮춘다.
    """
    return _DEMO_USER


def require_ingest_admin(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """적재 권한을 강제한다 — 마스터 레벨 미만은 403.

    `get_current_user`를 거치므로 미인증은 먼저 401로 막힌다. 권한 부족(인증됐으나
    적재 불가)은 403으로 구분한다.
    """
    if user.access_level < _INGEST_MIN_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="적재 권한이 없습니다",
        )
    return user


def require_master(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """관리자(마스터) 권한을 강제한다 (ADR-017) — 멤버는 403.

    API키·외부소스·멤버 관리 라우트의 게이트. 미인증은 `get_current_user`에서 먼저
    401로 막히고, 인증됐으나 마스터가 아니면(멤버) 403으로 구분한다.
    """
    if not user.access_level.is_master:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="마스터 권한이 필요합니다",
        )
    return user
