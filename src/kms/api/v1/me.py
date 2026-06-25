"""현재 사용자 컨텍스트 라우트 (GET /v1/me) — 프론트 프로필 표시용.

진입점은 얇게: 인증 → `UserContext`를 JSON으로 변환만 한다. 토큰·자격증명은
응답에 싣지 않는다(시크릿 무노출, CONVENTIONS).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from kms.api.deps import get_current_user
from kms.domain.access import AccessLevel
from kms.domain.models import UserContext

router = APIRouter(prefix="/v1", tags=["me"])


class MeResponse(BaseModel):
    """프로필 표시용 사용자 정보 — 토큰/시크릿 없음."""

    user_id: str
    department: str
    access_level: int
    role: str
    is_master: bool


# AccessLevel → 사람이 읽는 역할 라벨 (ADR-017). 변경 시 ADR을 함께 갱신한다.
_ROLE_LABEL: dict[int, str] = {
    int(AccessLevel.멤버): "멤버",
    int(AccessLevel.마스터): "마스터",
}


@router.get("/me", response_model=MeResponse)
def me(user: UserContext = Depends(get_current_user)) -> MeResponse:
    """현재 인증된 사용자의 user_id·부서·접근레벨·역할·마스터 여부를 반환한다."""
    level = int(user.access_level)
    return MeResponse(
        user_id=user.user_id,
        department=user.department,
        access_level=level,
        role=_ROLE_LABEL.get(level, str(level)),
        is_master=user.access_level.is_master,
    )
